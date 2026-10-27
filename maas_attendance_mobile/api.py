"""Whitelisted API for the Frappe HR mobile app and the HR desk tools."""

import frappe


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
def attendance_status():
    from maas_attendance_mobile.attendance import status
    return status(_linked_employee())


@frappe.whitelist(methods=["POST"])
def punch(request_id, log_type, timestamp, device_id, app_version, location=None, device_secret=None, photo=None, attendance_mode=None):
    """No employee or skip-attendance input is accepted; attendance_mode is ignored (app 1.3.x)."""
    from maas_attendance_mobile.attendance import submit
    return submit(_linked_employee(), request_id, log_type, timestamp, device_id, app_version, location=location, device_secret=device_secret, photo=photo, attendance_mode=attendance_mode)


@frappe.whitelist(methods=["POST"])
def register_device(device_id, device_secret, device_label):
    from maas_attendance_mobile.devices import register
    return register(_linked_employee(), device_id, device_secret, device_label)


@frappe.whitelist(methods=["POST"])
def device_status(device_id, device_secret):
    from maas_attendance_mobile.devices import status
    return status(_linked_employee(), device_id, device_secret)


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
def photo_retention_preview(company):
    from maas_attendance_mobile.photo_retention import preview
    return preview(company)


@frappe.whitelist(methods=['POST'])
def photo_retention_hold(doctype, name, hold, reason):
    from maas_attendance_mobile.photo_retention import set_hold
    return set_hold(doctype, name, hold, reason)


@frappe.whitelist(methods=['POST'])
def face_test_match(image_base64, company=None):
    from maas_attendance_mobile.faces import test_match
    return test_match(image_base64, company)


@frappe.whitelist(methods=['POST'])
def face_enroll_employee_photos(company, consent_confirmed):
    from maas_attendance_mobile.faces import enroll_from_employee_photos
    return enroll_from_employee_photos(company, consent_confirmed)



@frappe.whitelist(methods=['POST'])
def supervisor_identify(photo, device_id, device_secret):
    from maas_attendance_mobile.supervisor import identify
    return identify(_linked_employee(), photo, device_id, device_secret)


@frappe.whitelist(methods=['POST'])
def supervisor_punch(employee, request_id, log_type, timestamp, device_id, app_version, location, device_secret, photo):
    """The server identifies the face again; employee is only the supervisor's confirmation."""
    from maas_attendance_mobile.supervisor import punch
    return punch(_linked_employee(), employee, request_id, log_type, timestamp, device_id, app_version, location, device_secret, photo)


@frappe.whitelist(methods=["POST"])
def supervisor_offline_punch(request, device_secret):
    """Upload a scan saved without connection; the server identifies the employee."""
    from maas_attendance_mobile.supervisor import offline_punch
    return offline_punch(_linked_employee(), request, device_secret)


@frappe.whitelist(methods=['POST'])
def face_enroll_employee(employee, consent_confirmed):
    from maas_attendance_mobile.faces import enroll_employee
    return enroll_employee(employee, consent_confirmed)
