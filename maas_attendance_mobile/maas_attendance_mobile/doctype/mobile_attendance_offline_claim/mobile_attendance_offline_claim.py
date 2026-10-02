import frappe
from frappe.model.document import Document


class MobileAttendanceOfflineClaim(Document):
    def validate(self):
        if frappe.flags.get('maas_offline_write') != self.request_id:
            frappe.throw('Use the offline submission or HR review API.', frappe.PermissionError)
        old = self.get_doc_before_save()
        if not old:
            if self.status != 'Pending Review' or self.checkin or self.reviewed_by or self.reviewed_at or self.review_reason:
                frappe.throw('New offline claims must await review.', frappe.ValidationError)
        else:
            for field in ('request_id', 'employee', 'user', 'request_hash', 'payload_json', 'registered_device', 'log_type', 'event_time', 'test_mode', 'event_company',
                          'photo_company', 'photo_uploaded_at', 'photo_retention_hold', 'photo_hold_reason',
                          'photo_hold_by', 'photo_hold_at', 'photo_removed_at', 'photo_cleanup'):
                if self.get(field) != old.get(field):
                    frappe.throw('Offline evidence cannot be changed.', frappe.PermissionError)
            if old.status != 'Pending Review' or self.status not in ('Approved', 'Rejected'):
                frappe.throw('Offline reviews are final.', frappe.ValidationError)

    def on_trash(self):
        frappe.throw('Offline audit claims cannot be deleted.', frappe.PermissionError)
