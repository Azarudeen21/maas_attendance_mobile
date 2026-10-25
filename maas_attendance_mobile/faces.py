"""Face enrollment and HR test matching (pilot). No checkins are created here."""
import base64
import hashlib
import io
import os
import struct
import threading

import frappe

ENROLLMENT = 'Mobile Attendance Face Enrollment'
SETTINGS = 'Mobile Attendance Settings'
HR_ROLES = {'HR Manager', 'System Manager'}
MODEL_DIR = os.path.join(os.path.dirname(__file__), 'face_models')
DETECTOR = ('face_detection_yunet_2023mar.onnx', '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4')
RECOGNIZER = ('face_recognition_sface_2021dec.onnx', '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79')
MODEL_VERSION = 'yunet-2023mar+sface-2021dec'
DIMENSIONS = 128
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_SIDE = 1280
DETECT_SIDE = 640
MIN_FACE_PX = 64
DETECTION_SCORE = 0.9
DEFAULT_THRESHOLD = 0.363  # OpenCV's published SFace cosine threshold; tune on real staff photos.
DEFAULT_MARGIN = 0.05
MAX_SAMPLES = 5

_engine = None
_lock = threading.Lock()  # OpenCV detector/recognizer objects are not thread-safe.


class FaceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def truthy(value):
    return value in (1, '1', True)


def require_hr():
    if not HR_ROLES.intersection(frappe.get_roles()):
        frappe.throw('HR Manager or System Manager access is required.', frappe.PermissionError)


def limits(site):
    threshold = site.get('face_match_threshold')
    margin = site.get('face_match_margin')
    threshold = DEFAULT_THRESHOLD if threshold in (None, '') else float(threshold)
    margin = DEFAULT_MARGIN if margin in (None, '') else float(margin)
    if not 0.2 <= threshold <= 0.9:
        raise ValueError('Face match threshold must be between 0.2 and 0.9.')
    if not 0 <= margin <= 0.3:
        raise ValueError('Face match margin must be between 0 and 0.3.')
    return threshold, margin


def config():
    # No cached settings: disabling face recognition stops biometric processing immediately.
    site = frappe.db.get_singles_dict(SETTINGS)
    if not truthy(site.get('enable_face_recognition')):
        frappe.throw('Face recognition is disabled in Mobile Attendance Settings.', frappe.PermissionError)
    return limits(site)


