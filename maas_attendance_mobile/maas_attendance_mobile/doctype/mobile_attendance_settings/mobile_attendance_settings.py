import frappe
from frappe.model.document import Document


class MobileAttendanceSettings(Document):
    def validate(self):
        online = self.get('enable_test_attendance') in (1, '1', True)
        if self.get('enable_offline_test') in (1, '1', True) and not online:
            frappe.throw('Enable the online test policy before enabling offline claims.')
        if not online:
            return
        row = frappe.db.get_value('Employee', self.test_employee, ['status', 'user_id'], as_dict=True) if self.test_employee else None
        if not row or row.status != 'Active' or not row.user_id:
            frappe.throw('Select an active test employee linked to an enabled Employee login.')
        if not frappe.db.get_value('User', row.user_id, 'enabled') or 'Employee' not in frappe.get_roles(row.user_id):
            frappe.throw('The test login must be enabled and have the Employee role.')
        if len(frappe.get_all('Employee', filters={'user_id': row.user_id}, limit_page_length=2)) != 1:
            frappe.throw('The test login must have exactly one linked employee.')
        if self.get('require_test_location') in (1, '1', True):
            company = frappe.db.get_value('Employee', self.test_employee, 'company')
            workplaces = frappe.get_all('Mobile Attendance Workplace', filters={'company': company, 'enabled': 1},
                fields=['latitude', 'longitude', 'radius_m', 'max_accuracy_m']) if company else []
            if not workplaces:
                frappe.throw('Configure an enabled workplace for the test employee company before requiring GPS workplace checks.')
            from maas_attendance_mobile.geofence import workplace_values
            for workplace in workplaces:
                try:
                    values = workplace_values(workplace)
                    if values['max_accuracy_m'] >= values['radius_m']:
                        raise ValueError('GPS uncertainty must be smaller than the workplace radius.')
                except ValueError as error:
                    frappe.throw(str(error), frappe.ValidationError)
