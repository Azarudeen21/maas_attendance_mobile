"""Real Frappe/HRMS SQL tests. Opt-in development sites only; not mocked tests."""
from datetime import datetime, timedelta, timezone
import secrets
import unittest
from uuid import uuid4
from unittest.mock import patch

import frappe

from maas_attendance_mobile import attendance, devices, offline
from maas_attendance_mobile.integration_checks import require_isolated_test_site


class TestAttendanceDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Before company/user fixtures or settings changes, even when invoked by
        # an overly broad bench run-tests command.
        require_isolated_test_site()
        if not {'erpnext', 'hrms', 'maas_attendance_mobile'} <= set(frappe.get_installed_apps()):
            raise RuntimeError('Install and migrate ERPNext, HRMS and MAAS before running these tests')

    def setUp(self):
        self.old_user = frappe.session.user
        frappe.set_user('Administrator')
        self.point = 'maas_integration_' + uuid4().hex[:12]
        frappe.db.savepoint(self.point)
        self.addCleanup(self._rollback)
        # No fixture commits. The failed-receipt test uses a second savepoint
        # to model Frappe's failed-POST transaction rollback.
        self.commit_guard = patch.object(frappe.db, 'commit', side_effect=RuntimeError('Integration fixtures must not commit'))
        self.commit_guard.start()
        self.addCleanup(self.commit_guard.stop)
        suffix = uuid4().hex[:8]
        self.company = frappe.get_doc({'doctype': 'Company', 'company_name': 'MAAS Integration ' + suffix,
            'abbr': suffix.upper(), 'default_currency': 'SAR', 'country': 'Saudi Arabia'}).insert().name
        self.user = 'maas-integration-' + suffix + '@example.invalid'
        frappe.get_doc({'doctype': 'User', 'email': self.user, 'first_name': 'Integration',
            'enabled': 1, 'send_welcome_email': 0, 'user_type': 'System User',
            'roles': [{'role': 'Employee'}]}).insert()
        self.employee = frappe.get_doc({'doctype': 'Employee', 'first_name': 'Integration',
            'employee_name': 'Integration ' + suffix, 'company': self.company, 'user_id': self.user,
            'status': 'Active', 'gender': 'Male', 'date_of_birth': '2000-01-01',
            'date_of_joining': '2020-01-01'}).insert().as_dict()
        frappe.get_doc({'doctype': 'Mobile Attendance Company Policy', 'company': self.company,
            'enabled': 0, 'enable_production_attendance': 1, 'enable_production_offline': 1,
            'production_require_photo': 0, 'production_require_location': 0}).insert()
        frappe.get_doc({'doctype': 'Mobile Attendance Employee', 'employee': self.employee.name,
            'company': self.company, 'enabled': 1}).insert()
        settings = frappe.get_single(attendance.SETTINGS)
        settings.update({'enable_test_attendance': 0, 'enable_offline_test': 0,
            'enable_production_attendance': 1, 'enable_production_offline': 1,
            'require_test_photo': 0, 'require_test_location': 0})
        settings.save()
        self.device_id = str(uuid4()); self.device_secret = secrets.token_hex(32)
        frappe.set_user(self.user)
        devices.register(self.employee, self.device_id, self.device_secret, 'Integration device')
        registration, _ = devices.credential(self.device_id, self.device_secret, self.employee.name)
        frappe.set_user('Administrator')
        self.device = frappe.get_doc(devices.DEVICE, registration)
        self.device.status = 'Approved'; self.device.save()
        self.event = datetime.now(timezone.utc) - timedelta(seconds=15)
        self.request_id = str(uuid4())
        frappe.set_user(self.user)

    def _rollback(self):
        try:
            frappe.set_user('Administrator')
            frappe.db.rollback(save_point=self.point)
            frappe.clear_cache()
        finally:
            frappe.set_user(self.old_user)

    def punch(self, **changes):
        args = dict(request_id=self.request_id, log_type='IN', timestamp=self.event.isoformat(),
            device_id=self.device_id, device_secret=self.device_secret,
            app_version='integration-0.12', attendance_mode='production')
        args.update(changes)
        return attendance.submit(self.employee, **args)

    def test_production_receipt_retry_and_out_create_exactly_two_checkins(self):
        first = self.punch()
        duplicate = self.punch()
        self.assertEqual(first['checkin'], duplicate['checkin']); self.assertTrue(duplicate['duplicate'])
        self.assertFalse(first['test_mode']); self.assertFalse(first['skip_auto_attendance'])
        self.assertEqual(frappe.db.get_value('Employee Checkin', first['checkin'], 'skip_auto_attendance'), 0)
        second = self.punch(request_id=str(uuid4()), log_type='OUT', timestamp=(self.event + timedelta(seconds=3)).isoformat())
        self.assertNotEqual(first['checkin'], second['checkin'])
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 2)
        self.assertEqual(frappe.db.count(attendance.RECEIPT, {'employee': self.employee.name}), 2)

    def test_receipt_failure_rolls_back_inserted_checkin_and_restores_context(self):
        from maas_attendance_mobile.maas_attendance_mobile.doctype.mobile_attendance_transaction.mobile_attendance_transaction import MobileAttendanceTransaction
        point = 'maas_failed_post'; frappe.db.savepoint(point)
        before = frappe.flags.get('maas_test_punch')
        with patch.object(MobileAttendanceTransaction, 'before_insert', side_effect=RuntimeError('Injected receipt failure')):
            with self.assertRaises(RuntimeError): self.punch()
        frappe.db.rollback(save_point=point)
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 0)
        self.assertEqual(frappe.db.count(attendance.RECEIPT, {'employee': self.employee.name}), 0)
        self.assertEqual(frappe.flags.get('maas_test_punch'), before)

    def test_direct_resource_writes_and_audit_mutation_are_blocked(self):
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({'doctype': 'Employee Checkin', 'employee': self.employee.name,
                'time': datetime.now(), 'log_type': 'IN'}).insert(ignore_permissions=True)
        first = self.punch()
        receipt = frappe.get_doc(attendance.RECEIPT, first['request_id'])
        receipt.app_version = 'changed'
        with self.assertRaises(frappe.PermissionError): receipt.save(ignore_permissions=True)

    def test_revoked_device_blocks_even_exact_retry(self):
        first = self.punch()
        frappe.set_user('Administrator')
        device = frappe.get_doc(devices.DEVICE, self.device.name)
        device.status = 'Revoked'; device.save()
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError): self.punch()
        self.assertTrue(frappe.db.exists('Employee Checkin', first['checkin']))
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 1)

    def test_offline_hr_approval_preserves_original_time_and_production_mode(self):
        claim = {'request_id': self.request_id, 'log_type': 'IN', 'timestamp': self.event.isoformat(),
            'device_id': self.device_id, 'app_version': 'integration-0.12', 'base_checkin': None,
            'previous_request_id': None, 'attendance_mode': 'production'}
        submitted = offline.submit(self.employee, claim, self.device_secret)
        self.assertEqual(submitted['status'], 'Pending Review')
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 0)
        frappe.set_user('Administrator')
        approved = offline.review(self.request_id, 'Approved', 'Integration evidence confirmed')
        row = frappe.get_doc(offline.CLAIM, self.request_id)
        checkin = frappe.get_doc('Employee Checkin', approved['checkin'])
        self.assertEqual(checkin.time, row.event_time.replace(microsecond=0))
        self.assertEqual(checkin.skip_auto_attendance, 0)
        self.assertFalse(approved['test_mode'])

    def prepare_automatic_offline(self):
        frappe.set_user('Administrator')
        frappe.get_doc({'doctype': 'Mobile Attendance Workplace',
            'workplace_name': 'Integration ' + uuid4().hex[:8], 'company': self.company,
            'enabled': 1, 'latitude': 24.7136, 'longitude': 46.6753,
            'radius_m': 100, 'max_accuracy_m': 20}).insert()
        policy = frappe.get_doc('Mobile Attendance Company Policy', self.company)
        policy.production_require_photo = policy.production_require_location = 1
        policy.save()
        self.generated_photo_ids = set()
        self.addCleanup(self.remove_generated_claim_photos)
        frappe.set_user(self.user)

    def photo_claim(self, *, event=None, previous=None, log_type='IN'):
        import base64, io
        from PIL import Image
        event = event or self.event
        stream = io.BytesIO()
        Image.frombytes('RGB', (160, 160), secrets.token_bytes(160 * 160 * 3)).save(stream, format='JPEG')
        request_id = str(uuid4())
        self.generated_photo_ids.add(request_id)
        captured = event + timedelta(seconds=1)
        return {'request_id': request_id, 'log_type': log_type, 'timestamp': event.isoformat(),
            'device_id': self.device_id, 'app_version': 'integration-0.13',
            'base_checkin': None, 'previous_request_id': previous, 'attendance_mode': 'production',
            'photo': {'image_base64': base64.b64encode(stream.getvalue()).decode(), 'captured_at': captured.isoformat()},
            'location': {'latitude': 24.7136, 'longitude': 46.6753, 'accuracy_m': 5,
                'precise': True, 'is_mocked': False, 'timestamp': captured.isoformat()}}

    def remove_generated_claim_photos(self):
        """Remove only this fixture's random JPEG bytes before SQL rollback."""
        import os
        frappe.set_user('Administrator')
        for request_id in self.generated_photo_ids:
            for row in frappe.get_all('File', filters={'attached_to_doctype': offline.CLAIM,
                    'attached_to_name': request_id}, fields=['name']):
                file = frappe.get_doc('File', row.name)
                if not file.is_private or not file.file_name.startswith('maas-punch-' + request_id):
                    raise RuntimeError('Refuse cleanup of unexpected evidence')
                context = frappe.flags.get('maas_photo_delete')
                frappe.flags.maas_photo_delete = {'doctype': offline.CLAIM, 'name': request_id,
                    'file': file.name, 'url': file.file_url}
                try:
                    path = file.get_full_path()
                    frappe.delete_doc('File', file.name, ignore_permissions=True)
                    self.assertFalse(os.path.isfile(path))
                finally: frappe.flags.maas_photo_delete = context

    def test_automatic_offline_photo_in_out_and_retry_preserve_evidence(self):
        self.prepare_automatic_offline()
        first_payload = self.photo_claim()
        first = offline.submit(self.employee, first_payload, self.device_secret)
        self.assertEqual(first['status'], 'Approved'); self.assertTrue(first['automatically_accepted'])
        self.assertEqual(offline.submit(self.employee, first_payload, self.device_secret)['checkin'], first['checkin'])
        second_payload = self.photo_claim(event=self.event + timedelta(seconds=4),
            previous=first_payload['request_id'], log_type='OUT')
        second = offline.submit(self.employee, second_payload, self.device_secret)
        self.assertEqual(second['status'], 'Approved'); self.assertTrue(second['automatically_accepted'])
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 2)
        for payload, response in ((first_payload, first), (second_payload, second)):
            row = frappe.get_doc(offline.CLAIM, payload['request_id'])
            checkin = frappe.get_doc('Employee Checkin', response['checkin'])
            self.assertTrue(row.punch_photo.startswith('/private/files/'))
            self.assertEqual(row.photo_company, self.company)
            self.assertIsNone(row.reviewed_by)
            self.assertEqual(checkin.time, row.event_time.replace(microsecond=0))
            self.assertEqual(checkin.skip_auto_attendance, 0)
            self.assertEqual(frappe.db.count('File', {'attached_to_doctype': offline.CLAIM,
                'attached_to_name': row.name}), 1)
        self.assertIsNone(frappe.flags.get('maas_offline_auto_accept'))

    def test_automatic_offline_conflict_preserves_private_photo_for_hr(self):
        self.prepare_automatic_offline()
        newer = self.photo_claim(event=self.event + timedelta(seconds=4))
        self.assertEqual(offline.submit(self.employee, newer, self.device_secret)['status'], 'Approved')
        pending_payload = self.photo_claim()
        response = offline.submit(self.employee, pending_payload, self.device_secret)
        self.assertEqual(response['status'], 'Pending Review')
        self.assertIsNone(response['checkin']); self.assertFalse(response['automatically_accepted'])
        self.assertIn('Attendance changed', response['sync_note'])
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 1)
        row = frappe.get_doc(offline.CLAIM, pending_payload['request_id'])
        self.assertTrue(row.punch_photo.startswith('/private/files/'))

    def test_automatic_test_offline_photo_pair_preserves_time_without_hr(self):
        self.prepare_automatic_offline()
        frappe.set_user('Administrator')
        settings = frappe.get_single(attendance.SETTINGS)
        settings.update({'enable_production_attendance': 0,
            'enable_production_offline': 0, 'enable_test_attendance': 1,
            'enable_offline_test': 1,
            'require_test_photo': 1, 'require_test_location': 0,
            'use_company_test_policies': 0, 'test_employee': self.employee.name})
        settings.save()
        frappe.set_user(self.user)
        first_payload = self.photo_claim()
        first_payload['attendance_mode'] = 'test'
        first = offline.submit(self.employee, first_payload, self.device_secret)
        second_payload = self.photo_claim(event=self.event + timedelta(seconds=4),
            previous=first_payload['request_id'], log_type='OUT')
        second_payload['attendance_mode'] = 'test'
        second = offline.submit(self.employee, second_payload, self.device_secret)
        self.assertEqual(frappe.db.count('Employee Checkin', {'employee': self.employee.name}), 2)
        for payload, response in ((first_payload, first), (second_payload, second)):
            self.assertEqual(response['status'], 'Approved')
            self.assertTrue(response['automatically_accepted'])
            row = frappe.get_doc(offline.CLAIM, payload['request_id'])
            checkin = frappe.get_doc('Employee Checkin', response['checkin'])
            self.assertEqual(checkin.time, row.event_time.replace(microsecond=0))
            self.assertEqual(checkin.skip_auto_attendance, 1)
            self.assertIsNone(row.reviewed_by)
            self.assertTrue(row.punch_photo.startswith('/private/files/'))
            self.assertEqual(offline.submit(self.employee, payload, self.device_secret)['checkin'], response['checkin'])
        self.assertIsNone(frappe.flags.get('maas_offline_auto_accept'))
