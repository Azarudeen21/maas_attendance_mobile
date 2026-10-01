"""Service contract tests with fake Frappe; database tests remain a live checkpoint."""

import importlib
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

import test_api
from test_punch_rules import DEVICE, REQUEST
from maas_attendance_mobile.punch_rules import parse_request, request_hash


class Flags(dict):
    def __setattr__(self, key, value):
        self[key] = value


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests()
        self.base.setUp()
        self.frappe = self.base.frappe
        self.service = self.base.attendance
        self.employee = self.frappe.get_list.return_value[0]
        self.config = {'enable_test_attendance': '1', 'test_employee': self.employee['name']}
        self.frappe.db.get_singles_dict.return_value = self.config
        self.frappe.flags = Flags()
        self.frappe.db.exists = Mock(return_value=True)
        self.frappe.db.commit = Mock()
        self.latest = None
        self.receipt = None
        self.created = []
        self.frappe.db.get_value.side_effect = self.get_value
        self.frappe.get_doc = Mock(side_effect=self.get_doc)
        self.old_utils = sys.modules.get('frappe.utils')
        utils = types.ModuleType('frappe.utils')
        utils.get_system_timezone = lambda: 'Asia/Riyadh'
        sys.modules['frappe.utils'] = utils
        self.args = dict(request_id=REQUEST, log_type='IN',
                         timestamp=datetime.now(timezone.utc).isoformat(),
                         device_id=DEVICE, app_version='0.2.0')

    def tearDown(self):
        self.base.tearDown()
        if self.old_utils is None:
            sys.modules.pop('frappe.utils', None)
        else:
            sys.modules['frappe.utils'] = self.old_utils

    def get_value(self, doctype, name, field, **kwargs):
        if doctype == 'Employee':
            return self.employee['user_id'] if field == 'user_id' else self.employee
        if doctype == 'User':
            return 1
        if doctype == self.service.RECEIPT:
            return self.receipt
        if doctype == 'Employee Checkin':
            return self.latest
        raise AssertionError(doctype)

    def get_doc(self, value):
        self.created.append(value)
        doc = Mock()
        doc.name = value.get('request_id', 'CHECKIN-TEST')
        doc.insert.return_value = doc
        doc.as_dict.return_value = dict(value, name=doc.name)
        return doc

    def submit(self, **changes):
        return self.service.submit(self.employee, **dict(self.args, **changes))

    def saved_receipt(self):
        payload, _ = parse_request(**self.args)
        return dict(name=REQUEST, employee=self.employee['name'],
                    request_hash=request_hash(payload, self.employee['name'], self.frappe.session.user),
                    checkin='ORIGINAL', log_type='IN', event_time='2026-10-01 22:44:28',
                    timestamp_utc=payload['timestamp_utc'])

    def test_new_punch_forces_employee_and_skip_auto_attendance(self):
        result = self.submit()
        self.assertEqual(self.created[0]['employee'], self.employee['name'])
        self.assertEqual(self.created[0]['skip_auto_attendance'], 1)
        self.assertTrue(self.created[0]['device_id'].startswith('MAAS-TEST:'))
        self.assertEqual(self.created[1]['request_id'], REQUEST)
        self.assertFalse(result['duplicate'])
        self.frappe.db.commit.assert_not_called()
        self.assertIsNone(self.frappe.flags.get('maas_test_punch'))
        employee_read = next(c for c in self.frappe.db.get_value.call_args_list
                             if c.args[0] == 'Employee' and isinstance(c.args[2], list))
        self.assertTrue(employee_read.kwargs['for_update'])

    def test_default_disabled_and_wrong_allowlist_create_nothing(self):
        for config in ({}, {'enable_test_attendance': '1', 'test_employee': 'OTHER'}):
            self.frappe.db.get_singles_dict.return_value = config
            with self.assertRaises(test_api.FakePermissionError):
                self.submit()
        self.frappe.get_doc.assert_not_called()

    def test_identity_revalidated_under_lock(self):
        self.employee['status'] = 'Left'
        with self.assertRaises(test_api.FakePermissionError):
            self.submit()
        self.frappe.get_doc.assert_not_called()

    def test_same_uuid_retry_returns_original_without_write(self):
        self.receipt = self.saved_receipt()
        self.assertTrue(self.submit()['duplicate'])
        self.frappe.get_doc.assert_not_called()

    def test_expired_retry_still_returns_original(self):
        self.args['timestamp'] = '2020-01-01T12:00:00Z'
        self.receipt = self.saved_receipt()
        self.assertTrue(self.submit()['duplicate'])

    def test_uuid_changed_payload_or_other_employee_rejected(self):
        self.receipt = self.saved_receipt()
        with self.assertRaises(ValueError):
            self.submit(log_type='OUT')
        self.receipt['employee'] = 'OTHER'
        with self.assertRaises(ValueError):
            self.submit()
        self.frappe.get_doc.assert_not_called()

    def test_stale_new_punch_rejected(self):
        with self.assertRaises(ValueError):
            self.submit(timestamp='2020-01-01T12:00:00Z')
        self.frappe.get_doc.assert_not_called()

    def test_receipt_failure_propagates_and_clears_context(self):
        self.frappe.get_doc.side_effect = [self.get_doc({'doctype': 'Employee Checkin'}), ValueError('receipt failure')]
        with self.assertRaisesRegex(ValueError, 'receipt failure'):
            self.submit()
        self.assertIsNone(self.frappe.flags.get('maas_test_punch'))
        self.frappe.db.commit.assert_not_called()

    def test_direct_standard_write_for_test_login_rejected_even_when_disabled(self):
        doc = types.SimpleNamespace(employee='OTHER', is_new=lambda: True)
        self.config['enable_test_attendance'] = '0'
        with self.assertRaises(test_api.FakePermissionError):
            self.service.guard_checkin(doc)

    def test_other_admin_workflow_unaffected(self):
        self.frappe.session.user = 'Administrator'
        self.service.guard_checkin(types.SimpleNamespace(employee='OTHER'))

    def test_guard_rejects_saved_record_and_wrong_context(self):
        doc = types.SimpleNamespace(employee=self.employee['name'], is_new=lambda: False)
        self.frappe.flags.maas_test_punch = dict(employee=doc.employee, user=self.frappe.session.user)
        with self.assertRaises(test_api.FakePermissionError):
            self.service.guard_checkin(doc)

    def test_deleted_original_receipt_fails_without_recreating(self):
        self.receipt = self.saved_receipt()
        self.frappe.db.exists.return_value = False
        with self.assertRaises(ValueError):
            self.submit()
        self.frappe.get_doc.assert_not_called()

    def test_client_cannot_supply_employee_or_skip_flag(self):
        with self.assertRaises(TypeError):
            self.base.api.punch(**self.args, employee='OTHER')
        with self.assertRaises(TypeError):
            self.base.api.punch(**self.args, skip_auto_attendance=0)
