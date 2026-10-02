"""Authenticated profile, capabilities and allowlisted online test attendance."""

from importlib import import_module

import frappe

from maas_attendance_mobile import __version__


@frappe.whitelist(allow_guest=True, methods=['GET'])
def mobile_setup():
    from maas_attendance_mobile.site_setup import discovery
    return discovery()


def _linked_employee():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw("Sign in to use the mobile API.", frappe.AuthenticationError)
    if not frappe.db.get_value("User", user, "enabled"):
        frappe.throw("This login is disabled.", frappe.PermissionError)
    if "Employee" not in frappe.get_roles(user):
        frappe.throw("The Employee role is required.", frappe.PermissionError)

    # The query uses the SERVER session identity; no employee/user ID is
    # accepted from the request. get_list also enforces normal Frappe permissions.
    rows = frappe.get_list(
        "Employee",
        filters={"user_id": user},
        fields=["name", "employee_name", "company", "status", "user_id"],
        limit_page_length=2,
    )
    if len(rows) != 1:
        frappe.throw(
            "Exactly one Employee record must be linked to this login. Contact HR.",
            frappe.PermissionError,
        )
    employee = rows[0]
    if employee["user_id"] != user or employee["status"] != "Active":
        frappe.throw("An active linked Employee record is required.", frappe.PermissionError)
    return employee


@frappe.whitelist(methods=["GET"])
def profile():
    """Return only the server-authenticated employee's basic profile."""
    employee = _linked_employee()
    return {
        "api_version": 1,
        "user": frappe.session.user,
        "employee": dict(employee),
    }


@frappe.whitelist(methods=["GET"])
def capabilities():
    """Verify installed HR schema before mobile attendance writes are added."""
    employee = _linked_employee()
    installed = frappe.get_installed_apps()
    if "erpnext" not in installed or "hrms" not in installed:
        frappe.throw("ERPNext and HRMS must be installed.", frappe.ValidationError)

    versions = {
        app: getattr(import_module(app), "__version__", "unknown")
        for app in ("frappe", "erpnext", "hrms")
    }
    for version in versions.values():
        if version.split(".")[0] not in ("15", "16"):
            frappe.throw("This integration supports verified v15/v16 schemas only.", frappe.ValidationError)

    meta = frappe.get_meta("Employee Checkin")
    required = ["employee", "time", "log_type"]
    if any(not meta.has_field(field) for field in required):
        frappe.throw("Employee Checkin is missing a required field.", frappe.ValidationError)
    candidates = required + [
        "device_id", "skip_auto_attendance", "latitude", "longitude", "geolocation"
    ]
    from maas_attendance_mobile.attendance import enabled_for, settings
    config = settings(employee['name'])
    return {
        "api_version": 1,
        "app_version": __version__,
        "versions": versions,
        "employee_checkin_fields": [field for field in candidates if meta.has_field(field)],
        "attendance_submission_enabled": bool(config.get('production_mode')) and enabled_for(employee['name'], config),
        "online_test_submission_enabled": not config.get('production_mode', False) and enabled_for(employee['name'], config),
        "test_mode": not bool(config.get('production_mode')),
        "offline_submission_enabled": bool(config.get('production_mode')) and enabled_for(employee['name'], config) and config.get('enable_offline_test') in (1, '1', True),
        "offline_test_claims_supported": True,
        "face_verification_enabled": False,
        "punch_photo_supported": True,
        "device_registration_enabled": True,
    }


@frappe.whitelist(methods=["GET"])
def attendance_status():
    from maas_attendance_mobile.attendance import status
    return status(_linked_employee())


@frappe.whitelist(methods=["POST"])
def punch(request_id, log_type, timestamp, device_id, app_version, location=None, device_secret=None, photo=None, attendance_mode='test'):
    """No employee, skip-attendance or face-verification input is accepted."""
    from maas_attendance_mobile.attendance import submit
    return submit(_linked_employee(), request_id, log_type, timestamp, device_id, app_version, location=location, device_secret=device_secret, photo=photo, attendance_mode=attendance_mode)


@frappe.whitelist(methods=["POST"])
def register_device(device_id, device_secret, device_label):
    from maas_attendance_mobile.devices import register
    return register(_linked_employee(), device_id, device_secret, device_label)


@frappe.whitelist(methods=["POST"])
def device_status(device_id, device_secret):
    from maas_attendance_mobile.devices import record, reply
    return reply(record(_linked_employee(), device_id, device_secret))


@frappe.whitelist(methods=["GET"])
def attendance_history(cursor=None):
    from maas_attendance_mobile.history import page
    return page(_linked_employee(), cursor)


@frappe.whitelist(methods=['POST'])
def submit_offline_claim(request, device_secret):
    from maas_attendance_mobile.offline import submit
    return submit(_linked_employee(), request, device_secret)


@frappe.whitelist(methods=['GET'])
def offline_claim_status(request_id):
    from maas_attendance_mobile.offline import own_status
    return own_status(_linked_employee(), request_id)


@frappe.whitelist(methods=['POST'])
def review_offline_claim(request_id, decision, reason):
    from maas_attendance_mobile.offline import review
    return review(request_id, decision, reason)


@frappe.whitelist(methods=['GET'])
def hr_overview():
    from maas_attendance_mobile.hr_controls import overview
    return overview()


@frappe.whitelist(methods=['GET'])
def photo_retention_preview(company):
    from maas_attendance_mobile.photo_retention import preview
    return preview(company)


@frappe.whitelist(methods=['POST'])
def photo_retention_hold(doctype, name, hold, reason):
    from maas_attendance_mobile.photo_retention import set_hold
    return set_hold(doctype, name, hold, reason)
