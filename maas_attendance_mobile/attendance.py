"""Online test or enrolled production punches with explicit mode binding."""

from datetime import datetime, timezone
import json

import frappe

from maas_attendance_mobile.punch_rules import (
    check_fresh, check_transition, parse_request, request_hash, site_time,
)

SETTINGS = "Mobile Attendance Settings"
RECEIPT = "Mobile Attendance Transaction"


def settings(employee=None):
    # No cached settings: an administrator can disable this test gate immediately.
    site = frappe.db.get_singles_dict(SETTINGS)
    if employee is None:
        return site
    from maas_attendance_mobile.company_policy import effective
    from maas_attendance_mobile import employee_rules
    return employee_rules.apply(employee, effective(employee, site))


def enabled_for(employee, config):
    return str(config.get("enable_test_attendance", 0)) == "1" and config.get("test_employee") == employee


def online_enabled(employee, config):
    """Policy eligibility plus the employee's Online Check in box."""
    return enabled_for(employee, config) and bool(config.get("online_allowed"))


def require_enabled(employee, config):
    if not enabled_for(employee, config):
        frappe.throw("Attendance is disabled for this employee.", frappe.PermissionError)


def latest_checkin(employee, *, lock=False):
    return frappe.db.get_value(
        "Employee Checkin", {"employee": employee}, ["name", "log_type", "time"],
        as_dict=True, order_by="time desc, creation desc, name desc", for_update=lock,
    )


def status(employee):
    config = settings(employee["name"])
    latest = latest_checkin(employee["name"])
    from maas_attendance_mobile.offline import enabled, automatic_enabled
    from maas_attendance_mobile.supervisor import available as supervisor_available
    return {
        "api_version": 1, "test_mode": not bool(config.get('production_mode')),
        "attendance_mode": 'production' if config.get('production_mode') else 'test',
        "online_submission_enabled": online_enabled(employee["name"], config),
        "online_test_submission_enabled": online_enabled(employee["name"], config) and not config.get('production_mode', False),
        "last_checkin": dict(latest) if latest else None,
        "next_log_type": "OUT" if latest and latest["log_type"] == "IN" else
                         "IN" if not latest or latest["log_type"] == "OUT" else None,
        "skip_auto_attendance": not bool(config.get('production_mode')),
        "location_required": str(config.get("require_test_location", 0)) == "1",
        "approved_device_required": str(config.get("require_approved_device", 0)) == "1",
        "offline_test_submission_enabled": enabled(employee['name'], config) and not config.get('production_mode', False),
        "offline_submission_enabled": enabled(employee['name'], config),
        "offline_auto_accept_enabled": enabled(employee['name'], config) and automatic_enabled(config),
        "photo_required": str(config.get('require_test_photo', 0)) == '1',
        "photo_optional": bool(config.get('photo_optional')),
        "standard_checkin": bool(config.get('standard_checkin')),
        "supervisor_checkin_enabled": supervisor_available(),
    }


def _reply(receipt, duplicate):
    if not frappe.db.exists("Employee Checkin", receipt["checkin"]):
        frappe.throw("The original checkin is missing. Ask HR to review the audit receipt.", frappe.ValidationError)
    return {
        "api_version": 1, "request_id": receipt["name"], "checkin": receipt["checkin"],
        "employee": receipt["employee"], "log_type": receipt["log_type"],
        "time": receipt["event_time"], "timestamp_utc": receipt["timestamp_utc"],
        "duplicate": duplicate, "test_mode": receipt.get('test_mode', 1) in (1, '1', True),
        "skip_auto_attendance": receipt.get('test_mode', 1) in (1, '1', True),
    }


