import frappe
from frappe.model.document import Document


class MobileAttendanceCompanyPolicy(Document):
    def validate(self):
        from maas_attendance_mobile.photo_retention import retention_days
        from maas_attendance_mobile.photos import require_file_guard
        if not self.get('company'):
            frappe.throw('Select a company for this policy.')
        try:
            self.photo_retention_days = retention_days(self.get('photo_retention_days'))
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)
        if self.get('enable_photo_cleanup') in (1, '1', True):
            if not self.photo_retention_days:
                frappe.throw('Set a positive retention period before enabling photo cleanup.')
            require_file_guard()
        if self.get('enabled') not in (1, '1', True):
            return
        if self.get('require_location') in (1, '1', True):
            from maas_attendance_mobile.policy import validate_workplaces
            validate_workplaces(self.get('company'))
        if self.get('require_photo') in (1, '1', True):
            require_file_guard()
