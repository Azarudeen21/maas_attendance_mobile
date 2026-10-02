import importlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import test_attendance
import test_api
from test_punch_rules import REQUEST, DEVICE


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.context = test_attendance.AttendanceTests()
        self.context.setUp()
        self.frappe = self.context.frappe
        self.frappe.db.exists = Mock(return_value=False)
        self.context.config['enable_offline_test'] = '1'
        self.frappe.get_all.return_value = []
        import maas_attendance_mobile.offline
        self.service = importlib.reload(maas_attendance_mobile.offline)
        self.service.require_approved = Mock(return_value='REGISTERED')
        self.old = None
        self.prior = None
        self.device_state = 'Approved'
        self.frappe.db.get_value.side_effect = self.get_value
        self.frappe.get_doc.side_effect = self.get_doc
        self.request = dict(request_id=REQUEST, device_id=DEVICE, log_type='IN', app_version='test-0.6.0',
            timestamp=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), base_checkin=None, previous_request_id=None)
        self.doc = None
        import sys
        sys.modules['frappe.utils'].now_datetime = lambda: '2026-10-02 11:00:00'

    def tearDown(self): self.context.tearDown()

    def get_value(self, doctype, name, field, **kwargs):
        if doctype == self.service.CLAIM:
            if self.prior and name == self.prior['name']: return self.prior
            return self.old
        if doctype == 'Mobile Attendance Device':
            return {'status': self.device_state, 'employee': self.context.employee['name'], 'user': 'employee@example.test'}
        return self.context.get_value(doctype, name, field, **kwargs)

    def get_doc(self, value, name=None):
        if name: return self.doc
        doc = self.context.get_doc(value)
        if value['doctype'] == self.service.CLAIM:
            for key, item in value.items(): setattr(doc, key, item)
            doc.user = 'employee@example.test'
            doc.as_dict.side_effect = lambda: dict(value, name=doc.name, status=doc.status, checkin=doc.checkin)
            doc.save.return_value = doc
            self.doc = doc
        return doc

    def submit(self):
        result = self.service.submit(self.context.employee, self.request, 'a' * 64)
        self.old = self.doc.as_dict() | {'user': 'employee@example.test'}
        return result

    def reviewer(self):
        self.frappe.session.user = 'hr@example.test'
        self.frappe.get_roles.side_effect = lambda user=None: ['HR Manager'] if user is None else ['Employee']

    def test_upload_creates_only_immutable_pending_claim_without_secret(self):
        result = self.submit()
        self.assertEqual(result['status'], 'Pending Review')
        self.assertIsNone(result['checkin'])
        self.assertEqual([r['doctype'] for r in self.context.created], [self.service.CLAIM])
        self.assertNotIn('device_secret', self.doc.payload_json)
        self.frappe.db.commit.assert_not_called()
        self.assertIsNone(self.frappe.flags.get('maas_offline_write'))

    def test_exact_retry_recovers_and_payload_change_rejected(self):
        self.submit()
        self.assertTrue(self.submit()['duplicate'])
        self.assertEqual(len(self.context.created), 1)
        self.request['log_type'] = 'OUT'
        with self.assertRaisesRegex(ValueError, 'different claim'): self.submit()

    def test_gate_device_and_age_reject_without_inserts(self):
        self.context.config['enable_offline_test'] = '0'
        with self.assertRaises(test_api.FakePermissionError): self.submit()
        self.context.config['enable_offline_test'] = '1'
        self.service.require_approved.side_effect = test_api.FakePermissionError('revoked')
        with self.assertRaises(test_api.FakePermissionError): self.submit()
        self.service.require_approved.side_effect = None
        self.request['timestamp'] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with self.assertRaisesRegex(ValueError, '24 hours'): self.submit()
        self.assertEqual(self.context.created, [])

    def test_scope_unknown_fields_and_online_id_collision_rejected(self):
        self.request['employee'] = 'OTHER'
        with self.assertRaises(ValueError): self.submit()
        del self.request['employee']
        self.frappe.db.exists.return_value = True
        with self.assertRaisesRegex(ValueError, 'online punch'): self.submit()
        self.frappe.db.get_value.reset_mock()
        self.service.own_status(self.context.employee, REQUEST)
        self.assertEqual(self.frappe.db.get_value.call_args.args[1], {'name': REQUEST, 'employee': self.context.employee['name'], 'user': 'employee@example.test'})

    def test_review_requires_hr_and_separate_reviewer(self):
        self.submit()
        with self.assertRaises(test_api.FakePermissionError): self.service.review(REQUEST, 'Approved', 'Confirmed test')
        self.frappe.get_roles.return_value = ['HR Manager']
        with self.assertRaisesRegex(test_api.FakePermissionError, 'different HR'): self.service.review(REQUEST, 'Approved', 'Confirmed test')
        self.assertEqual(len(self.context.created), 1)

    def test_hr_approval_is_atomic_and_final_retry_has_no_duplicate_checkin(self):
        self.submit(); self.reviewer()
        response = self.service.review(REQUEST, 'Approved', 'Confirmed test evidence')
        self.assertEqual(response['status'], 'Approved')
        self.assertEqual(response['checkin'], 'CHECKIN-TEST')
        checkin = self.context.created[-1]
        self.assertEqual(checkin['employee'], self.context.employee['name'])
        self.assertEqual(checkin['skip_auto_attendance'], 1)
        self.assertTrue(self.service.review(REQUEST, 'Approved', 'Confirmed test evidence')['duplicate'])
        self.assertEqual(len(self.context.created), 2)
        with self.assertRaisesRegex(ValueError, 'already final'): self.service.review(REQUEST, 'Rejected', 'Reject now')

    def test_newer_online_checkin_causes_conflict_without_insert(self):
        self.submit(); self.reviewer()
        self.context.latest = {'name': 'NEWER', 'log_type': 'OUT', 'time': datetime.now()}
        with self.assertRaisesRegex(ValueError, 'Attendance changed'): self.service.review(REQUEST, 'Approved', 'Confirmed test')
        self.assertEqual(self.doc.status, 'Pending Review')
        self.assertEqual(len(self.context.created), 1)
        response = self.service.review(REQUEST, 'Rejected', 'Online attendance changed; manual correction required')
        self.assertEqual(response['status'], 'Rejected')
        self.assertIsNone(response['checkin'])

    def test_preceding_claim_and_revocation_block_approval(self):
        previous = 'ea1ffca1-c4b3-483e-b82e-911f46aa9be7'
        self.request['previous_request_id'] = previous
        self.submit(); self.reviewer()
        with self.assertRaisesRegex(ValueError, 'preceding'): self.service.review(REQUEST, 'Approved', 'Confirmed test')
        self.device_state = 'Revoked'
        with self.assertRaises(test_api.FakePermissionError): self.service.review(REQUEST, 'Approved', 'Confirmed test')
        self.assertEqual(len(self.context.created), 1)

    def test_delayed_gps_is_checked_at_event_time_and_current_workplace(self):
        self.context.config['require_test_location'] = '1'
        self.frappe.get_all.return_value = [dict(name='Office', latitude=24.7, longitude=46.7, radius_m=100, max_accuracy_m=30)]
        self.request['location'] = {'latitude': 24.7, 'longitude': 46.7, 'accuracy_m': 5, 'timestamp': self.request['timestamp'], 'precise': True, 'is_mocked': False}
        self.submit()
        self.assertEqual(json.loads(self.doc.payload_json)['location']['latitude'], 24.7)

    def test_future_clock_and_circular_claim_rejected(self):
        self.request['timestamp'] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        with self.assertRaises(ValueError): self.submit()
        self.request['previous_request_id'] = REQUEST
        with self.assertRaises(ValueError): self.service.parse_claim(self.request)

    def test_approved_predecessor_allows_next_out_in_order(self):
        previous = 'ea1ffca1-c4b3-483e-b82e-911f46aa9be7'
        self.request.update(previous_request_id=previous, log_type='OUT')
        self.submit(); self.reviewer()
        self.prior = {'name': previous, 'employee': self.context.employee['name'], 'user': 'employee@example.test', 'status': 'Approved', 'checkin': 'FIRST'}
        event = datetime.fromisoformat(self.request['timestamp'])
        from maas_attendance_mobile.punch_rules import site_time
        self.context.latest = {'name': 'FIRST', 'log_type': 'IN', 'time': site_time(event - timedelta(minutes=5), 'Asia/Riyadh')}
        result = self.service.review(REQUEST, 'Approved', 'Confirmed ordered offline test')
        self.assertEqual(result['status'], 'Approved')
        self.assertEqual(self.context.created[-1]['log_type'], 'OUT')


if __name__ == '__main__': unittest.main()
