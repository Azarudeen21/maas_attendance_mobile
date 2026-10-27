"""Employee-scoped installation credentials; not hardware attestation."""
import hashlib
import hmac
import re

import frappe

from maas_attendance_mobile.punch_rules import uuid4_string

DEVICE = "Mobile Attendance Device"


def credential(device_id, secret, employee):
    uuid4_string(device_id)
    if not isinstance(secret, str) or not re.fullmatch(r"[0-9a-f]{64}", secret):
        raise ValueError("A device credential is required.")
    name = hashlib.sha256((employee + ":" + device_id).encode()).hexdigest()
    return name, hashlib.sha256(secret.encode()).hexdigest()


def record(employee, device_id, device_secret, *, lock=False):
    try:
        name, digest = credential(device_id, device_secret, employee["name"])
    except ValueError:
        frappe.throw("Invalid device credentials.", frappe.PermissionError)
    row = frappe.db.get_value(DEVICE, name,
        ["name", "employee", "user", "device_id", "secret_hash", "status", "device_label"],
        as_dict=True, for_update=lock)
    if not row or row["employee"] != employee["name"] or row["user"] != frappe.session.user or not isinstance(row["secret_hash"], str) or not hmac.compare_digest(row["secret_hash"], digest):
        frappe.throw("This device is not registered for your account.", frappe.PermissionError)
    return row


def status(employee, device_id, device_secret):
    """A phone never registered for this employee is a normal state, not an error."""
    try:
        name, _ = credential(device_id, device_secret, employee["name"])
    except ValueError:
        frappe.throw("Invalid device credentials.", frappe.PermissionError)
    if not frappe.db.exists(DEVICE, name):
        return {"api_version": 1, "device_id": device_id, "status": "Not registered", "device_label": None}
    return reply(record(employee, device_id, device_secret))


def reply(row):
    return {"api_version": 1, "device_id": row["device_id"], "status": row["status"], "device_label": row["device_label"]}


def register(employee, device_id, device_secret, device_label):
    try:
        name, digest = credential(device_id, device_secret, employee["name"])
    except ValueError:
        frappe.throw("Invalid device credentials.", frappe.ValidationError)
    if not isinstance(device_label, str) or not 1 <= len(device_label.strip()) <= 60 or any(ord(c) < 32 for c in device_label):
        frappe.throw("Use a device label of 1 to 60 characters.", frappe.ValidationError)
    # Same employee lock as punching serializes registration limits and identity.
    current = frappe.db.get_value("Employee", employee["name"], ["user_id", "status"], as_dict=True, for_update=True)
    if not current or current["user_id"] != frappe.session.user or current["status"] != "Active":
        frappe.throw("An active linked employee is required.", frappe.PermissionError)
    if not frappe.db.get_value("User", frappe.session.user, "enabled", for_update=True) or "Employee" not in frappe.get_roles(frappe.session.user):
        frappe.throw("This account is not eligible for device registration.", frappe.PermissionError)
    existing = frappe.db.get_value(DEVICE, name, "name", for_update=True)
    if existing:
        # Lost-response retry can recover the same registration, never rotate it.
        return reply(record(employee, device_id, device_secret, lock=True))
    rows = frappe.get_all(DEVICE, filters={"employee": employee["name"], "status": ["!=", "Revoked"]}, fields=["name"], limit_page_length=5)
    if len(rows) >= 5:
        frappe.throw("Device limit reached. Ask HR to revoke an unused device.", frappe.ValidationError)
    previous = frappe.flags.get("maas_device_registration")
    frappe.flags.maas_device_registration = name
    try:
        doc = frappe.get_doc({"doctype": DEVICE, "registration_id": name,
            "employee": employee["name"], "user": frappe.session.user,
            "device_id": device_id, "secret_hash": digest,
            "device_label": device_label.strip(), "status": "Pending"}).insert(ignore_permissions=True)
    finally:
        frappe.flags.maas_device_registration = previous
    return reply(doc.as_dict())


def require_approved(employee, device_id, device_secret):
    row = record(employee, device_id, device_secret, lock=True)
    if row["status"] != "Approved":
        frappe.throw("This device is pending approval or revoked. Contact HR.", frappe.PermissionError)
    return row["name"]
