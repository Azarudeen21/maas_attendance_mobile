import hashlib
import types
import unittest
from unittest.mock import Mock

import test_api
from test_punch_rules import DEVICE

SECRET = 'a' * 64


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests()
        self.base.setUp()
        self.service = self.base.devices
        self.frappe = self.base.frappe
        from test_attendance import Flags
        self.frappe.flags = Flags()
        self.employee = self.frappe.get_list.return_value[0]
        self.row = None
        self.created = []
        self.frappe.db.get_value.side_effect = self.lookup
        self.frappe.get_all = Mock(return_value=[])
        self.frappe.get_doc = Mock(side_effect=self.make_doc)

    def tearDown(self):
        self.base.tearDown()

    def lookup(self, doctype, name, fields, **kwargs):
        if doctype == 'Employee':
            return self.employee
        if doctype == 'User':
            return 1
        if doctype == self.service.DEVICE:
            if self.row is None:
                return None
            return self.row['name'] if fields == 'name' else self.row
        raise AssertionError(doctype)

    def make_doc(self, data):
        self.created.append(data)
        self.row = dict(data, name=data['registration_id'])
        doc = Mock()
        doc.insert.return_value = doc
        doc.as_dict.return_value = self.row
        return doc

    def register(self, secret=SECRET):
        return self.service.register(self.employee, DEVICE, secret, 'My device')

    def test_registration_is_pending_hash_only_and_scoped(self):
        result = self.register()
        self.assertEqual(result['status'], 'Pending')
        self.assertNotIn('secret_hash', result)
        self.assertNotIn('device_secret', self.created[0])
        self.assertEqual(self.created[0]['secret_hash'], hashlib.sha256(SECRET.encode()).hexdigest())
        self.assertEqual(self.created[0]['employee'], self.employee['name'])
        self.assertIsNone(self.frappe.flags.get('maas_device_registration'))

    def test_lost_registration_response_retry_does_not_rotate_or_duplicate(self):
        first = self.register()
        self.assertEqual(self.register(), first)
        self.assertEqual(len(self.created), 1)
        with self.assertRaises(test_api.FakePermissionError):
            self.register('b' * 64)

    def test_invalid_credentials_and_labels_create_nothing(self):
        for secret in ('short', 'A' * 64, None):
            with self.assertRaises(ValueError):
                self.register(secret)
        with self.assertRaises(ValueError):
            self.service.register(self.employee, DEVICE, SECRET, 'bad\nlabel')
        self.assertEqual(self.created, [])

    def test_pending_and_revoked_block_and_approved_requires_exact_secret(self):
        self.register()
        for status in ('Pending', 'Revoked'):
            self.row['status'] = status
            with self.assertRaises(test_api.FakePermissionError):
                self.service.require_approved(self.employee, DEVICE, SECRET)
        self.row['status'] = 'Approved'
        self.assertEqual(self.service.require_approved(self.employee, DEVICE, SECRET), self.row['name'])
        with self.assertRaises(test_api.FakePermissionError):
            self.service.require_approved(self.employee, DEVICE, 'b' * 64)

    def test_cross_account_status_rejected(self):
        self.register()
        self.frappe.session.user = 'other@example.test'
        with self.assertRaises(test_api.FakePermissionError):
            self.service.record(self.employee, DEVICE, SECRET)

    def test_status_of_unregistered_phone_is_not_an_error(self):
        self.frappe.db.exists = Mock(side_effect=lambda doctype, name: self.row is not None)
        self.assertEqual(self.service.status(self.employee, DEVICE, SECRET)['status'], 'Not registered')
        self.register()
        self.assertEqual(self.service.status(self.employee, DEVICE, SECRET)['device_id'], DEVICE)
        with self.assertRaises(test_api.FakePermissionError):  # Wrong secret is still refused.
            self.service.status(self.employee, DEVICE, 'b' * 64)

    def test_limit_and_employee_revalidation_prevent_inserts(self):
        self.frappe.get_all.return_value = [{'name': str(n)} for n in range(5)]
        with self.assertRaises(ValueError):
            self.register()
        self.employee['status'] = 'Left'
        with self.assertRaises(test_api.FakePermissionError):
            self.register()
        self.assertEqual(self.created, [])

    def test_revoked_registration_retry_stays_revoked(self):
        self.register()
        self.row['status'] = 'Revoked'
        self.assertEqual(self.register()['status'], 'Revoked')
        self.assertEqual(len(self.created), 1)


if __name__ == '__main__':
    unittest.main()
