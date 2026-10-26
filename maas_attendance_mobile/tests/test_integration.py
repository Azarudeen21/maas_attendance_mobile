"""Real-database checks, run with: bench --site <site> run-tests --module maas_attendance_mobile.tests.test_integration

Requires allow_tests on the site. Every test runs as a fresh employee and is rolled back.
"""
import base64
import io
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase  # v16
except ImportError:
    from frappe.tests.utils import FrappeTestCase as TestCase  # v15

from maas_attendance_mobile import api

POLICY = 'Mobile Attendance Company Policy'


def jpeg(color='gray'):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (480, 360), color).save(buffer, 'JPEG')
    return base64.b64encode(buffer.getvalue()).decode()


class MobileAttendanceIntegration(TestCase):
    def setUp(self):
        frappe.set_user('Administrator')
        self.company = frappe.get_all('Company', pluck='name', limit=1)[0]
        frappe.db.set_single_value('Mobile Attendance Settings', {'enable_mobile_attendance': 1, 'enable_offline_sync': 1,
            'enable_face_recognition': 1, 'enable_supervisor_checkin': 1})
        rules = {'enabled': 1, 'require_photo': 0, 'require_location': 0, 'require_approved_device': 0, 'allow_offline': 1}
        if frappe.db.exists(POLICY, self.company):
            frappe.db.set_value(POLICY, self.company, rules)
        else:
            frappe.get_doc(dict(rules, doctype=POLICY, company=self.company)).insert()
        self.user = 'maas.integration.%s@example.test' % uuid.uuid4().hex[:8]
        frappe.get_doc({'doctype': 'User', 'email': self.user, 'first_name': 'Integration', 'send_welcome_email': 0,
            'roles': [{'role': 'Employee'}, {'role': 'MAAS Attendance Supervisor'}]}).insert(ignore_permissions=True)
        self.employee = frappe.get_doc({'doctype': 'Employee', 'first_name': 'Integration', 'last_name': 'Test',
            'gender': frappe.get_all('Gender', pluck='name', limit=1)[0], 'date_of_birth': '1990-01-01',
            'date_of_joining': '2024-01-01', 'company': self.company, 'status': 'Active', 'user_id': self.user,
            'maas_online_checkin': 1, 'maas_offline_checkin': 1}).insert(ignore_permissions=True).name
        self.device_id, self.secret = str(uuid.uuid4()), secrets.token_hex(32)

    def tearDown(self):
        frappe.set_user('Administrator')
        frappe.db.rollback()
        frappe.clear_cache(doctype='Mobile Attendance Settings')

    def approved_device(self):
        frappe.set_user(self.user)
        self.assertEqual(api.register_device(self.device_id, self.secret, 'Integration phone')['status'], 'Pending')
        with self.assertRaises(frappe.PermissionError):  # Nobody approves their own phone.
            device = frappe.get_doc('Mobile Attendance Device', {'device_id': self.device_id})
            device.status = 'Approved'
            device.save(ignore_permissions=True)
        frappe.set_user('Administrator')
        device = frappe.get_doc('Mobile Attendance Device', {'device_id': self.device_id})
        device.status = 'Approved'
        device.save()
        frappe.set_user(self.user)
        self.assertEqual(api.device_status(self.device_id, self.secret)['status'], 'Approved')

    def test_online_punch_creates_a_real_checkin(self):
        frappe.set_user(self.user)
        status = api.attendance_status()
        self.assertTrue(status['online_submission_enabled'])
        reply = api.punch(str(uuid.uuid4()), status['next_log_type'], datetime.now(timezone.utc).isoformat(),
                          self.device_id, '1.4.0')
        checkin = frappe.db.get_value('Employee Checkin', reply['checkin'], ['employee', 'skip_auto_attendance', 'device_id'], as_dict=True)
        self.assertEqual((checkin.employee, checkin.skip_auto_attendance), (self.employee, 0))
        self.assertTrue(checkin.device_id.startswith('MAAS:'))

    def test_unticked_employee_is_blocked(self):
        frappe.db.set_value('Employee', self.employee, {'maas_online_checkin': 0, 'maas_offline_checkin': 0})
        frappe.set_user(self.user)
        self.assertFalse(api.attendance_status()['online_submission_enabled'])
        with self.assertRaises(frappe.PermissionError):
            api.punch(str(uuid.uuid4()), 'IN', datetime.now(timezone.utc).isoformat(), self.device_id, '1.4.0')

    def test_offline_upload_becomes_checkin_at_original_time(self):
        self.approved_device()
        status = api.attendance_status()
        taken = datetime.now(timezone.utc) - timedelta(seconds=30)
        request = {'request_id': str(uuid.uuid4()), 'device_id': self.device_id, 'log_type': status['next_log_type'],
                   'timestamp': taken.isoformat(), 'app_version': '1.4.0',
                   'base_checkin': status['last_checkin']['name'] if status['last_checkin'] else None, 'previous_request_id': None}
        reply = api.submit_offline_claim(frappe.as_json(request), self.secret)
        self.assertEqual(reply['status'], 'Approved')
        self.assertEqual(api.submit_offline_claim(frappe.as_json(request), self.secret)['checkin'], reply['checkin'])
        stored = frappe.db.get_value('Employee Checkin', reply['checkin'], ['time', 'skip_auto_attendance'], as_dict=True)
        self.assertEqual(stored.skip_auto_attendance, 0)
        self.assertLess(abs((stored.time - frappe.utils.convert_utc_to_system_timezone(taken.replace(tzinfo=None)).replace(tzinfo=None, microsecond=0)).total_seconds()), 2)

    def test_face_engine_and_supervisor_identify_run(self):
        self.assertEqual(api.face_test_match(jpeg(), self.company)['decision'], 'no_face')
        self.approved_device()
        photo = {'image_base64': jpeg('white'), 'captured_at': datetime.now(timezone.utc).isoformat()}
        self.assertEqual(api.supervisor_identify(photo, self.device_id, self.secret)['decision'], 'no_face')
