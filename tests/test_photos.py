import base64
import importlib
import io
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from PIL import Image
import test_attendance
import test_api
import test_offline


def image_payload(stamp, color='blue'):
    buffer = io.BytesIO()
    exif = Image.Exif(); exif[270] = 'private camera metadata'
    Image.new('RGB', (320, 240), color).save(buffer, 'JPEG', exif=exif)
    return {'image_base64': base64.b64encode(buffer.getvalue()).decode(), 'captured_at': stamp.isoformat()}


class PhotoRulesTests(unittest.TestCase):
    def setUp(self):
        self.context = test_api.ApiTests(); self.context.setUp()
        import maas_attendance_mobile.photos
        self.photos = importlib.reload(maas_attendance_mobile.photos)
        self.guard_patch = patch.object(self.photos, 'require_file_guard')
        self.guard_patch.start()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.guard_patch.stop()
        self.context.tearDown()

    def test_jpeg_pixels_validated_and_stored_copy_strips_metadata(self):
        metadata, content, captured = self.photos.parse_photo(image_payload(self.now))
        self.assertEqual(captured, self.now)
        self.assertEqual(len(metadata['sha256']), 64)
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.format, 'JPEG'); self.assertEqual(dict(image.getexif()), {})

    def test_nonimages_png_tiny_large_unknown_fields_and_offsetless_time_rejected(self):
        good = image_payload(self.now)
        for change in ({'image_base64': 'not base64!'}, {'image_base64': base64.b64encode(b'not JPEG').decode()}, {'captured_at': '2026-10-02T12:00:00'}, {'other': True}, {'image_base64': 'A' * (3 * 1024 * 1024)}):
            with self.assertRaises((ValueError, OSError)): self.photos.parse_photo(good | change)
        for format, size in [('PNG', (320, 240)), ('JPEG', (1, 1)), ('JPEG', (2200, 120))]:
            data = io.BytesIO(); Image.new('RGB', size).save(data, format)
            with self.assertRaises(ValueError): self.photos.parse_photo(good | {'image_base64': base64.b64encode(data.getvalue()).decode()})

    def test_start_time_retained_while_gps_follows_camera_capture(self):
        location = {'precise': True, 'is_mocked': False, 'accuracy_m': 5}
        started = self.now - timedelta(seconds=70)
        self.photos.check_timing(started, self.now - timedelta(seconds=10), location, self.now, self.now)
        with self.assertRaises(ValueError): self.photos.check_timing(started - timedelta(minutes=3), self.now, location, self.now, self.now)
        with self.assertRaises(ValueError): self.photos.check_timing(started, self.now - timedelta(seconds=40), location, self.now, self.now)
        old = self.now - timedelta(hours=1)
        self.photos.check_timing(old, old, location, old, self.now, offline=True)
        with self.assertRaises(ValueError): self.photos.check_timing(old, old, location, old, self.now)

    def test_audit_files_cannot_be_public_detached_or_deleted(self):
        self.context.frappe.flags = test_attendance.Flags()
        values = {'attached_to_doctype': 'Mobile Attendance Transaction', 'attached_to_name': 'ID', 'is_private': 1}
        doc = types.SimpleNamespace(get=values.get, get_doc_before_save=lambda: None)
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(doc)
        self.context.frappe.flags.maas_photo_write = {'doctype': values['attached_to_doctype'], 'name': 'ID'}
        self.photos.guard_file(doc)
        values['is_private'] = 0
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(doc)
        values['is_private'] = 1
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(doc, 'on_trash')
        old = types.SimpleNamespace(get=values.get)
        detached = types.SimpleNamespace(get={}.get, get_doc_before_save=lambda: old)
        self.context.frappe.flags.maas_photo_write = None
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(detached)

    def test_storage_is_private_attached_and_restores_service_context(self):
        self.context.frappe.flags = test_attendance.Flags()
        self.context.frappe.db.set_value = Mock()
        utils = types.ModuleType('frappe.utils')
        utils.now_datetime = lambda: '2026-10-02 12:00:00'
        manager = types.ModuleType('frappe.utils.file_manager')
        def save(*args, **kwargs):
            self.assertEqual(kwargs, {'is_private': 1, 'df': 'punch_photo'})
            self.assertEqual(args, ('maas-punch-ID.jpg', b'JPEG', 'Mobile Attendance Transaction', 'ID'))
            self.assertEqual(self.context.frappe.flags.get('maas_photo_write'), {'doctype': 'Mobile Attendance Transaction', 'name': 'ID'})
            return types.SimpleNamespace(is_private=1, file_url='/private/files/photo.jpg')
        manager.save_file = Mock(side_effect=save)
        with patch.dict(sys.modules, {'frappe.utils.file_manager': manager, 'frappe.utils': utils}):
            self.assertEqual(self.photos.save_private('Mobile Attendance Transaction', 'ID', b'JPEG'), '/private/files/photo.jpg')
        self.assertIsNone(self.context.frappe.flags.get('maas_photo_write'))
        self.context.frappe.db.set_value.assert_any_call('Mobile Attendance Transaction', 'ID', 'punch_photo', '/private/files/photo.jpg', update_modified=False)
        self.context.frappe.db.set_value.assert_any_call('Mobile Attendance Transaction', 'ID',
            {'photo_company': 1, 'photo_uploaded_at': '2026-10-02 12:00:00'}, update_modified=False)

    def test_public_storage_result_rejected_before_audit_attachment(self):
        self.context.frappe.flags = test_attendance.Flags()
        self.context.frappe.db.set_value = Mock()
        manager = types.ModuleType('frappe.utils.file_manager')
        manager.save_file = Mock(return_value=types.SimpleNamespace(is_private=0, file_url='/files/photo.jpg'))
        with patch.dict(sys.modules, {'frappe.utils.file_manager': manager}):
            with self.assertRaises(test_api.FakePermissionError): self.photos.save_private('Mobile Attendance Transaction', 'ID', b'JPEG')
        self.context.frappe.db.set_value.assert_not_called()
        self.assertIsNone(self.context.frappe.flags.get('maas_photo_write'))