def submit(employee, request_id, log_type, timestamp, device_id, app_version, location=None, device_secret=None, photo=None, attendance_mode='test'):
    photo_bytes = photo_at = None
    try:
        payload, event = parse_request(request_id, log_type, timestamp, device_id, app_version)
        if location is not None:
            from maas_attendance_mobile.geofence import parse_location
            location, sampled_at = parse_location(location)
            payload["location"] = location
        if photo is not None:
            from maas_attendance_mobile.photos import parse_photo
            payload['photo'], photo_bytes, photo_at = parse_photo(photo)
    except (ValueError, OverflowError, OSError, TypeError):
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
    config = settings(employee["name"])
    require_enabled(employee["name"], config)
    if not config.get("online_allowed"):
        frappe.throw("Online check-in is not enabled for you. Use offline attendance or contact HR.", frappe.PermissionError)
    from maas_attendance_mobile.production_policy import require_mode
    require_mode(config, attendance_mode)
    production = bool(config.get('production_mode'))
    if production:
        payload['attendance_mode'] = 'production'
    registered_device = None
    if str(config.get("require_approved_device", 0)) == "1":
        from maas_attendance_mobile.devices import require_approved
        registered_device = require_approved(employee, device_id, device_secret)
    fingerprint = request_hash(payload, employee["name"], user)
    receipt = frappe.db.get_value(RECEIPT, request_id, [
        "name", "employee", "request_hash", "checkin", "log_type", "event_time", "timestamp_utc", "test_mode",
    ], as_dict=True, for_update=True)
    if receipt:
        if receipt["employee"] != employee["name"] or receipt["request_hash"] != fingerprint:
            frappe.throw("This request ID was already used for a different punch.", frappe.ValidationError)
        # A lost-response retry works even after the online freshness window.
        return _reply(receipt, True)

    if frappe.db.exists('Mobile Attendance Offline Claim', request_id):
        frappe.throw('This request ID already belongs to an offline claim.', frappe.ValidationError)

    if str(config.get('require_test_photo', 0)) == '1' and photo is None:
        frappe.throw('A punch photo is required.', frappe.ValidationError)
    if photo is not None:
        from maas_attendance_mobile.photos import check_timing
        try: check_timing(event, photo_at, location, sampled_at if location else None, datetime.now(timezone.utc))
        except ValueError as error: frappe.throw(str(error), frappe.ValidationError)

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
            gps = evaluate(location, sampled_at, workplaces, sampled_at if photo_bytes else datetime.now(timezone.utc), photo_at or event)
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)

    from frappe.utils import get_system_timezone
    local_time = site_time(event, get_system_timezone())
    try:
        check_fresh(event, datetime.now(timezone.utc))
        check_transition(latest_checkin(employee["name"], lock=True), log_type, local_time)
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)

    context = {"employee": employee["name"], "user": user, "request_id": request_id, 'production': production}
    previous = frappe.flags.get("maas_test_punch")
    frappe.flags.maas_test_punch = context
    try:
        checkin = frappe.get_doc({
            "doctype": "Employee Checkin", "employee": employee["name"],
            "time": local_time, "log_type": log_type,
            "device_id": ('MAAS:' if production else 'MAAS-TEST:') + device_id,
            "skip_auto_attendance": int(not production),
            **({"latitude": location["latitude"], "longitude": location["longitude"]} if gps or photo_bytes else {}),
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            "doctype": RECEIPT, "request_id": request_id, "employee": employee["name"],
            "request_hash": fingerprint, "checkin": checkin.name, "log_type": log_type,
            "event_time": local_time, "timestamp_utc": payload["timestamp_utc"],
            "device_id": device_id, "app_version": app_version, "test_mode": int(not production),
            "location_json": json.dumps(location, sort_keys=True) if location else None,
            "workplace": gps["workplace"] if gps else None,
            "distance_m": gps["distance_m"] if gps else None,
            "accuracy_m": gps["accuracy_m"] if gps else None,
            "registered_device": registered_device,
            "photo_sha256": payload.get('photo', {}).get('sha256'),
            "photo_captured_at": payload.get('photo', {}).get('captured_at'),
        }).insert(ignore_permissions=True)
        if photo_bytes:
            from maas_attendance_mobile.photos import save_private
            save_private(RECEIPT, receipt.name, photo_bytes)
    finally:
        frappe.flags.maas_test_punch = previous
    # No commits here: checkin + UUID-primary-key receipt succeed or roll back
    # together under Frappe's POST transaction, including a UUID collision.
    return _reply(receipt.as_dict(), False)


def guard_checkin(doc, method=None):
    """Prevent the configured normal test login bypassing this API via ORM routes."""
    from maas_attendance_mobile.company_policy import test_login
    site = settings()
    test_user = frappe.session.user
    from maas_attendance_mobile.production_policy import enrolled_login
    protected = test_login(site, test_user) or enrolled_login(test_user)
    if not protected:
        return
    context = frappe.flags.get("maas_test_punch")
    if not context and doc.is_new():
        from maas_attendance_mobile.employee_rules import allows_standard_checkin
        if allows_standard_checkin(doc.employee, test_user):
            return  # "Check in Without Photo" employees use normal HRMS check-in.
    if not context or context.get("user") != test_user or context.get("employee") != doc.employee or not doc.is_new():
        frappe.throw("Use the MAAS test punch API for this account.", frappe.PermissionError)
    require_enabled(doc.employee, settings(doc.employee))
    production = bool(settings(doc.employee).get('production_mode'))
    if bool(context.get('production')) != production:
        frappe.throw('The attendance policy changed.', frappe.PermissionError)
    doc.skip_auto_attendance = int(not production)


def guard_delete(doc, method=None):
    from maas_attendance_mobile.company_policy import test_login
    from maas_attendance_mobile.production_policy import enrolled_login
    if test_login(settings(), frappe.session.user) or enrolled_login(frappe.session.user):
        frappe.throw("Ask HR to review attendance corrections.", frappe.PermissionError)
