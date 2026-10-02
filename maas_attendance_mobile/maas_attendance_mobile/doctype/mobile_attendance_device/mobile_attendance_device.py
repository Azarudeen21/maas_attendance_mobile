import frappe
from frappe.model.document import Document


class MobileAttendanceDevice(Document):
    def before_insert(self):
        if frappe.flags.get("maas_device_registration") != self.registration_id:
            frappe.throw("Register devices through the mobile API.", frappe.PermissionError)
        self.status = "Pending"

    def validate(self):
        if self.is_new():
            self.before_insert()
            return
        before = self.get_doc_before_save()
        if not before:
            frappe.throw("Device audit state is unavailable.")
        for key in ("registration_id", "employee", "user", "device_id", "secret_hash", "device_label"):
            if self.get(key) != before.get(key):
                frappe.throw("Device identity and credentials cannot be edited.", frappe.PermissionError)
        for key in ("approved_by", "approved_at", "revoked_by", "revoked_at"):
            self.set(key, before.get(key))
        if self.status == before.status:
            return
        if not set(frappe.get_roles()).intersection({"HR Manager", "System Manager"}) or self.user == frappe.session.user:
            frappe.throw("Another HR Manager or System Manager must review this device.", frappe.PermissionError)
        transitions = {"Pending": {"Approved", "Revoked"}, "Approved": {"Revoked"}, "Revoked": set()}
        if self.status not in transitions.get(before.status, set()):
            frappe.throw("A revoked device cannot be reactivated. Register a new installation.")
        from frappe.utils import now_datetime
        prefix = "approved" if self.status == "Approved" else "revoked"
        self.set(prefix + "_by", frappe.session.user)
        self.set(prefix + "_at", now_datetime())

    def on_trash(self):
        frappe.throw("Revoke devices instead of deleting their audit records.", frappe.PermissionError)
