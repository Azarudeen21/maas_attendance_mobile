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
