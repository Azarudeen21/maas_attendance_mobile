"""Online mobile punches, attendance status and the Employee Checkin guard."""

from datetime import datetime, timezone
import json

import frappe

from maas_attendance_mobile import policy
from maas_attendance_mobile.punch_rules import (
    check_fresh, check_transition, parse_request, request_hash, site_time,
)

RECEIPT = "Mobile Attendance Transaction"
# Set while a MAAS service inserts an Employee Checkin for the signed-in employee.
CONTEXT = "maas_punch"


def latest_checkin(employee, *, lock=False):
    return frappe.db.get_value(
        "Employee Checkin", {"employee": employee}, ["name", "log_type", "time"],
        as_dict=True, order_by="time desc, creation desc, name desc", for_update=lock,
    )


def status(employee):
    rules = policy.resolve(employee["name"])
    latest = latest_checkin(employee["name"])
    from maas_attendance_mobile.supervisor import available as supervisor_available
    return {
        "api_version": 1,
        # Kept for app 1.3.x, which sends it back with each punch; always production.
        "attendance_mode": "production",
        "online_submission_enabled": rules["online"],
        "offline_submission_enabled": rules["offline"],
        "last_checkin": dict(latest) if latest else None,
        "next_log_type": "OUT" if latest and latest["log_type"] == "IN" else
                         "IN" if not latest or latest["log_type"] == "OUT" else None,
        "location_required": rules["require_location"],
        "approved_device_required": rules["require_device"],
        "photo_required": rules["require_photo"],
        "photo_optional": rules["photo_optional"],
        "standard_checkin": rules["standard_checkin"],
        "supervisor_checkin_enabled": supervisor_available(),
    }


def _reply(receipt, duplicate):
    if not frappe.db.exists("Employee Checkin", receipt["checkin"]):
        frappe.throw("The original checkin is missing. Ask HR to review the audit receipt.", frappe.ValidationError)
    return {
        "api_version": 1, "request_id": receipt["name"], "checkin": receipt["checkin"],
        "employee": receipt["employee"], "log_type": receipt["log_type"],
        "time": receipt["event_time"], "timestamp_utc": receipt["timestamp_utc"],
        "duplicate": duplicate,
    }


def submit(employee, request_id, log_type, timestamp, device_id, app_version, location=None, device_secret=None, photo=None, attendance_mode=None):
    photo_bytes = photo_at = None
    try:
        payload, event = parse_request(request_id, log_type, timestamp, device_id, app_version)
        # App 1.3.x sends the mode it was given; keep it in the hash so its retries match.
        if attendance_mode == "production":
            payload["attendance_mode"] = "production"
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
        "Employee", employee["name"], ["user_id", "status", "company"], as_dict=True, for_update=True,
    )
    if not current or current["user_id"] != user or current["status"] != "Active":
        frappe.throw("An active linked Employee record is required.", frappe.PermissionError)
    if not frappe.db.get_value("User", user, "enabled", for_update=True) or "Employee" not in frappe.get_roles(user):
        frappe.throw("This login is not eligible for attendance.", frappe.PermissionError)
    rules = policy.resolve(employee["name"])
    if not rules["online"]:
        frappe.throw("Online check-in is not enabled for you. Use offline attendance or contact HR." if rules["offline"]
                     else "Attendance is not enabled for your account. Contact HR.", frappe.PermissionError)
    registered_device = None
    if rules["require_device"]:
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

    if rules["require_photo"] and photo is None:
        frappe.throw('A punch photo is required.', frappe.ValidationError)
    if photo is not None:
        from maas_attendance_mobile.photos import check_timing
        try: check_timing(event, photo_at, location, sampled_at if location else None, datetime.now(timezone.utc))
        except ValueError as error: frappe.throw(str(error), frappe.ValidationError)
        from maas_attendance_mobile.faces import FaceMismatch, own_face_problem
        problem = own_face_problem(employee["name"], current["company"], photo_bytes)
        if problem:
            frappe.throw(problem, FaceMismatch)

    gps = None
    if rules["require_location"]:
        if location is None:
            frappe.throw("A fresh GPS location is required for this punch.", frappe.ValidationError)
        from maas_attendance_mobile.geofence import evaluate
        # Server derives the company under the employee lock; client cannot select it.
        if not current["company"]:
            frappe.throw("An employee company is required for GPS checks.", frappe.ValidationError)
        workplaces = frappe.get_all("Mobile Attendance Workplace", filters={"company": current["company"], "enabled": 1},
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

    previous = frappe.flags.get(CONTEXT)
    frappe.flags[CONTEXT] = {"employee": employee["name"], "user": user, "request_id": request_id}
    try:
        checkin = frappe.get_doc({
            "doctype": "Employee Checkin", "employee": employee["name"],
            "time": local_time, "log_type": log_type,
            "device_id": "MAAS:" + device_id, "skip_auto_attendance": 0,
            **({"latitude": location["latitude"], "longitude": location["longitude"]} if gps or photo_bytes else {}),
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            "doctype": RECEIPT, "request_id": request_id, "employee": employee["name"],
            "request_hash": fingerprint, "checkin": checkin.name, "log_type": log_type,
            "event_time": local_time, "timestamp_utc": payload["timestamp_utc"],
            "device_id": device_id, "app_version": app_version,
            "location_json": json.dumps(location, sort_keys=True) if location else None,
            "workplace": gps["workplace"] if gps else None,
            "distance_m": gps["distance_m"] if gps else None,
            "accuracy_m": gps["accuracy_m"] if gps else None,
            "registered_device": registered_device,
            "photo_sha256": payload.get('photo', {}).get('sha256'),
            "photo_captured_at": payload.get('photo', {}).get('captured_at'),
        }).insert(ignore_permissions=True)
        if photo_bytes:
            from maas_attendance_mobile.photos import link_checkin, save_private
            link_checkin(checkin.name, save_private(RECEIPT, receipt.name, photo_bytes))
    finally:
        frappe.flags[CONTEXT] = previous
    # No commits here: checkin + UUID-primary-key receipt succeed or roll back
    # together under Frappe's POST transaction, including a UUID collision.
    return _reply(receipt.as_dict(), False)


def guard_checkin(doc, method=None):
    """App users must check themselves in through the app (or normal HRMS when allowed).

    HR and other users recording check-ins for someone else are not affected.
    """
    user = frappe.session.user
    if not policy.uses_app(user, doc.employee):
        return
    context = frappe.flags.get(CONTEXT)
    if not context and doc.is_new() and policy.allows_standard_checkin(doc.employee, user):
        return  # "Check in Without Photo" employees use normal HRMS check-in.
    if not context or context.get("user") != user or context.get("employee") != doc.employee or not doc.is_new():
        if not policy.resolve(doc.employee)["online"]:
            frappe.throw("Online check-in is not enabled for you. Contact HR.", frappe.PermissionError)
        frappe.throw("Use the mobile app to record your attendance.", frappe.PermissionError)


def guard_delete(doc, method=None):
    if policy.uses_app(frappe.session.user, doc.employee):
        frappe.throw("Ask HR to review attendance corrections.", frappe.PermissionError)
