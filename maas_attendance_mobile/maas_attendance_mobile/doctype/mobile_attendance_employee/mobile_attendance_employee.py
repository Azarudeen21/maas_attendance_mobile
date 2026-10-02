import frappe
from frappe.model.document import Document

from maas_attendance_mobile.company_policy import validate_employee, test_login


class MobileAttendanceEmployee(Document):
    def validate(self):
        old = self.get_doc_before_save()
        if old and (old.employee != self.employee or old.company != self.company):
            frappe.throw('Enrollment employee and company cannot be changed. Disable the old enrollment and arrange a reviewed migration.')
        if self.get('enabled') not in (1, '1', True):
            return
        row = validate_employee(self.employee, self.company)
        site = frappe.db.get_singles_dict('Mobile Attendance Settings')
        if test_login({**site, 'use_company_test_policies': 1}, row['user_id']):
            frappe.throw('A dedicated test account cannot be enrolled for production attendance.')

    def on_trash(self):
        frappe.throw('Disable enrollment instead of deleting its audit history.', frappe.PermissionError)
