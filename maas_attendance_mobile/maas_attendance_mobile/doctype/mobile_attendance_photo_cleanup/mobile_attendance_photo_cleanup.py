import frappe
from frappe.model.document import Document


class MobileAttendancePhotoCleanup(Document):
    def validate(self):
        if not self.is_new() or frappe.flags.get('maas_photo_cleanup_write') != self.name:
            frappe.throw('Photo cleanup audits are managed by the retention service.', frappe.PermissionError)
        if self.status != 'Pending':
            frappe.throw('New photo cleanup audits must be Pending.', frappe.ValidationError)

    def on_trash(self):
        frappe.throw('Photo cleanup audits cannot be deleted.', frappe.PermissionError)
