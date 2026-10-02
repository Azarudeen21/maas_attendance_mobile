import importlib
import sys
import types
import unittest

import test_api


class Document:
    def get(self, key):
        return getattr(self, key, None)

    def set(self, key, value):
        setattr(self, key, value)

    def is_new(self):
        return self.new

    def get_doc_before_save(self):
        return self.before


class DeviceControllerTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests()
        self.base.setUp()
        self.frappe = self.base.frappe
        from test_attendance import Flags
        self.frappe.flags = Flags()
        self.frappe.session.user = 'hr@example.test'
        self.frappe.get_roles.return_value = ['HR Manager']
        self.previous = {key: sys.modules.get(key) for key in ('frappe.model', 'frappe.model.document', 'frappe.utils')}
        sys.modules['frappe.model'] = types.ModuleType('frappe.model')
        module = types.ModuleType('frappe.model.document')
        module.Document = Document
        sys.modules['frappe.model.document'] = module
        utils = types.ModuleType('frappe.utils')
        utils.now_datetime = lambda: '2026-10-02 10:00:00'
        sys.modules['frappe.utils'] = utils
        controller = importlib.import_module('maas_attendance_mobile.maas_attendance_mobile.doctype.mobile_attendance_device.mobile_attendance_device')
        self.controller = importlib.reload(controller)
        self.doc = self.controller.MobileAttendanceDevice()
        self.doc.new = False
        self.doc.before = Document()
        values = dict(registration_id='test-id', employee='EMP-TEST', user='employee@example.test', device_id='uuid', secret_hash='hash', device_label='Test', status='Pending')
        for key, value in values.items():
            self.doc.set(key, value)
            self.doc.before.set(key, value)

    def tearDown(self):
        self.base.tearDown()
        for key, value in self.previous.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def test_hr_approval_and_revocation_record_reviewer(self):
        self.doc.status = 'Approved'
        self.doc.validate()
        self.assertEqual(self.doc.approved_by, 'hr@example.test')
        self.assertEqual(self.doc.approved_at, '2026-10-02 10:00:00')
        self.doc.before.status = 'Approved'
        self.doc.before.approved_by = self.doc.approved_by
        self.doc.status = 'Revoked'
        self.doc.validate()
        self.assertEqual(self.doc.revoked_by, 'hr@example.test')
        self.assertEqual(self.doc.approved_by, 'hr@example.test')

    def test_self_approval_and_non_hr_rejected(self):
        self.doc.status = 'Approved'
        self.frappe.session.user = self.doc.user
        with self.assertRaises(test_api.FakePermissionError):
            self.doc.validate()
        self.frappe.session.user = 'other@example.test'
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError):
            self.doc.validate()

    def test_revoked_cannot_reactivate(self):
        self.doc.before.status = 'Revoked'
        self.doc.status = 'Approved'
        with self.assertRaises(ValueError):
            self.doc.validate()

    def test_identity_edits_and_delete_rejected(self):
        self.doc.employee = 'OTHER'
        with self.assertRaises(test_api.FakePermissionError):
            self.doc.validate()
        with self.assertRaises(test_api.FakePermissionError):
            self.doc.on_trash()

    def test_audit_fields_cannot_be_forged_and_creation_requires_service(self):
        self.doc.approved_by = 'forged@example.test'
        self.doc.validate()
        self.assertIsNone(self.doc.approved_by)
        self.doc.new = True
        with self.assertRaises(test_api.FakePermissionError):
            self.doc.validate()
        self.frappe.flags.maas_device_registration = self.doc.registration_id
        self.doc.status = 'Approved'
        self.doc.validate()
        self.assertEqual(self.doc.status, 'Pending')


if __name__ == '__main__':
    unittest.main()
