from frappe.model.document import Document


class MobileAttendanceFaceEnrollment(Document):
    def validate(self):
        from maas_attendance_mobile.faces import validate_enrollment
        validate_enrollment(self)

    def on_trash(self):
        # Deletion is allowed so biometric data can be erased; attached photos are removed with it.
        from maas_attendance_mobile.faces import require_hr
        require_hr()
