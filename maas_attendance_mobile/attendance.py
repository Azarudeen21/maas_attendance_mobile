"""Online, allowlisted test punches. Not a production attendance policy."""

from datetime import datetime, timezone
import json

import frappe

from maas_attendance_mobile.punch_rules import (
    check_fresh, check_transition, parse_request, request_hash, site_time,
)

SETTINGS = "Mobile Attendance Settings"
RECEIPT = "Mobile Attendance Transaction"


def settings():
    # No cached settings: an administrator can disable this test gate immediately.
    return frappe.db.get_singles_dict(SETTINGS)


def enabled_for(employee, config):
    return str(config.get("enable_test_attendance", 0)) == "1" and config.get("test_employee") == employee


def require_enabled(employee, config):
    if not enabled_for(employee, config):
        frappe.throw("Online test attendance is disabled for this employee.", frappe.PermissionError)


def latest_checkin(employee, *, lock=False):
    return frappe.db.get_value(
        "Employee Checkin", {"employee": employee}, ["name", "log_type", "time"],
        as_dict=True, order_by="time desc, creation desc, name desc", for_update=lock,
    )


def status(employee):
    config = settings()
    latest = latest_checkin(employee["name"])
    from maas_attendance_mobile.offline import enabled
    return {
        "api_version": 1, "test_mode": True,
        "online_test_submission_enabled": enabled_for(employee["name"], config),
        "last_checkin": dict(latest) if latest else None,
        "next_log_type": "OUT" if latest and latest["log_type"] == "IN" else
                         "IN" if not latest or latest["log_type"] == "OUT" else None,
        "skip_auto_attendance": True,
        "location_required": str(config.get("require_test_location", 0)) == "1",
        "approved_device_required": str(config.get("require_approved_device", 0)) == "1",
        "offline_test_submission_enabled": enabled(employee['name'], config),
    }


def _reply(receipt, duplicate):
    if not frappe.db.exists("Employee Checkin", receipt["checkin"]):
        frappe.throw("The original checkin is missing. Ask HR to review the audit receipt.", frappe.ValidationError)
    return {
        "api_version": 1, "request_id": receipt["name"], "checkin": receipt["checkin"],
        "employee": receipt["employee"], "log_type": receipt["log_type"],
        "time": receipt["event_time"], "timestamp_utc": receipt["timestamp_utc"],
        "duplicate": duplicate, "test_mode": True, "skip_auto_attendance": True,
    }


