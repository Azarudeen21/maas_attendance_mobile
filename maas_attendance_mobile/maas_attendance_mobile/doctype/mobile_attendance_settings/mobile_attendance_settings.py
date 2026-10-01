import frappe
from frappe.model.document import Document


class MobileAttendanceSettings(Document):
    def validate(self):
        if not self.enable_test_attendance:
            return
        row = frappe.db.get_value('Employee', self.test_employee, ['status', 'user_id'], as_dict=True) if self.test_employee else None
        if not row or row.status != 'Active' or not row.user_id:
            frappe.throw('Select an active test employee linked to an enabled Employee login.')
        if not frappe.db.get_value('User', row.user_id, 'enabled') or 'Employee' not in frappe.get_roles(row.user_id):
            frappe.throw('The test login must be enabled and have the Employee role.')
        if len(frappe.get_all('Employee', filters={'user_id': row.user_id}, limit_page_length=2)) != 1:
            frappe.throw('The test login must have exactly one linked employee.')
