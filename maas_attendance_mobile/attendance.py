"""Online, allowlisted test punches. Not a production attendance policy."""

from datetime import datetime, timezone

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
    return {
        "api_version": 1, "test_mode": True,
        "online_test_submission_enabled": enabled_for(employee["name"], config),
        "last_checkin": dict(latest) if latest else None,
        "next_log_type": "OUT" if latest and latest["log_type"] == "IN" else
                         "IN" if not latest or latest["log_type"] == "OUT" else None,
        "skip_auto_attendance": True,
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


def submit(employee, request_id, log_type, timestamp, device_id, app_version):
    try:
        payload, event = parse_request(request_id, log_type, timestamp, device_id, app_version)
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
    require_enabled(employee["name"], settings())
    fingerprint = request_hash(payload, employee["name"], user)
    receipt = frappe.db.get_value(RECEIPT, request_id, [
        "name", "employee", "request_hash", "checkin", "log_type", "event_time", "timestamp_utc",
    ], as_dict=True, for_update=True)
    if receipt:
        if receipt["employee"] != employee["name"] or receipt["request_hash"] != fingerprint:
            frappe.throw("This request ID was already used for a different punch.", frappe.ValidationError)
        # A lost-response retry works even after the online freshness window.
        return _reply(receipt, True)

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
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            "doctype": RECEIPT, "request_id": request_id, "employee": employee["name"],
            "request_hash": fingerprint, "checkin": checkin.name, "log_type": log_type,
            "event_time": local_time, "timestamp_utc": payload["timestamp_utc"],
            "device_id": device_id, "app_version": app_version, "test_mode": 1,
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