def submit(employee, request_id, log_type, timestamp, device_id, app_version, location=None, device_secret=None):
    try:
        payload, event = parse_request(request_id, log_type, timestamp, device_id, app_version)
        if location is not None:
            from maas_attendance_mobile.geofence import parse_location
            location, sampled_at = parse_location(location)
            payload["location"] = location
    except (ValueError, OverflowError):
        frappe.throw("Invalid punch request. Use UUID v4 IDs, IN/OUT and an ISO timestamp with offset.", frappe.ValidationError)

    user = frappe.session.user
    # Serializes mobile requests for this employee until Frappe commits/rolls back
    # the POST transaction. Revalidate identity under lock after any wait.
    current = frappe.db.get_value(
        "Employee", employee["name"], ["user_id", "status"], as_dict=True, for_update=True,
    )
    if not current or current["user_id"] != user or current["status"] != "Active":
        frappe.throw("An active linked Employee record is required.", frappe.PermissionError)
    if not frappe.db.get_value("User", user, "enabled", for_update=True) or "Employee" not in frappe.get_roles(user):
        frappe.throw("This login is not eligible for attendance.", frappe.PermissionError)
    config = settings()
    require_enabled(employee["name"], config)
    registered_device = None
    if str(config.get("require_approved_device", 0)) == "1":
        from maas_attendance_mobile.devices import require_approved
        registered_device = require_approved(employee, device_id, device_secret)
    fingerprint = request_hash(payload, employee["name"], user)
    receipt = frappe.db.get_value(RECEIPT, request_id, [
        "name", "employee", "request_hash", "checkin", "log_type", "event_time", "timestamp_utc",
    ], as_dict=True, for_update=True)
    if receipt:
        if receipt["employee"] != employee["name"] or receipt["request_hash"] != fingerprint:
            frappe.throw("This request ID was already used for a different punch.", frappe.ValidationError)
        # A lost-response retry works even after the online freshness window.
        return _reply(receipt, True)

    if frappe.db.exists('Mobile Attendance Offline Claim', request_id):
        frappe.throw('This request ID already belongs to an offline claim.', frappe.ValidationError)

    gps = None
    if str(config.get("require_test_location", 0)) == "1":
        if location is None:
            frappe.throw("A fresh GPS location is required for this punch.", frappe.ValidationError)
        from maas_attendance_mobile.geofence import evaluate
        # Server derives the company under the employee lock; client cannot select it.
        company = frappe.db.get_value("Employee", employee["name"], "company")
        if not company:
            frappe.throw("An employee company is required for GPS checks.", frappe.ValidationError)
        workplaces = frappe.get_all("Mobile Attendance Workplace", filters={"company": company, "enabled": 1},
            fields=["name", "latitude", "longitude", "radius_m", "max_accuracy_m"])
        try:
            gps = evaluate(location, sampled_at, workplaces, datetime.now(timezone.utc), event)
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)

    from frappe.utils import get_system_timezone
    local_time = site_time(event, get_system_timezone())
    try:
        check_fresh(event, datetime.now(timezone.utc))
        check_transition(latest_checkin(employee["name"], lock=True), log_type, local_time)
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)

    context = {"employee": employee["name"], "user": user, "request_id": request_id}
    previous = frappe.flags.get("maas_test_punch")
    frappe.flags.maas_test_punch = context
    try:
        checkin = frappe.get_doc({
            "doctype": "Employee Checkin", "employee": employee["name"],
            "time": local_time, "log_type": log_type,
            "device_id": "MAAS-TEST:" + device_id, "skip_auto_attendance": 1,
            **({"latitude": location["latitude"], "longitude": location["longitude"]} if gps else {}),
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            "doctype": RECEIPT, "request_id": request_id, "employee": employee["name"],
            "request_hash": fingerprint, "checkin": checkin.name, "log_type": log_type,
            "event_time": local_time, "timestamp_utc": payload["timestamp_utc"],
            "device_id": device_id, "app_version": app_version, "test_mode": 1,
            "location_json": json.dumps(location, sort_keys=True) if location else None,
            "workplace": gps["workplace"] if gps else None,
            "distance_m": gps["distance_m"] if gps else None,
            "accuracy_m": gps["accuracy_m"] if gps else None,
            "registered_device": registered_device,
        }).insert(ignore_permissions=True)
    finally:
        frappe.flags.maas_test_punch = previous
    # No commits here: checkin + UUID-primary-key receipt succeed or roll back
    # together under Frappe's POST transaction, including a UUID collision.
    return _reply(receipt.as_dict(), False)


def guard_checkin(doc, method=None):
    """Prevent the configured normal test login bypassing this API via ORM routes."""
    config = settings()
    test_employee = config.get("test_employee")
    test_user = frappe.db.get_value("Employee", test_employee, "user_id") if test_employee else None
    if not test_user or frappe.session.user != test_user:
        return
    context = frappe.flags.get("maas_test_punch")
    if not context or context.get("user") != test_user or context.get("employee") != doc.employee or not doc.is_new():
        frappe.throw("Use the MAAS test punch API for this account.", frappe.PermissionError)
    require_enabled(doc.employee, config)
    doc.skip_auto_attendance = 1


def guard_delete(doc, method=None):
    config = settings()
    employee = config.get("test_employee")
    if employee and frappe.db.get_value("Employee", employee, "user_id") == frappe.session.user:
        frappe.throw("Ask HR to review attendance corrections.", frappe.PermissionError)
