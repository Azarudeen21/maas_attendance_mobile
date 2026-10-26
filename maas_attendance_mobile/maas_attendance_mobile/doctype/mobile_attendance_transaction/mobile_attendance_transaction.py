import frappe
from frappe.model.document import Document


class MobileAttendanceTransaction(Document):
    def before_insert(self):
        context = frappe.flags.get('maas_punch')
        if not context or context.get('user') != frappe.session.user or context.get('employee') != self.employee or context.get('request_id') != self.request_id:
            frappe.throw('Receipts can only be created by the mobile punch service.', frappe.PermissionError)

    def validate(self):
        if not self.is_new():
            frappe.throw('Mobile audit receipts are immutable.', frappe.PermissionError)

    def on_trash(self):
        frappe.throw('Mobile audit receipts cannot be deleted.', frappe.PermissionError)
