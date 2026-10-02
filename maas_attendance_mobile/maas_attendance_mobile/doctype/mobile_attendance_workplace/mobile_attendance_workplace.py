import frappe
from frappe.model.document import Document

from maas_attendance_mobile.geofence import workplace_values


class MobileAttendanceWorkplace(Document):
    def validate(self):
        try:
            values = workplace_values(self.as_dict())
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)
        for key, value in values.items():
            self.set(key, value)
        if values["max_accuracy_m"] >= values["radius_m"]:
            frappe.throw("Maximum GPS uncertainty must be smaller than the workplace radius.")
