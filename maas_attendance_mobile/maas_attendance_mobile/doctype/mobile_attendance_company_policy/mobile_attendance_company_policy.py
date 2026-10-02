import frappe
from frappe.model.document import Document
from maas_attendance_mobile.company_policy import validate_employee, validate_workplaces


class MobileAttendanceCompanyPolicy(Document):
    def validate(self):
        from maas_attendance_mobile.photo_retention import retention_days
        try:
            self.photo_retention_days = retention_days(self.get('photo_retention_days'))
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)
        if self.get('enable_photo_cleanup') in (1, '1', True) and not self.photo_retention_days:
            frappe.throw('Set a positive retention period before enabling photo cleanup.')
        if self.get('enable_photo_cleanup') in (1, '1', True):
            from maas_attendance_mobile.photos import require_file_guard
            require_file_guard()
        if not self.get('company'):
            frappe.throw('Select a company for this policy.')
        validate_employee(self.get('test_employee'), self.get('company'))
        if self.get('enabled') in (1, '1', True) and self.get('require_test_location') in (1, '1', True):
            validate_workplaces(self.get('company'))