class PhotoPunchTests(unittest.TestCase):
    def setUp(self):
        self.context = test_attendance.AttendanceTests(); self.context.setUp()
        import maas_attendance_mobile.photos
        self.photos = importlib.reload(maas_attendance_mobile.photos)
        self.context.config['require_test_photo'] = '1'
        self.now = datetime.now(timezone.utc)
        self.context.args['timestamp'] = (self.now - timedelta(seconds=70)).isoformat()
        self.photo = image_payload(self.now - timedelta(seconds=10))
        self.gps = {'latitude': 24.7, 'longitude': 46.7, 'accuracy_m': 5, 'timestamp': self.now.isoformat(), 'precise': True, 'is_mocked': False}

    def tearDown(self): self.context.tearDown()

    def test_online_photo_links_private_receipt_and_uses_button_time(self):
        self.context.config['require_test_location'] = '1'
        with patch.object(self.photos, 'save_private') as save:
            self.context.submit(photo=self.photo, location=self.gps)
        save.assert_called_once()
        checkin, receipt = self.context.created
        self.assertEqual(checkin['time'].second, datetime.fromisoformat(self.context.args['timestamp']).second)
        self.assertEqual(receipt['photo_sha256'], self.photos.parse_photo(self.photo)[0]['sha256'])
        self.assertNotIn('image_base64', str(receipt))

    def test_missing_photo_or_gps_blocks_new_punch(self):
        with self.assertRaisesRegex(ValueError, 'photo is required'): self.context.submit()
        with self.assertRaisesRegex(ValueError, 'GPS reading'): self.context.submit(photo=self.photo)
        self.assertEqual(self.context.created, [])

    def test_unchanged_photo_gps_can_retry_within_online_event_window(self):
        self.context.config['require_test_location'] = '1'
        started = self.now - timedelta(minutes=4)
        self.context.args['timestamp'] = started.isoformat()
        photo = image_payload(started + timedelta(seconds=10))
        gps = self.gps | {'timestamp': (started + timedelta(seconds=15)).isoformat()}
        with patch.object(self.photos, 'save_private'):
            result = self.context.submit(photo=photo, location=gps)
        self.assertFalse(result['duplicate'])

    def test_changed_photo_cannot_reuse_uuid_and_exact_retry_creates_no_file(self):
        from maas_attendance_mobile.punch_rules import parse_request, request_hash
        payload, _ = parse_request(**self.context.args)
        from maas_attendance_mobile.geofence import parse_location
        payload['location'], _ = parse_location(self.gps)
        payload['photo'], _, _ = self.photos.parse_photo(self.photo)
        self.context.receipt = self.context.saved_receipt() | {'request_hash': request_hash(payload, self.context.employee['name'], self.context.frappe.session.user)}
        with patch.object(self.photos, 'save_private') as save:
            self.assertTrue(self.context.submit(photo=self.photo, location=self.gps)['duplicate'])
            save.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'different punch'): self.context.submit(photo=image_payload(self.now - timedelta(seconds=10), 'red'), location=self.gps)


class OfflinePhotoTests(unittest.TestCase):
    def setUp(self):
        self.context = test_offline.OfflineTests(); self.context.setUp()
        import maas_attendance_mobile.photos
        self.photos = importlib.reload(maas_attendance_mobile.photos)

    def tearDown(self): self.context.tearDown()

    def test_delayed_photo_uploaded_privately_without_base64_in_audit_json(self):
        event = datetime.fromisoformat(self.context.request['timestamp'])
        self.context.context.config['require_test_photo'] = '1'
        self.context.request['photo'] = image_payload(event + timedelta(seconds=10))
        self.context.request['location'] = {'latitude': 24.7, 'longitude': 46.7, 'accuracy_m': 5, 'timestamp': (event + timedelta(seconds=15)).isoformat(), 'precise': True, 'is_mocked': False}
        with patch.object(self.photos, 'save_private') as save:
            result = self.context.submit()
        self.assertEqual(result['status'], 'Approved')
        save.assert_called_once()
        self.assertNotIn('image_base64', self.context.doc.payload_json)
        self.assertEqual([r['doctype'] for r in self.context.context.created], ['Mobile Attendance Offline Claim', 'Employee Checkin'])


if __name__ == '__main__': unittest.main()