def load_image(raw):
    """Decode real JPEG/PNG pixels, apply camera orientation and return a BGR array."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_IMAGE_BYTES:
        raise FaceError('invalid_image', 'Use a JPEG or PNG photo of at most 8 MB.')
    from PIL import Image, ImageOps
    try:
        opened = Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError:
        raise FaceError('invalid_image', 'Photo dimensions are too large.') from None
    except OSError:
        raise FaceError('invalid_image', 'Use a JPEG or PNG photo.') from None
    with opened as image:
        if image.format not in ('JPEG', 'PNG') or image.width * image.height > 40_000_000:
            raise FaceError('invalid_image', 'Use a JPEG or PNG photo.')
        try:
            clean = ImageOps.exif_transpose(image).convert('RGB')
        except OSError:
            raise FaceError('invalid_image', 'The photo file is damaged.') from None
    clean.thumbnail((MAX_SIDE, MAX_SIDE))
    import numpy
    return numpy.ascontiguousarray(numpy.asarray(clean)[:, :, ::-1])  # OpenCV expects BGR.


def _verified(name, digest):
    path = os.path.join(MODEL_DIR, name)
    with open(path, 'rb') as handle:
        if hashlib.sha256(handle.read()).hexdigest() != digest:
            raise RuntimeError('Face model ' + name + ' failed its integrity check. Redeploy the app.')
    return path


def _load():
    global _engine
    if _engine is None:
        import cv2
        detector = cv2.FaceDetectorYN.create(_verified(*DETECTOR), '', (320, 320), DETECTION_SCORE, 0.3, 50)
        recognizer = cv2.FaceRecognizerSF.create(_verified(*RECOGNIZER), '')
        _engine = (detector, recognizer)
    return _engine


def analyse(image, *, enrollment=False):
    """Return (unit embedding, detection score) for the one clear face in the image."""
    import cv2
    # YuNet under-scores large close-ups (typical HR photos), so detect on a smaller copy
    # and map the box/landmarks back to full resolution for alignment.
    scale = min(1.0, DETECT_SIDE / max(image.shape[:2]))
    small = image if scale == 1 else cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)),
                                                interpolation=cv2.INTER_AREA)
    with _lock:
        detector, recognizer = _load()
        detector.setInputSize((small.shape[1], small.shape[0]))
        _, found = detector.detect(small)
        faces = sorted((row.copy() for row in ([] if found is None else found) if row[14] >= DETECTION_SCORE),
                       key=lambda row: row[2] * row[3], reverse=True)
        for row in faces:
            row[:14] /= scale
        if not faces:
            raise FaceError('no_face', 'No clear face was found. Use a well-lit, front-facing photo.')
        main = faces[0]
        if min(main[2], main[3]) < MIN_FACE_PX:
            raise FaceError('face_too_small', 'The face is too small. Move closer or crop the photo.')
        # Enrollment must be unambiguous; matching tolerates small background faces only.
        if len(faces) > 1 and (enrollment or faces[1][2] * faces[1][3] >= 0.5 * main[2] * main[3]):
            raise FaceError('multiple_faces', 'More than one face was found. Capture one person at a time.')
        vector = [float(value) for value in recognizer.feature(recognizer.alignCrop(image, main)).flatten()]
    return normalise(vector), float(main[14])


def normalise(vector):
    norm = sum(value * value for value in vector) ** 0.5
    if len(vector) != DIMENSIONS or not norm:
        raise FaceError('invalid_embedding', 'The face could not be measured.')
    return [value / norm for value in vector]


def encode(vector):
    return base64.b64encode(struct.pack('<%df' % DIMENSIONS, *vector)).decode()


def decode(text):
    raw = base64.b64decode(text or '', validate=True)
    if len(raw) != DIMENSIONS * 4:
        raise ValueError('Stored face embedding has the wrong size.')
    return struct.unpack('<%df' % DIMENSIONS, raw)


def decide(scores, threshold, margin):
    """scores maps employee to best cosine similarity. Never guesses between close candidates."""
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < threshold:
        return 'no_match', ranked
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
        return 'ambiguous', ranked
    return 'match', ranked


def rank(vector, company=None):
    """Best score per ACTIVE employee among enabled current-model enrollments."""
    filters = {'enabled': 1, 'model_version': MODEL_VERSION}
    if company:
        filters['company'] = company
    rows = frappe.get_list(ENROLLMENT, filters=filters, fields=['employee', 'embedding'], limit_page_length=0)
    active = {row['name']: row for row in frappe.get_all('Employee',
        filters={'name': ['in', sorted({row['employee'] for row in rows})], 'status': 'Active'},
        fields=['name', 'employee_name', 'company'], limit_page_length=0)} if rows else {}
    scores = {}
    for row in rows:
        employee = active.get(row['employee'])
        if not employee or (company and employee['company'] != company):
            continue  # Left employees and company moves never match.
        try:
            stored = decode(row['embedding'])
        except ValueError:
            continue
        score = sum(a * b for a, b in zip(stored, vector))
        scores[row['employee']] = max(score, scores.get(row['employee'], -1.0))
    return scores, active


def _upload_bytes(image_base64):
    if not isinstance(image_base64, str) or len(image_base64) > (MAX_UPLOAD_BYTES + 2) // 3 * 4:
        frappe.throw('Use a photo of at most 4 MB.', frappe.ValidationError)
    try:
        return base64.b64decode(image_base64, validate=True)
    except ValueError:
        frappe.throw('The photo upload is not valid base64.', frappe.ValidationError)


def test_match(image_base64, company=None):
    """HR diagnostic: the photo is analysed in memory and never stored."""
    require_hr()
    threshold, margin = config()
    raw = _upload_bytes(image_base64)
    if company and not frappe.db.exists('Company', company):
        frappe.throw('Select a valid company.', frappe.ValidationError)
    reply = {'api_version': 1, 'threshold': threshold, 'margin': margin, 'model_version': MODEL_VERSION,
             'face_score': None, 'candidates': [], 'message': None}
    try:
        vector, face_score = analyse(load_image(raw))
    except FaceError as error:
        return dict(reply, decision=error.code, message=str(error))
    scores, employees = rank(vector, company)
    decision, ranked = decide(scores, threshold, margin)
    return dict(reply, decision=decision, face_score=round(face_score, 3), compared=len(scores),
        candidates=[{'employee': name, 'employee_name': employees[name]['employee_name'],
                     'score': round(score, 4)} for name, score in ranked[:3]])


def _readable_file(url):
    if not isinstance(url, str) or not url.startswith(('/files/', '/private/files/')):
        raise FaceError('invalid_image', 'The photo must be uploaded to this site.')
    for name in frappe.get_all('File', filters={'file_url': url}, pluck='name', limit_page_length=5):
        file = frappe.get_doc('File', name)
        if frappe.has_permission('File', 'read', doc=file):
            content = file.get_content()
            return content.encode() if isinstance(content, str) else content
    raise FaceError('invalid_image', 'The photo file is missing or not readable.')


def validate_enrollment(doc):
    require_hr()
    old = doc.get_doc_before_save()
    if old and old.employee != doc.employee:
        frappe.throw('Enrollment employee cannot be changed. Create a new enrollment.')
    if old:
        doc.source = old.source
    else:
        doc.source = 'Employee Photo' if frappe.flags.get('maas_face_employee_photo') == doc.employee else 'Uploaded Photo'
    employee = frappe.db.get_value('Employee', doc.employee, ['employee_name', 'company', 'status'], as_dict=True)
    if not employee:
        frappe.throw('Select a valid employee.')
    doc.employee_name, doc.company = employee['employee_name'], employee['company']
    derived = ('embedding', 'face_score', 'image_sha256', 'model_version', 'enrolled_by', 'enrolled_at')
    if old is None or old.photo != doc.photo or any(doc.get(key) != old.get(key) for key in derived[:4]):
        # Embeddings are always derived on the server from the attached private photo.
        if not old and employee['status'] != 'Active':
            frappe.throw('Only active employees can be enrolled.')
        config()
        if not isinstance(doc.photo, str) or not doc.photo.startswith('/private/files/'):
            frappe.throw('Attach the face photo as a private file.')
        try:
            raw = _readable_file(doc.photo)
            vector, score = analyse(load_image(raw), enrollment=True)
        except FaceError as error:
            frappe.throw(str(error))
        from frappe.utils import now_datetime
        doc.embedding, doc.face_score = encode(vector), round(score, 4)
        doc.image_sha256, doc.model_version = hashlib.sha256(raw).hexdigest(), MODEL_VERSION
        doc.enrolled_by, doc.enrolled_at = frappe.session.user, now_datetime()
    else:
        for key in derived:
            doc.set(key, old.get(key))
    if not truthy(doc.enabled):
        return
    if not truthy(doc.consent_confirmed):
        frappe.throw("Confirm that the employee's consent to face recognition is on file.")
    others = frappe.get_all(ENROLLMENT, filters={'employee': doc.employee, 'enabled': 1, 'name': ['!=', doc.name or '']},
        fields=['name', 'image_sha256'], limit_page_length=MAX_SAMPLES)
    if any(row['image_sha256'] == doc.image_sha256 for row in others):
        frappe.throw('This photo is already enrolled for the employee.')
    if len(others) >= MAX_SAMPLES:
        frappe.throw('An employee can have at most %d enabled face photos. Disable an old one first.' % MAX_SAMPLES)


def enroll_from_employee_photos(company, consent_confirmed):
    require_hr()
    config()
    if not truthy(consent_confirmed):
        frappe.throw("Confirm that employees' consent to face recognition is on file.")
    if not company or not frappe.db.exists('Company', company):
        frappe.throw('Select a valid company.', frappe.ValidationError)
    frappe.enqueue('maas_attendance_mobile.faces.run_employee_photo_enrollment', queue='long', timeout=3600,
        job_id='maas-face-enrollment-' + company, deduplicate=True, company=company, user=frappe.session.user)
    return {'api_version': 1, 'queued': True}


def run_employee_photo_enrollment(company, user):
    rows = frappe.get_all('Employee', filters={'company': company, 'status': 'Active', 'image': ['is', 'set']},
        fields=['name', 'image'], limit_page_length=0)
    summary = {'company': company, 'employees': len(rows), 'enrolled': 0, 'unchanged': 0, 'failed': []}
    for row in rows:
        try:
            summary[enroll_employee_photo(row)] += 1
            frappe.db.commit()
        except Exception as error:
            frappe.db.rollback()
            summary['failed'].append({'employee': row['name'], 'reason': str(error)[:200]})
    frappe.publish_realtime('maas_face_enrollment_done', summary, user=user)
    return summary


def enroll_employee_photo(row):
    """Copy the current Employee photo privately; it replaces older Employee Photo samples."""
    raw = _readable_file(row['image'])
    digest = hashlib.sha256(raw).hexdigest()
    if frappe.db.exists(ENROLLMENT, {'employee': row['name'], 'image_sha256': digest, 'enabled': 1}):
        return 'unchanged'
    analyse(load_image(raw), enrollment=True)  # Fail before storing an unusable copy.
    for name in frappe.get_all(ENROLLMENT, filters={'employee': row['name'], 'source': 'Employee Photo', 'enabled': 1}, pluck='name'):
        old = frappe.get_doc(ENROLLMENT, name)
        old.enabled = 0
        old.save()
    from frappe.utils.file_manager import save_file
    extension = '.jpg' if raw[:3] == b'\xff\xd8\xff' else '.png'
    copy = save_file('maas-face-' + row['name'] + extension, raw, None, None, is_private=1)
    previous = frappe.flags.get('maas_face_employee_photo')
    frappe.flags.maas_face_employee_photo = row['name']
    try:
        doc = frappe.get_doc({'doctype': ENROLLMENT, 'employee': row['name'], 'photo': copy.file_url,
            'consent_confirmed': 1, 'enabled': 1}).insert()
    finally:
        frappe.flags.maas_face_employee_photo = previous
    if not frappe.db.get_value('File', copy.name, 'attached_to_name'):
        frappe.db.set_value('File', copy.name, {'attached_to_doctype': ENROLLMENT,
            'attached_to_name': doc.name, 'attached_to_field': 'photo'})
    return 'enrolled'
