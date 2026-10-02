import frappe
from frappe.model.document import Document
from maas_attendance_mobile.company_policy import validate_employee, validate_workplaces


class MobileAttendanceCompanyPolicy(Document):
    def validate(self):
        if not self.get('company'):
            frappe.throw('Select a company for this policy.')
        validate_employee(self.get('test_employee'), self.get('company'))
        if self.get('enabled') in (1, '1', True) and self.get('require_test_location') in (1, '1', True):
            validate_workplaces(self.get('company'))
