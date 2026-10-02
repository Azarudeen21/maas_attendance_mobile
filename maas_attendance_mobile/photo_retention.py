"""Opt-in, company-scoped photo cleanup with a durable deletion intent."""
from datetime import datetime, timedelta

import frappe

from maas_attendance_mobile.photos import AUDIT_TYPES, PHOTO_FIELD

POLICY = 'Mobile Attendance Company Policy'
JOB = 'Mobile Attendance Photo Cleanup'


def retention_days(value):
    try:
        days = int(str(value or 0))
    except (ValueError, TypeError):
        raise ValueError('Photo retention must be a whole number of days.') from None
    if not 0 <= days <= 3650:
        raise ValueError('Photo retention must be between 0 and 3650 days. Zero keeps photos.')
    return days


def _now():
    from frappe.utils import now_datetime
    return now_datetime()


def _datetime(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def due(record, policy, now):
    days = retention_days(policy.get('photo_retention_days'))
    if not days or not record.get(PHOTO_FIELD) or record.get('photo_retention_hold') in (1, '1', True):
        return False
    if record.get('photo_removed_at') or not record.get('photo_company') or not record.get('photo_uploaded_at'):
        return False
    if record.get('doctype') == AUDIT_TYPES[1] and record.get('status') not in ('Approved', 'Rejected'):
        return False
    # Offline review starts an additional full retention interval. Pending
    # claims and newly reviewed evidence cannot expire just because upload is old.
    anchor = _datetime(record['photo_uploaded_at'])
    if record.get('doctype') == AUDIT_TYPES[1]:
        if not record.get('reviewed_at'):
            return False
        anchor = max(anchor, _datetime(record['reviewed_at']))
    return anchor + timedelta(days=days) <= now


def _reviewer():
    if frappe.session.user == 'Guest' or not {'HR Manager', 'System Manager'}.intersection(frappe.get_roles()):
        frappe.throw('HR Manager or System Manager access is required.', frappe.PermissionError)


def preview(company):
    _reviewer()
    policy = frappe.get_doc(POLICY, company)
    policy.check_permission('read')
    now = _now()
    counts = {'eligible': 0, 'held': 0, 'awaiting_review': 0, 'kept': 0}
    for doctype in AUDIT_TYPES:
        # Permission-scoped pages; no photos, paths, employee names or GPS are
        # returned. Company is the immutable company at upload, not a transfer.
        start = 0
        while True:
            rows = frappe.get_list(doctype, filters={'photo_company': company, PHOTO_FIELD: ['is', 'set']},
                fields=['name', PHOTO_FIELD, 'photo_company', 'photo_uploaded_at', 'photo_retention_hold',
                        'photo_removed_at', *(['status', 'reviewed_at'] if doctype == AUDIT_TYPES[1] else [])],
                order_by='name asc', start=start, page_length=100)
            for row in rows:
                row['doctype'] = doctype
                if row.get('photo_retention_hold') in (1, '1', True):
                    counts['held'] += 1
                elif doctype == AUDIT_TYPES[1] and row.get('status') == 'Pending Review':
                    counts['awaiting_review'] += 1
                elif due(row, policy.as_dict(), now):
                    counts['eligible'] += 1
                else:
                    counts['kept'] += 1
            if len(rows) < 100:
                break
            start += 100
    return {'api_version': 1, 'company': company, 'retention_days': retention_days(policy.get('photo_retention_days')),
            'automatic_cleanup_enabled': policy.get('enable_photo_cleanup') in (1, '1', True), 'counts': counts}


def set_hold(doctype, name, hold, reason):
    _reviewer()
    if doctype not in AUDIT_TYPES or str(hold) not in ('0', '1'):
        frappe.throw('Select a punch receipt or offline claim and a valid hold.', frappe.ValidationError)
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        frappe.throw('Enter a hold reason between 5 and 500 characters.', frappe.ValidationError)
    frappe.db.get_value(doctype, name, 'name', for_update=True)
    record = frappe.get_doc(doctype, name)
    record.check_permission('read')
    if not record.get(PHOTO_FIELD) or record.get('photo_removed_at'):
        frappe.throw('This record has no retained photo.', frappe.ValidationError)
    frappe.db.set_value(doctype, name, {'photo_retention_hold': int(hold),
        'photo_hold_reason': reason.strip(), 'photo_hold_by': frappe.session.user, 'photo_hold_at': _now()})
    # Each change is preserved as a Comment, in addition to current hold fields.
    record.add_comment('Comment', text=('Photo retention hold added: ' if int(hold) else 'Photo retention hold released: ') + reason.strip())
    return {'api_version': 1, 'hold': bool(int(hold))}


def run():
    """Hourly scheduler: finish existing intents, then plan at most 100 more.

    New intents commit before a later run touches storage. A database rollback
    after filesystem removal leaves the original Pending intent for recovery.
    No new intent can remove bytes in its own planning transaction.
    """
    for row in frappe.get_all(JOB, filters={'status': 'Pending'}, fields=['name'],
                             order_by='modified asc', limit_page_length=100):
        frappe.db.savepoint('maas_photo_cleanup')
        try:
            _finish(row['name'])
        except Exception:
            # A broken storage attachment must not block the rest of the queue.
            # The previously committed intent survives this rollback, including
            # the rare case where bytes were removed before database failure.
            frappe.db.rollback(save_point='maas_photo_cleanup')
            frappe.log_error(title='MAAS photo cleanup needs retry',
                             message='Cleanup audit ' + row['name'] + ' remains Pending. Inspect storage and retry.')
        # Rotate held/shared intents so they cannot starve later work.
        frappe.db.set_value(JOB, row['name'], 'last_attempt_at', _now())
    remaining = 100
    for policy in frappe.get_all(POLICY, filters={'enable_photo_cleanup': 1},
                                 fields=['company', 'photo_retention_days']):
        days = retention_days(policy.get('photo_retention_days'))
        if not days:
            continue
        for doctype in AUDIT_TYPES:
            cutoff = _now() - timedelta(days=days)
            filters = {'photo_company': policy['company'],
                PHOTO_FIELD: ['is', 'set'], 'photo_retention_hold': 0,
                'photo_cleanup': ['is', 'not set'],
                'photo_removed_at': ['is', 'not set'],
                'photo_uploaded_at': ['<=', cutoff]}
            if doctype == AUDIT_TYPES[1]:
                filters.update(status=['in', ['Approved', 'Rejected']], reviewed_at=['<=', cutoff])
            rows = frappe.get_all(doctype, filters=filters,
                fields=['name', PHOTO_FIELD, 'photo_company', 'photo_uploaded_at', 'photo_retention_hold',
                        'photo_removed_at', *(['status', 'reviewed_at'] if doctype == AUDIT_TYPES[1] else [])],
                order_by='photo_uploaded_at asc', limit_page_length=100)
            for row in rows:
                row['doctype'] = doctype
                job_name = ('online-' if doctype == AUDIT_TYPES[0] else 'offline-') + row['name']
                if not due(row, policy, _now()) or frappe.db.exists(JOB, job_name):
                    continue
                previous = frappe.flags.get('maas_photo_cleanup_write')
                frappe.flags.maas_photo_cleanup_write = job_name
                try:
                    frappe.get_doc({'doctype': JOB, 'name': job_name, 'source_doctype': doctype,
                        'source_name': row['name'], 'company': row['photo_company'],
                        'photo_url': row[PHOTO_FIELD], 'status': 'Pending'}).insert(ignore_permissions=True)
                    frappe.db.set_value(doctype, row['name'], 'photo_cleanup', job_name, update_modified=False)
                finally:
                    frappe.flags.maas_photo_cleanup_write = previous
                remaining -= 1
                if not remaining:
                    return


def _finish(name):
    frappe.db.get_value(JOB, name, 'name', for_update=True)
    job = frappe.get_doc(JOB, name)
    if job.status != 'Pending' or job.source_doctype not in AUDIT_TYPES:
        return
    # Same row lock as hold changes. Recheck policy immediately before deleting.
    frappe.db.get_value(job.source_doctype, job.source_name, 'name', for_update=True)
    record = frappe.get_doc(job.source_doctype, job.source_name)
    policy = frappe.db.get_value(POLICY, job.company,
        ['enable_photo_cleanup', 'photo_retention_days'], as_dict=True, for_update=True)
    if not policy or policy.get('enable_photo_cleanup') not in (1, '1', True) or not due(record.as_dict(), policy, _now()):
        return
    if record.get('photo_company') != job.company or record.get(PHOTO_FIELD) != job.photo_url:
        return
    files = frappe.get_all('File', filters={'file_url': job.photo_url},
        fields=['name', 'is_private', 'attached_to_doctype', 'attached_to_name', 'attached_to_field', 'content_hash'])
    # Shared URLs, unknown references and malformed/missing File records need
    # administrator review; never remove someone else's attachment.
    if len(files) != 1:
        return
    file = files[0]
    if (file.get('is_private') not in (1, '1', True) or not job.photo_url.startswith('/private/files/')
        or file.get('attached_to_doctype') != job.source_doctype or file.get('attached_to_name') != job.source_name
        or file.get('attached_to_field') != PHOTO_FIELD or not file.get('content_hash')):
        return
    if frappe.db.exists('File', {'content_hash': file['content_hash'], 'name': ['!=', file['name']]}):
        return
    context = {'doctype': job.source_doctype, 'name': job.source_name, 'file': file['name'], 'url': job.photo_url}
    from maas_attendance_mobile.photos import require_file_guard
    require_file_guard()
    previous = frappe.flags.get('maas_photo_delete')
    frappe.flags.maas_photo_delete = context
    try:
        frappe.delete_doc('File', file['name'], ignore_permissions=True)
        stamp = _now()
        frappe.db.set_value(job.source_doctype, job.source_name,
            {PHOTO_FIELD: None, 'photo_removed_at': stamp, 'photo_cleanup': name}, update_modified=False)
        frappe.db.set_value(JOB, name, {'status': 'Completed', 'completed_at': stamp}, update_modified=False)
    finally:
        frappe.flags.maas_photo_delete = previous
