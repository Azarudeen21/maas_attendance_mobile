"""Private punch photo evidence. No face recognition or liveness claims."""
import base64
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import frappe

MAX_BYTES = 2 * 1024 * 1024
PHOTO_FIELD = 'punch_photo'
AUDIT_TYPES = ('Mobile Attendance Transaction', 'Mobile Attendance Offline Claim')


def parse_photo(value):
    if isinstance(value, str):
        if len(value) > 3 * 1024 * 1024: raise ValueError('Photo is too large.')
        value = json.loads(value)
    if not isinstance(value, dict) or set(value) != {'image_base64', 'captured_at'}:
        raise ValueError('A camera photo and capture time are required.')
    encoded = value['image_base64']
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ValueError('Photo exceeds 2 MB.')
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > MAX_BYTES: raise ValueError('Invalid photo size.')
    stamp = value['captured_at']
    if not isinstance(stamp, str) or len(stamp) > 40: raise ValueError('Invalid photo capture time.')
    captured = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    if captured.tzinfo is None: raise ValueError('Photo capture time needs a timezone offset.')
    captured = captured.astimezone(timezone.utc)
    from PIL import Image, ImageOps
    try:
        decoded = Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError:
        raise ValueError('Photo dimensions are too large.') from None
    with decoded as image:
        if image.format != 'JPEG' or not 120 <= min(image.size) or max(image.size) > 2048 or image.width * image.height > 4_000_000:
            raise ValueError('Use a camera JPEG between 120 and 2048 pixels per side.')
        image.load()  # Validate actual pixels, not a filename or MIME claim.
        clean = ImageOps.exif_transpose(image).convert('RGB')
        clean.thumbnail((1024, 1024))
        output = io.BytesIO()
        clean.save(output, format='JPEG', quality=85)  # Strip EXIF/location metadata.
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'captured_at': captured.isoformat(timespec='microseconds').replace('+00:00', 'Z')}, output.getvalue(), captured


def check_timing(event, captured, location, sampled_at, now, *, offline=False):
    if captured < event or captured > event + timedelta(minutes=2):
        raise ValueError('Take the photo within two minutes of pressing the punch button.')
    if not location or not location['precise'] or location['is_mocked'] or location['accuracy_m'] <= 0:
        raise ValueError('Capture a precise GPS reading after the photo.')
    if sampled_at < captured - timedelta(seconds=5) or sampled_at > captured + timedelta(seconds=30):
        raise ValueError('GPS must be captured with the original photo.')
    # Online photo evidence remains tied to the original event for its 15-minute
    # retry window. GPS-only requests still use the stricter live 30-second rule.
    if not offline and (sampled_at < now - timedelta(minutes=15) or sampled_at > now + timedelta(seconds=5)):
        raise ValueError('The original GPS reading is outside the online retry window.')


def save_private(doctype, name, content):
    from frappe.utils.file_manager import save_file
    previous = frappe.flags.get('maas_photo_write')
    frappe.flags.maas_photo_write = {'doctype': doctype, 'name': name}
    try:
        file = save_file('maas-punch-' + name + '.jpg', content, doctype, name, is_private=1, df=PHOTO_FIELD)
        if not file.is_private or not file.file_url.startswith('/private/files/'):
            frappe.throw('Photo storage must remain private.', frappe.PermissionError)
        frappe.db.set_value(doctype, name, PHOTO_FIELD, file.file_url, update_modified=False)
        return file.file_url
    finally:
        frappe.flags.maas_photo_write = previous


def guard_file(doc, method=None):
    old = doc.get_doc_before_save()
    references = [(doc.get('attached_to_doctype'), doc.get('attached_to_name'))]
    if old: references.append((old.get('attached_to_doctype'), old.get('attached_to_name')))
    for doctype, name in references:
        if doctype not in AUDIT_TYPES: continue
        context = frappe.flags.get('maas_photo_write')
        if method == 'on_trash' or context != {'doctype': doctype, 'name': name} or not doc.get('is_private'):
            frappe.throw('Punch evidence files are managed by the attendance service.', frappe.PermissionError)
