import types
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

import test_attendance
import test_offline
import test_api


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.base = test_attendance.AttendanceTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.config = self.base.config
        self.config.update(test_employee='DEDICATED-TEST', enable_production_attendance=1,
                           enable_production_offline=1)
        self.enrollment = {'enabled': 1, 'company': 'Test Company'}
        self.policy = {'enable_production_attendance': 1, 'enable_production_offline': 1,
                       'production_require_photo': 0, 'production_require_location': 0}
        self.original_lookup = self.base.get_value
        self.frappe.db.get_value.side_effect = self.lookup
        from maas_attendance_mobile import devices
        self.device_patch = patch.object(devices, 'require_approved', return_value='APPROVED')
        self.device_patch.start()

    def tearDown(self):
        self.device_patch.stop(); self.base.tearDown()

    def lookup(self, dt, name, fields, **kwargs):
        if dt == 'Mobile Attendance Employee': return self.enrollment
        if dt == 'Mobile Attendance Company Policy': return self.policy
        return self.original_lookup(dt, name, fields, **kwargs)

    def test_status_uses_production_contract_and_mandatory_device(self):
        status = self.base.service.status(self.base.employee)
        self.assertFalse(status['test_mode']); self.assertFalse(status['skip_auto_attendance'])
        self.assertTrue(status['online_submission_enabled']); self.assertTrue(status['approved_device_required'])
        self.assertFalse(status['online_test_submission_enabled'])
        self.assertEqual(status['attendance_mode'], 'production')

    def test_auto_accept_requires_explicit_company_flag_and_both_evidence_checks(self):
        self.assertFalse(self.base.service.status(self.base.employee)['offline_auto_accept_enabled'])
        self.policy['production_auto_accept_offline'] = 1
        self.assertFalse(self.base.service.status(self.base.employee)['offline_auto_accept_enabled'])
        self.policy.update(production_require_photo=1, production_require_location=1)
        self.assertTrue(self.base.service.status(self.base.employee)['offline_auto_accept_enabled'])
        self.config['enable_production_offline'] = 0
        self.assertFalse(self.base.service.status(self.base.employee)['offline_auto_accept_enabled'])

    def test_site_company_and_employee_gates_each_block_production(self):
        for target, key in ((self.config, 'enable_production_attendance'),
                            (self.policy, 'enable_production_attendance'), (self.enrollment, 'enabled')):
            old = target[key]; target[key] = 0
            with self.assertRaises(test_api.FakePermissionError): self.base.submit(attendance_mode='production')
            self.assertEqual(self.base.created, [])
            target[key] = old

    def test_missing_or_transferred_enrollment_is_denied(self):
        for row in (None, {'enabled': 1, 'company': 'Other Company'}):
            self.enrollment = row
            with self.assertRaises(test_api.FakePermissionError): self.base.submit(attendance_mode='production')

    def test_production_creates_standard_checkin_without_skip_and_binds_mode(self):
        result = self.base.submit(attendance_mode='production', device_secret='secret')
        checkin, receipt = self.base.created
        self.assertEqual(checkin['skip_auto_attendance'], 0)
        self.assertTrue(checkin['device_id'].startswith('MAAS:'))
        self.assertEqual(receipt['test_mode'], 0)
        self.assertFalse(result['test_mode']); self.assertFalse(result['skip_auto_attendance'])
        self.assertEqual(receipt['registered_device'], 'APPROVED')

    def test_old_client_or_wrong_mode_cannot_silently_create_production(self):
        with self.assertRaisesRegex(ValueError, 'mode changed'): self.base.submit()
        self.assertEqual(self.base.created, [])

    def test_dedicated_test_employee_never_becomes_production(self):
        self.config['test_employee'] = self.base.employee['name']
        self.config['enable_test_attendance'] = 1
        status = self.base.service.status(self.base.employee)
        self.assertTrue(status['test_mode'])
        with self.assertRaisesRegex(ValueError, 'mode changed'): self.base.submit(attendance_mode='production')
        self.assertTrue(self.base.submit()['skip_auto_attendance'])

    def test_exact_production_retry_keeps_original_mode_and_does_not_insert(self):
        self.base.submit(attendance_mode='production')
        self.base.receipt = dict(self.base.created[1], name=self.base.args['request_id'])
        self.base.created.clear()
        result = self.base.submit(attendance_mode='production')
        self.assertTrue(result['duplicate']); self.assertFalse(result['test_mode'])
        self.assertEqual(self.base.created, [])


