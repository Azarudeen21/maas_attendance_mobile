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
            frappe.throw('Enable face recognition before supervisor face check-in.')
        if self.get('enable_mobile_site_setup') in (1, '1', True):
            if not self.get('mobile_oauth_client'):
                frappe.throw('Select the public OAuth client for mobile site setup.')
            from maas_attendance_mobile.site_setup import validate_client
            validate_client(self.get('mobile_oauth_client'))
            label = (self.get('mobile_site_label') or '').strip()
            if not label or len(label) > 100:
                frappe.throw('Enter a mobile site label of at most 100 characters.')
            self.set('mobile_site_label', label)
