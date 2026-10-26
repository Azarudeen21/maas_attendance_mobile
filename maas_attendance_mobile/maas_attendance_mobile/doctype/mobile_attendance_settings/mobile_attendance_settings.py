import frappe
from frappe.model.document import Document


class MobileAttendanceSettings(Document):
    def validate(self):
        from maas_attendance_mobile.faces import limits
        try:
            limits(self)
        except ValueError as error:
            frappe.throw(str(error))
        if self.get('enable_supervisor_checkin') in (1, '1', True) and self.get('enable_face_recognition') not in (1, '1', True):
            frappe.throw('Enable the face recognition pilot before supervisor face check-in.')
        production = self.get('enable_production_attendance') in (1, '1', True)
        if self.get('enable_production_offline') in (1, '1', True) and not production:
            frappe.throw('Enable production attendance before enabling production offline claims.')
        if production:
            from maas_attendance_mobile.company_policy import validate_workplaces
            for policy in frappe.get_all('Mobile Attendance Company Policy', filters={'enable_production_attendance': 1},
                fields=['company', 'production_require_location', 'production_require_photo']):
                if policy.get('production_require_location') in (1, '1', True):
                    validate_workplaces(policy['company'])
                if policy.get('production_require_photo') in (1, '1', True):
                    from maas_attendance_mobile.photos import require_file_guard
                    require_file_guard()
        if self.get('enable_mobile_site_setup') in (1, '1', True):
            if not self.get('mobile_oauth_client'):
                frappe.throw('Select the public OAuth client for mobile site setup.')
            from maas_attendance_mobile.site_setup import validate_client
            validate_client(self.get('mobile_oauth_client'))
            label = (self.get('mobile_site_label') or '').strip()
            if not label or len(label) > 100:
                frappe.throw('Enter a mobile site label of at most 100 characters.')
            self.set('mobile_site_label', label)
        online = self.get('enable_test_attendance') in (1, '1', True)
        if self.get('enable_offline_test') in (1, '1', True) and not online:
            frappe.throw('Enable the online test policy before enabling offline claims.')
        if not online:
            return
        if self.get('use_company_test_policies') in (1, '1', True):
            from maas_attendance_mobile.company_policy import validate_employee, validate_workplaces
            policies = frappe.get_all('Mobile Attendance Company Policy', filters={'enabled': 1},
                fields=['company', 'test_employee', 'require_test_location'])
            for policy in policies:
                validate_employee(policy['test_employee'], policy['company'])
                if self.get('require_test_location') in (1, '1', True) or str(policy['require_test_location']) == '1':
                    validate_workplaces(policy['company'])
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
