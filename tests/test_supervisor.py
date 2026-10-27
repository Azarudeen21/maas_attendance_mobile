import importlib
import json
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import test_api
import test_attendance
from test_photos import image_payload
from test_punch_rules import DEVICE, REQUEST

SECRET = 'a' * 64


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.base = test_attendance.AttendanceTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.employee = self.base.employee  # Same row acts as the supervisor's own Employee.
        self.config = self.base.config
        self.config.update(enable_face_recognition=1, enable_supervisor_checkin=1)
        self.frappe.get_roles.return_value = ['Employee', 'MAAS Attendance Supervisor']
        self.frappe.session.user = 'employee@example.test'
        import maas_attendance_mobile.faces, maas_attendance_mobile.photos, maas_attendance_mobile.supervisor
        importlib.reload(maas_attendance_mobile.faces); importlib.reload(maas_attendance_mobile.photos)
        self.service = importlib.reload(maas_attendance_mobile.supervisor)
        self.match = {'HR-EMP-WORKER': 0.82, 'HR-EMP-OTHER': 0.21}
        self.names = {'HR-EMP-WORKER': {'employee_name': 'Worker One', 'company': 'Test Company'},
                      'HR-EMP-OTHER': {'employee_name': 'Other', 'company': 'Test Company'}}
        self.patches = [
            patch.object(self.service, 'analyse', Mock(return_value=([1.0] + [0.0] * 127, 0.94))),
            patch.object(self.service, 'rank', Mock(side_effect=lambda *a, **k: (self.match, self.names))),
            patch('maas_attendance_mobile.devices.require_approved', Mock(return_value='DEVICE-REG')),
            patch('maas_attendance_mobile.photos.save_private', Mock(return_value='/private/files/x.jpg')),
        ]
        for item in self.patches: item.start()
        now = datetime.now(timezone.utc)
        self.photo = image_payload(now)
        self.args = dict(employee='HR-EMP-WORKER', request_id=REQUEST, log_type='IN', timestamp=now.isoformat(),
                         device_id=DEVICE, app_version='1.2.0', device_secret=SECRET, photo=self.photo,
                         location=dict(latitude=24.7, longitude=46.7, accuracy_m=5, timestamp=now.isoformat(),
                                       precise=True, is_mocked=False))

    def tearDown(self):
        for item in self.patches: item.stop()
        self.base.tearDown()

    def identify(self):
        return self.service.identify(self.employee, self.photo, DEVICE, SECRET)

    def punch(self, **changes):
        return self.service.punch(self.employee, **dict(self.args, **changes))

    def test_identify_returns_only_the_clear_match_and_next_punch(self):
        result = self.identify()
        self.assertEqual((result['decision'], result['employee'], result['employee_name']), ('match', 'HR-EMP-WORKER', 'Worker One'))
        self.assertEqual((result['next_log_type'], result['attendance_enabled']), ('IN', True))
        self.assertEqual(self.service.rank.call_args.args[1], 'Test Company')
        self.assertTrue(self.service.rank.call_args.kwargs['ignore_permissions'])
        self.match = {'HR-EMP-WORKER': 0.50, 'HR-EMP-OTHER': 0.48}
        result = self.identify()
        self.assertEqual(result['decision'], 'ambiguous')
        self.assertNotIn('employee', result)
        self.assertFalse(self.base.created)

    def test_identify_reports_employee_without_mobile_attendance(self):
        self.base.rules['online'] = False
        result = self.identify()
        self.assertFalse(result['attendance_enabled'])
        self.assertIn('not enabled', result['message'])

    def test_role_settings_and_device_gate_every_call(self):
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.identify()
        self.frappe.get_roles.return_value = ['MAAS Attendance Supervisor']
        self.config['enable_supervisor_checkin'] = 0
        with self.assertRaises(test_api.FakePermissionError): self.punch()
        self.config['enable_supervisor_checkin'] = 1
        import maas_attendance_mobile.devices as devices
        devices.require_approved.side_effect = test_api.FakePermissionError('pending')
        with self.assertRaises(test_api.FakePermissionError): self.identify()
        self.assertFalse(self.base.created)

    def test_confirmed_punch_creates_checkin_for_identified_employee(self):
        result = self.punch()
        checkin, receipt = self.base.created
        self.assertEqual((checkin['employee'], checkin['log_type'], checkin['skip_auto_attendance']), ('HR-EMP-WORKER', 'IN', 0))
        self.assertEqual((receipt['capture_mode'], receipt['supervisor'], receipt['face_score']),
                         ('Supervisor', 'employee@example.test', 0.82))
        self.assertEqual(receipt['registered_device'], 'DEVICE-REG')
        self.assertEqual(result['employee_name'], 'Worker One')
        self.assertIsNone(self.frappe.flags.get('maas_punch'))
        import maas_attendance_mobile.photos as photos
        photos.save_private.assert_called_once()

    def test_confirmation_cannot_choose_a_different_employee(self):
        with self.assertRaisesRegex(ValueError, 'does not match'): self.punch(employee='HR-EMP-OTHER')
        self.match = {'HR-EMP-WORKER': 0.30}
        with self.assertRaisesRegex(ValueError, 'not recognised'): self.punch()
        self.assertFalse(self.base.created)

    def test_employee_policy_and_evidence_still_apply(self):
        self.base.rules['online'] = False
        with self.assertRaisesRegex(test_api.FakePermissionError, 'not enabled'): self.punch()
        self.base.rules['online'] = True
        with self.assertRaises(ValueError): self.punch(location=None)
        self.base.latest = {'name': 'C1', 'log_type': 'IN', 'time': datetime(2020, 1, 1)}
        with self.assertRaisesRegex(ValueError, 'Already checked in'): self.punch()
        self.assertFalse(self.base.created)

    def test_exact_retry_returns_original_receipt_without_new_rows(self):
        self.punch()
        receipt = dict(self.base.created[1], name=REQUEST)
        self.base.receipt = receipt; self.base.created.clear()
        self.assertTrue(self.punch()['duplicate'])
        self.assertFalse(self.base.created)
        with self.assertRaisesRegex(ValueError, 'already used'): self.punch(log_type='OUT')

    def offline(self, **changes):
        request = {key: self.args[key] for key in ('request_id', 'timestamp', 'device_id', 'app_version', 'photo', 'location')}
        return self.service.offline_punch(self.employee, json.dumps(dict(request, **changes)), SECRET)

    def test_offline_scan_identifies_employee_and_picks_next_action(self):
        result = self.offline()
        checkin, receipt = self.base.created
        self.assertEqual((checkin['employee'], checkin['log_type']), ('HR-EMP-WORKER', 'IN'))
        self.assertTrue(checkin['device_id'].startswith('MAAS-OFFLINE:'))
        self.assertEqual((receipt['capture_mode'], receipt['supervisor']), ('Supervisor', 'employee@example.test'))
        self.assertEqual(result['employee_name'], 'Worker One')
        self.base.created.clear()
        self.base.latest = {'name': 'C1', 'log_type': 'IN', 'time': datetime(2020, 1, 1)}
        self.offline(request_id=str(uuid.uuid4()))
        self.assertEqual(self.base.created[0]['log_type'], 'OUT')  # Server decides IN or OUT.

    def test_offline_scan_that_cannot_be_recorded_is_refused_and_stores_nothing(self):
        self.match = {'HR-EMP-WORKER': 0.30}
        with self.assertRaisesRegex(self.service.ScanRefused, 'not recognised'): self.offline()
        self.match = {'HR-EMP-WORKER': 0.82, 'HR-EMP-OTHER': 0.21}
        self.base.rules.update(online=False, offline=False)
        with self.assertRaisesRegex(self.service.ScanRefused, 'not enabled for Worker One'): self.offline()
        self.base.rules.update(online=True, offline_sync=False)
        with self.assertRaisesRegex(self.service.ScanRefused, 'not enabled'): self.offline()
        self.base.rules.update(online=False, offline=True, offline_sync=True)  # Offline box alone is enough.
        self.offline()
        self.assertEqual(len(self.base.created), 2)

    def test_offline_scan_retry_returns_original_receipt(self):
        self.offline()
        self.base.receipt = dict(self.base.created[1], name=REQUEST); self.base.created.clear()
        self.assertTrue(self.offline()['duplicate'])
        self.assertFalse(self.base.created)

    def test_confirm_saved_offline_after_lost_reply_is_not_recorded_twice(self):
        self.punch()  # Live confirm reached the server, but the phone never got the reply.
        self.base.receipt = dict(self.base.created[1], name=REQUEST); self.base.created.clear()
        result = self.offline()  # Same request ID, saved offline by the app.
        self.assertTrue(result['duplicate'])
        self.assertFalse(self.base.created)
        self.base.receipt['supervisor'] = 'someone.else@example.test'
        with self.assertRaisesRegex(ValueError, 'already used'): self.offline()

    def test_status_reports_supervisor_availability(self):
        self.assertTrue(self.base.base.api.attendance_status()['supervisor_checkin_enabled'])
        self.config['enable_face_recognition'] = 0
        self.assertFalse(self.base.base.api.attendance_status()['supervisor_checkin_enabled'])
        self.config['enable_face_recognition'] = 1
        self.assertTrue(self.base.base.api.attendance_status()['supervisor_checkin_enabled'])
        self.frappe.get_roles.return_value = ['Employee']
        self.assertFalse(self.base.base.api.attendance_status()['supervisor_checkin_enabled'])


if __name__ == '__main__':
    unittest.main()
