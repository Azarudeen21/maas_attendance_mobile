import importlib
import unittest
import test_device_controller
import test_api


class OfflineControllerTests(unittest.TestCase):
    def setUp(self):
        self.context = test_device_controller.DeviceControllerTests()
        self.context.setUp()
        module = importlib.import_module('maas_attendance_mobile.maas_attendance_mobile.doctype.mobile_attendance_offline_claim.mobile_attendance_offline_claim')
        module = importlib.reload(module)
        self.doc = module.MobileAttendanceOfflineClaim()
        self.doc.request_id = 'test-id'
        self.doc.status = 'Pending Review'
        self.doc.checkin = self.doc.reviewed_by = self.doc.reviewed_at = self.doc.review_reason = None
        self.doc.before = None

    def tearDown(self): self.context.tearDown()

    def test_orm_cannot_create_edit_or_delete_claims(self):
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()
        with self.assertRaises(test_api.FakePermissionError): self.doc.on_trash()
        self.context.frappe.flags.maas_offline_write = self.doc.request_id
        self.doc.validate()
        self.doc.status = 'Approved'
        with self.assertRaises(ValueError): self.doc.validate()

    def test_original_evidence_and_final_reviews_are_immutable(self):
        self.context.frappe.flags.maas_offline_write = self.doc.request_id
        self.doc.before = test_device_controller.Document()
        self.doc.before.request_id = self.doc.request_id
        self.doc.before.status = 'Pending Review'
        self.doc.status = 'Rejected'
        self.doc.payload_json = 'changed evidence'
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()
        self.doc.payload_json = None
        self.doc.validate()
        self.doc.before.status = 'Approved'
        with self.assertRaises(ValueError): self.doc.validate()

    def test_auto_approval_cannot_be_forged_or_change_immutable_mode(self):
        self.context.frappe.flags.maas_offline_write = self.doc.request_id
        self.doc.before = test_device_controller.Document()
        self.doc.before.request_id = self.doc.request_id
        self.doc.before.status = 'Pending Review'
        self.doc.status = 'Approved'; self.doc.checkin = 'CHECKIN'
        self.doc.automatically_accepted = 1; self.doc.test_mode = 1
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()
        self.context.frappe.flags.maas_offline_auto_accept = self.doc.request_id
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()
        self.doc.before.test_mode = 1
        self.doc.validate()
        self.doc.test_mode = 0; self.doc.before.test_mode = 0
        self.doc.validate()
        self.doc.before.status = 'Approved'
        with self.assertRaises(ValueError): self.doc.validate()

    def test_pending_auto_conflict_can_only_update_note(self):
        self.context.frappe.flags.maas_offline_write = self.doc.request_id
        self.context.frappe.flags.maas_offline_auto_accept = self.doc.request_id
        self.doc.before = test_device_controller.Document()
        self.doc.before.request_id = self.doc.request_id
        self.doc.before.status = 'Pending Review'
        self.doc.sync_note = 'Attendance changed after capture'
        self.doc.validate()
        self.doc.checkin = 'FORGED'
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()


if __name__ == '__main__': unittest.main()