class ProductionOfflineTests(unittest.TestCase):
    def setUp(self):
        self.base = test_offline.OfflineTests(); self.base.setUp()
        self.base.context.config.update(production_mode=True)
        # Effective configuration is supplied here; separate tests above cover
        # server-selected enrollment/company gates.
        self.base.request['attendance_mode'] = 'production'

    def tearDown(self): self.base.tearDown()

    def test_upload_and_approval_preserve_production_original_event(self):
        reply = self.base.submit()
        self.assertFalse(reply['test_mode']); self.assertFalse(reply['skip_auto_attendance'])
        self.assertEqual(reply['status'], 'Pending Review')
        self.base.reviewer()
        reply = self.base.service.review(self.base.request['request_id'], 'Approved', 'Evidence confirmed by HR')
        checkin = self.base.context.created[-1]
        self.assertEqual(checkin['skip_auto_attendance'], 0)
        self.assertTrue(checkin['device_id'].startswith('MAAS-OFFLINE:'))
        self.assertFalse(reply['test_mode'])
        self.assertEqual(checkin['time'], self.base.doc.event_time)

    def test_policy_mode_change_cannot_convert_existing_claim(self):
        self.base.submit(); self.base.reviewer()
        self.base.context.config['production_mode'] = False
        with self.assertRaisesRegex(ValueError, 'mode changed'):
            self.base.service.review(self.base.request['request_id'], 'Approved', 'Evidence confirmed by HR')
        self.assertEqual(len(self.base.context.created), 1)

    def test_employee_transfer_blocks_approval_but_allows_rejection(self):
        self.base.submit(); self.base.reviewer()
        self.base.context.employee['company'] = 'New Company'
        with self.assertRaisesRegex(ValueError, 'company changed'):
            self.base.service.review(self.base.request['request_id'], 'Approved', 'Evidence confirmed by HR')
        result = self.base.service.review(self.base.request['request_id'], 'Rejected', 'Company changed; manual correction required')
        self.assertEqual(result['status'], 'Rejected')

    def test_auto_accept_creates_original_production_checkin_and_exact_retry(self):
        self.base.context.config['auto_accept_offline'] = True
        response = self.base.submit()
        self.assertEqual(response['status'], 'Approved')
        self.assertTrue(response['automatically_accepted'])
        self.assertIsNone(self.base.doc.reviewed_by if hasattr(self.base.doc, 'reviewed_by') else None)
        checkin = self.base.context.created[-1]
        self.assertEqual(checkin['time'], self.base.doc.event_time)
        self.assertEqual(checkin['skip_auto_attendance'], 0)
        self.assertTrue(self.base.submit()['duplicate'])
        self.assertEqual(len(self.base.context.created), 2)
        self.base.frappe.db.commit.assert_not_called()
        self.assertIsNone(self.base.frappe.flags.get('maas_offline_auto_accept'))
        self.assertIsNone(self.base.frappe.flags.get('maas_test_punch'))

    def test_auto_accept_conflict_keeps_evidence_pending_without_checkin(self):
        self.base.context.config['auto_accept_offline'] = True
        self.base.context.latest = {'name': 'NEWER', 'log_type': 'OUT', 'time': datetime.now()}
        response = self.base.submit()
        self.assertEqual(response['status'], 'Pending Review')
        self.assertIn('Attendance changed', response['sync_note'])
        self.assertIsNone(response['checkin'])
        self.assertEqual(len(self.base.context.created), 1)

    def test_auto_accept_predecessor_requires_same_device_and_mode(self):
        self.base.context.config['auto_accept_offline'] = True
        self.base.request['previous_request_id'] = 'ea1ffca1-c4b3-483e-b82e-911f46aa9be7'
        self.base.prior = dict(name=self.base.request['previous_request_id'],
            employee=self.base.context.employee['name'], user='employee@example.test',
            status='Approved', checkin='FIRST', registered_device='OTHER', test_mode=0)
        response = self.base.submit()
        self.assertEqual(response['status'], 'Pending Review')
        self.assertIn('preceding', response['sync_note'])

    def test_automatic_out_accepts_approved_same_device_production_predecessor(self):
        self.base.context.config['auto_accept_offline'] = True
        previous = 'ea1ffca1-c4b3-483e-b82e-911f46aa9be7'
        self.base.request.update(previous_request_id=previous, log_type='OUT')
        self.base.prior = dict(name=previous, employee=self.base.context.employee['name'],
            user='employee@example.test', status='Approved', checkin='FIRST',
            registered_device='REGISTERED', test_mode=0)
        from maas_attendance_mobile.punch_rules import site_time
        event = datetime.fromisoformat(self.base.request['timestamp'])
        self.base.context.latest = dict(name='FIRST', log_type='IN',
            time=site_time(event - timedelta(minutes=5), 'Asia/Riyadh'))
        self.assertEqual(self.base.submit()['status'], 'Approved')
        self.assertEqual(self.base.context.created[-1]['log_type'], 'OUT')

    def test_automatic_chain_cannot_reference_a_test_mode_claim(self):
        self.base.context.config['auto_accept_offline'] = True
        previous = 'ea1ffca1-c4b3-483e-b82e-911f46aa9be7'
        self.base.request['previous_request_id'] = previous
        self.base.prior = dict(name=previous, employee=self.base.context.employee['name'],
            user='employee@example.test', status='Approved', checkin='FIRST',
            registered_device='REGISTERED', test_mode=1)
        self.assertEqual(self.base.submit()['status'], 'Pending Review')
        self.assertEqual(len(self.base.context.created), 1)

    def test_auto_accept_persistence_failure_is_not_reported_as_pending(self):
        self.base.context.config['auto_accept_offline'] = True
        original = self.base.get_doc
        def failed_checkin(value, name=None):
            if isinstance(value, dict) and value['doctype'] == 'Employee Checkin':
                raise RuntimeError('database unavailable')
            return original(value, name)
        self.base.frappe.get_doc.side_effect = failed_checkin
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'): self.base.submit()
        self.assertIsNone(self.base.frappe.flags.get('maas_test_punch'))

    def test_test_mode_never_automatically_accepts_even_with_flag(self):
        self.base.context.config.update(production_mode=False, auto_accept_offline=True)
        self.base.request.pop('attendance_mode')
        self.assertEqual(self.base.submit()['status'], 'Pending Review')
        self.assertEqual(len(self.base.context.created), 1)


if __name__ == '__main__': unittest.main()
