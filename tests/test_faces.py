import base64
import importlib
import io
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import test_api
import test_device_controller
from test_device_controller import Document


def jpeg(size=(320, 240), color='blue', exif_orientation=None):
    buffer = io.BytesIO()
    exif = Image.Exif()
    if exif_orientation: exif[274] = exif_orientation
    Image.new('RGB', size, color).save(buffer, 'JPEG', exif=exif)
    return buffer.getvalue()


def unit(index):
    vector = [0.0] * 128
    vector[index] = 1.0
    return vector


def blend(a, b, weight):
    return [weight * x + (1 - weight) * y for x, y in zip(a, b)]


class FaceTestCase(unittest.TestCase):
    def setUp(self):
        self.base = test_device_controller.DeviceControllerTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.site = {'enable_face_recognition': 1, 'face_match_threshold': 0.363, 'face_match_margin': 0.05}
        self.frappe.db.get_singles_dict = Mock(side_effect=lambda doctype: self.site)
        self.frappe.db.exists = Mock(return_value=True)
        import maas_attendance_mobile.faces
        self.faces = importlib.reload(maas_attendance_mobile.faces)

    def tearDown(self): self.base.tearDown()


class FaceRulesTests(FaceTestCase):
    def test_decide_requires_threshold_and_clear_lead(self):
        self.assertEqual(self.faces.decide({}, 0.363, 0.05)[0], 'no_match')
        self.assertEqual(self.faces.decide({'A': 0.30, 'B': 0.10}, 0.363, 0.05)[0], 'no_match')
        decision, ranked = self.faces.decide({'B': 0.41, 'A': 0.72}, 0.363, 0.05)
        self.assertEqual((decision, ranked[0][0]), ('match', 'A'))
        self.assertEqual(self.faces.decide({'A': 0.72, 'B': 0.70}, 0.363, 0.05)[0], 'ambiguous')
        self.assertEqual(self.faces.decide({'A': 0.72, 'B': 0.70}, 0.363, 0)[0], 'match')

    def test_limits_reject_unsafe_settings_and_keep_zero_margin(self):
        self.assertEqual(self.faces.limits({}), (0.363, 0.05))
        self.assertEqual(self.faces.limits({'face_match_threshold': '0.5', 'face_match_margin': 0}), (0.5, 0.0))
        for bad in ({'face_match_threshold': 0.1}, {'face_match_threshold': 0.95}, {'face_match_margin': -0.1}, {'face_match_margin': 0.5}):
            with self.assertRaises(ValueError): self.faces.limits(bad)

    def test_embedding_round_trip_and_size_check(self):
        vector = self.faces.normalise([float(i + 1) for i in range(128)])
        decoded = self.faces.decode(self.faces.encode(vector))
        self.assertAlmostEqual(sum(a * b for a, b in zip(decoded, vector)), 1.0, places=5)
        for bad in ('', base64.b64encode(b'x' * 12).decode(), 'not base64!'):
            with self.assertRaises(ValueError): self.faces.decode(bad)
        with self.assertRaises(ValueError): self.faces.normalise([0.0] * 128)

    def test_load_image_accepts_jpeg_png_with_orientation_and_rejects_others(self):
        image = self.faces.load_image(jpeg((320, 240), exif_orientation=6))
        self.assertEqual(image.shape, (320, 240, 3))  # Rotated upright, BGR channels.
        png = io.BytesIO(); Image.new('RGB', (200, 200)).save(png, 'PNG')
        self.assertEqual(self.faces.load_image(png.getvalue()).shape, (200, 200, 3))
        gif = io.BytesIO(); Image.new('RGB', (200, 200)).save(gif, 'GIF')
        for raw in (b'', b'not an image', gif.getvalue(), 'text', b'x' * (8 * 1024 * 1024 + 1)):
            with self.assertRaises(self.faces.FaceError): self.faces.load_image(raw)
        large = self.faces.load_image(jpeg((3000, 1500)))
        self.assertEqual(large.shape[:2], (640, 1280))


class FaceMatchTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.frappe.get_roles.return_value = ['HR Manager']
        self.enrollments = [
            {'employee': 'EMP-A', 'embedding': self.faces.encode(unit(0))},
            {'employee': 'EMP-A', 'embedding': self.faces.encode(self.faces.normalise(blend(unit(0), unit(1), 0.6)))},
            {'employee': 'EMP-B', 'embedding': self.faces.encode(unit(1))},
            {'employee': 'EMP-LEFT', 'embedding': self.faces.encode(unit(2))},
            {'employee': 'EMP-C', 'embedding': 'corrupt'},
        ]
        self.employees = [
            {'name': 'EMP-A', 'employee_name': 'Alice', 'company': 'Test Company'},
            {'name': 'EMP-B', 'employee_name': 'Bilal', 'company': 'Other Company'},
            {'name': 'EMP-C', 'employee_name': 'Chen', 'company': 'Test Company'},
        ]
        self.frappe.get_list = Mock(side_effect=lambda doctype, **kwargs: self.enrollments)
        self.frappe.get_all = Mock(side_effect=lambda doctype, **kwargs: self.employees)
        self.photo = base64.b64encode(jpeg()).decode()

    def match(self, vector, company=None, error=None):
        analyse = Mock(side_effect=error) if error else Mock(return_value=(vector, 0.93))
        with patch.object(self.faces, 'analyse', analyse):
            return self.faces.test_match(self.photo, company)

    def test_best_sample_per_active_employee_wins(self):
        result = self.match(unit(0))
        self.assertEqual(result['decision'], 'match')
        self.assertEqual([row['employee'] for row in result['candidates']], ['EMP-A', 'EMP-B'])
        self.assertEqual(result['candidates'][0]['employee_name'], 'Alice')
        self.assertEqual(result['compared'], 2)  # Left employee and corrupt embedding are skipped.
        filters = self.frappe.get_list.call_args.kwargs['filters']
        self.assertEqual(filters, {'enabled': 1, 'model_version': self.faces.MODEL_VERSION})
        employee_filters = self.frappe.get_all.call_args.kwargs['filters']
        self.assertEqual(employee_filters['status'], 'Active')

    def test_unknown_face_and_close_scores_never_guess(self):
        self.assertEqual(self.match(unit(5))['decision'], 'no_match')
        between = self.faces.normalise(blend(unit(0), unit(1), 0.5))
        self.site['face_match_margin'] = 0.3
        self.assertEqual(self.match(between)['decision'], 'ambiguous')

    def test_company_filter_uses_current_employee_company(self):
        result = self.match(unit(1), company='Test Company')
        self.assertEqual(self.frappe.get_list.call_args.kwargs['filters']['company'], 'Test Company')
        self.assertNotIn('EMP-B', [row['employee'] for row in result['candidates']])

    def test_detection_problems_are_reported_without_matching(self):
        result = self.match(None, error=self.faces.FaceError('multiple_faces', 'More than one face.'))
        self.assertEqual((result['decision'], result['candidates']), ('multiple_faces', []))
        self.frappe.get_list.assert_not_called()

    def test_role_gate_and_upload_limits(self):
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.match(unit(0))
        self.frappe.get_roles.return_value = ['System Manager']
        self.site['enable_face_recognition'] = 0
        with self.assertRaises(test_api.FakePermissionError): self.match(unit(0))
        self.site['enable_face_recognition'] = '1'
        for bad in ('not base64!', 'A' * (6 * 1024 * 1024), None):
            self.photo = bad
            with self.assertRaises(ValueError): self.match(unit(0))
        self.photo = base64.b64encode(jpeg()).decode()
        self.frappe.db.exists = Mock(return_value=False)
        with self.assertRaises(ValueError): self.match(unit(0), company='Missing')

    def test_bulk_enrollment_requires_consent_and_queues_one_job_per_company(self):
        self.frappe.enqueue = Mock()
        with self.assertRaises(ValueError): self.faces.enroll_from_employee_photos('Test Company', 0)
        self.assertEqual(self.faces.enroll_from_employee_photos('Test Company', 1)['queued'], True)
        kwargs = self.frappe.enqueue.call_args.kwargs
        self.assertEqual((kwargs['company'], kwargs['user'], kwargs['deduplicate']), ('Test Company', 'hr@example.test', True))


class FaceEnrollmentControllerTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        module = importlib.import_module('maas_attendance_mobile.maas_attendance_mobile.doctype.mobile_attendance_face_enrollment.mobile_attendance_face_enrollment')
        self.controller = importlib.reload(module)
        self.doc = self.controller.MobileAttendanceFaceEnrollment()
        for key, value in dict(new=True, before=None, name='MAFE-00001', employee='EMP-A', photo='/private/files/face.jpg',
                               enabled=1, consent_confirmed=1, source='Employee Photo').items():
            setattr(self.doc, key, value)
        self.employee = {'employee_name': 'Alice', 'company': 'Test Company', 'status': 'Active'}
        self.frappe.db.get_value = Mock(side_effect=lambda doctype, name, fields, **kw: self.employee)
        self.others = []
        self.frappe.get_all = Mock(side_effect=lambda doctype, **kwargs: self.others)
        self.raw = jpeg()
        self.analyse = patch.object(self.faces, 'analyse', Mock(return_value=(unit(3), 0.95)))
        self.reader = patch.object(self.faces, '_readable_file', Mock(side_effect=lambda url: self.raw))
        self.analyse.start(); self.reader.start()

    def tearDown(self):
        self.analyse.stop(); self.reader.stop()
        super().tearDown()

    def saved(self):
        old = Document()
        old.__dict__.update({key: value for key, value in self.doc.__dict__.items() if key not in ('before', 'new')})
        self.doc.new, self.doc.before = False, old
        return old

    def test_new_enrollment_derives_identity_and_embedding_on_server(self):
        self.doc.embedding = 'client supplied'; self.doc.company = 'Spoofed'
        self.doc.validate()
        self.assertEqual((self.doc.company, self.doc.employee_name, self.doc.source), ('Test Company', 'Alice', 'Uploaded Photo'))
        self.assertEqual(self.faces.decode(self.doc.embedding)[3], 1.0)
        self.assertEqual(self.doc.model_version, self.faces.MODEL_VERSION)
        self.assertEqual(self.doc.enrolled_by, 'hr@example.test')
        self.assertEqual(len(self.doc.image_sha256), 64)

    def test_employee_photo_source_only_through_bulk_flag(self):
        self.frappe.flags.maas_face_employee_photo = 'EMP-A'
        self.doc.validate()
        self.assertEqual(self.doc.source, 'Employee Photo')

    def test_tampered_embedding_is_recomputed_and_unchanged_save_keeps_audit(self):
        self.doc.validate(); self.saved()
        self.faces.analyse.reset_mock()
        self.doc.enabled = 0; self.doc.enrolled_by = 'someone@example.test'
        self.doc.validate()
        self.faces.analyse.assert_not_called()
        self.assertEqual(self.doc.enrolled_by, 'hr@example.test')
        self.doc.embedding = self.faces.encode(unit(9))
        self.doc.validate()
        self.faces.analyse.assert_called_once()
        self.assertEqual(self.faces.decode(self.doc.embedding)[3], 1.0)

    def test_disabling_does_not_need_the_pilot_gate_but_new_processing_does(self):
        self.doc.validate(); self.saved()
        self.site['enable_face_recognition'] = 0
        self.doc.enabled = 0
        self.doc.validate()
        self.doc.photo = '/private/files/other.jpg'
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()

    def test_rules_for_employee_photo_consent_duplicates_and_limit(self):
        self.doc.validate(); self.saved()
        self.doc.employee = 'EMP-B'
        with self.assertRaisesRegex(ValueError, 'cannot be changed'): self.doc.validate()
        self.doc.employee = 'EMP-A'; self.doc.consent_confirmed = 0
        with self.assertRaisesRegex(ValueError, 'consent'): self.doc.validate()
        self.doc.consent_confirmed = 1
        self.others = [{'name': 'MAFE-00000', 'image_sha256': self.doc.image_sha256}]
        with self.assertRaisesRegex(ValueError, 'already enrolled'): self.doc.validate()
        self.others = [{'name': 'MAFE-%05d' % i, 'image_sha256': str(i)} for i in range(5)]
        with self.assertRaisesRegex(ValueError, 'at most 5'): self.doc.validate()

    def test_public_photos_inactive_employees_and_non_hr_are_rejected(self):
        self.doc.photo = '/files/face.jpg'
        with self.assertRaisesRegex(ValueError, 'private'): self.doc.validate()
        self.doc.photo = '/private/files/face.jpg'; self.employee['status'] = 'Left'
        with self.assertRaisesRegex(ValueError, 'active'): self.doc.validate()
        self.employee['status'] = 'Active'
        self.faces.analyse.side_effect = self.faces.FaceError('no_face', 'No clear face was found.')
        with self.assertRaisesRegex(ValueError, 'No clear face'): self.doc.validate()
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.doc.validate()
        with self.assertRaises(test_api.FakePermissionError): self.doc.on_trash()


class EmployeeButtonTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.frappe.get_roles.return_value = ['HR Manager']
        self.frappe.has_permission = Mock(return_value=True)
        self.row = {'name': 'EMP-A', 'image': '/files/a.jpg'}
        self.frappe.db.get_value = Mock(side_effect=lambda doctype, name, fields, **kw: self.row)
        self.enroll = patch.object(self.faces, 'enroll_employee_photo', Mock(return_value=('enrolled', 'MAFE-00007')))
        self.enroll.start()

    def tearDown(self):
        self.enroll.stop()
        super().tearDown()

    def test_enrolls_saved_employee_photo_and_reports_outcome(self):
        self.assertEqual(self.faces.enroll_employee('EMP-A', 1),
                         {'api_version': 1, 'outcome': 'enrolled', 'enrollment': 'MAFE-00007'})
        self.faces.enroll_employee_photo.assert_called_once_with(self.row)

    def test_requires_photo_consent_access_hr_role_and_pilot(self):
        with self.assertRaisesRegex(ValueError, 'consent'): self.faces.enroll_employee('EMP-A', 0)
        self.row = {'name': 'EMP-A', 'image': None}
        with self.assertRaisesRegex(ValueError, 'photo'): self.faces.enroll_employee('EMP-A', 1)
        self.row = {'name': 'EMP-A', 'image': '/files/a.jpg'}
        self.frappe.has_permission.return_value = False
        with self.assertRaises(test_api.FakePermissionError): self.faces.enroll_employee('EMP-A', 1)
        self.frappe.has_permission.return_value = True
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.faces.enroll_employee('EMP-A', 1)
        self.frappe.get_roles.return_value = ['HR Manager']
        self.site['enable_face_recognition'] = 0
        with self.assertRaises(test_api.FakePermissionError): self.faces.enroll_employee('EMP-A', 1)
        self.faces.enroll_employee_photo.assert_not_called()

    def test_unusable_photo_shows_the_reason(self):
        self.faces.enroll_employee_photo.side_effect = self.faces.FaceError('no_face', 'No clear face was found.')
        with self.assertRaisesRegex(ValueError, 'No clear face'): self.faces.enroll_employee('EMP-A', 1)


def opencv_available():
    try:
        import cv2, numpy  # noqa: F401
        return hasattr(cv2, 'FaceDetectorYN')
    except ImportError:
        return False


@unittest.skipUnless(opencv_available(), 'OpenCV with FaceDetectorYN is not installed')
class FaceEngineTests(FaceTestCase):
    def test_bundled_models_pass_integrity_check_and_blank_image_has_no_face(self):
        with self.assertRaises(self.faces.FaceError) as caught:
            self.faces.analyse(self.faces.load_image(jpeg((640, 480), 'gray')))
        self.assertEqual(caught.exception.code, 'no_face')

    def test_tampered_model_is_refused(self):
        with patch.object(self.faces, 'RECOGNIZER', (self.faces.RECOGNIZER[0], '0' * 64)):
            with self.assertRaisesRegex(RuntimeError, 'integrity'): self.faces._verified(*self.faces.RECOGNIZER)


if __name__ == '__main__':
    unittest.main()
