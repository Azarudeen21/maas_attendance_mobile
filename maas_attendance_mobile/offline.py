"""Offline punches: immutable evidence, synced into Employee Checkins; HR reviews only conflicts."""
import json
from datetime import datetime, timedelta, timezone

import frappe
from maas_attendance_mobile import attendance, policy
from maas_attendance_mobile.devices import require_approved, DEVICE
from maas_attendance_mobile.geofence import parse_location, evaluate
from maas_attendance_mobile.punch_rules import parse_request, request_hash, uuid4_string, site_time, check_transition

CLAIM = 'Mobile Attendance Offline Claim'
# Set while the sync service finalises a claim without an HR reviewer.
SYNC = 'maas_offline_sync'


def parse_claim(request):
    if isinstance(request, str):
        if len(request) > 3 * 1024 * 1024: raise ValueError('Offline request too large.')
        request = json.loads(request)
    required = {'request_id', 'log_type', 'timestamp', 'device_id', 'app_version', 'base_checkin', 'previous_request_id'}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {'location', 'photo', 'attendance_mode'}:
        raise ValueError('Invalid offline request fields.')
    payload, event = parse_request(*(request[key] for key in ('request_id', 'log_type', 'timestamp', 'device_id', 'app_version')))
    base = request['base_checkin']
    if base is not None and (not isinstance(base, str) or not 1 <= len(base) <= 140):
        raise ValueError('Invalid base checkin.')
    previous = request['previous_request_id']
    if previous is not None:
        uuid4_string(previous)
        if previous == request['request_id']: raise ValueError('Circular offline request.')
    payload.update(base_checkin=base, previous_request_id=previous)
    # App 1.3.x still sends a mode; it no longer changes anything but stays in the hash.
    if request.get('attendance_mode', 'test') not in ('test', 'production'):
        raise ValueError('Invalid attendance mode.')
    if request.get('attendance_mode') == 'production':
        payload['attendance_mode'] = 'production'
    if 'location' in request:
        payload['location'], _ = parse_location(request['location'])
    photo_bytes = None
    if 'photo' in request:
        from maas_attendance_mobile.photos import parse_photo
        payload['photo'], photo_bytes, _ = parse_photo(request['photo'])
    return payload, event, photo_bytes


def check_window(event, now):
    if event < now - timedelta(days=7) or event > now + timedelta(minutes=2):
        raise ValueError('Offline uploads must be within seven days. Ask HR to correct older events manually.')


def reply(row, duplicate=False):
    return {'api_version': 1, 'request_id': row['name'], 'status': row['status'],
            'checkin': row.get('checkin'), 'duplicate': duplicate, 'sync_note': row.get('sync_note')}


def own_status(employee, request_id):
    try: uuid4_string(request_id)
    except ValueError: frappe.throw('Invalid request ID.', frappe.ValidationError)
    row = frappe.db.get_value(CLAIM, {'name': request_id, 'employee': employee['name'], 'user': frappe.session.user},
        ['name', 'status', 'checkin', 'sync_note'], as_dict=True)
    return reply(row) if row else {'api_version': 1, 'request_id': request_id, 'status': 'Not Found', 'checkin': None, 'duplicate': False}


def lock_employee(employee, user):
    row = frappe.db.get_value('Employee', employee, ['user_id', 'status', 'company'], as_dict=True, for_update=True)
    if not row or row['user_id'] != user or row['status'] != 'Active' or not frappe.db.get_value('User', user, 'enabled', for_update=True) or 'Employee' not in frappe.get_roles(user):
        frappe.throw('An active enabled Employee login is required.', frappe.PermissionError)
    return row


def gps_check(payload, event, company, rules):
    if not rules['require_location']: return None
    if not company or 'location' not in payload:
        frappe.throw('GPS evidence and employee company are required.', frappe.ValidationError)
    location, sampled = parse_location(payload['location'])
    workplaces = frappe.get_all('Mobile Attendance Workplace', filters={'company': company, 'enabled': 1},
        fields=['name', 'latitude', 'longitude', 'radius_m', 'max_accuracy_m'])
    # Delayed evidence is compared to the ORIGINAL event, never re-captured at sync.
    anchor = datetime.fromisoformat(payload['photo']['captured_at'].replace('Z', '+00:00')) if payload.get('photo') else event
    try: return evaluate(location, sampled, workplaces, sampled if payload.get('photo') else event, anchor)
    except ValueError as error: frappe.throw(str(error), frappe.ValidationError)


def submit(employee, request, device_secret):
    try: payload, event, photo_bytes = parse_claim(request)
    except (ValueError, TypeError, OverflowError, OSError): frappe.throw('Invalid offline claim.', frappe.ValidationError)
    user = frappe.session.user
    current = lock_employee(employee['name'], user)
    rules = policy.resolve(employee['name'])
    if not rules['offline']:
        frappe.throw('Offline check-in is not enabled for you.', frappe.PermissionError)
    registration = require_approved(employee, payload['device_id'], device_secret)
    fingerprint = request_hash(payload, employee['name'], user)
    old = frappe.db.get_value(CLAIM, payload['request_id'], ['name', 'employee', 'user', 'request_hash', 'status', 'checkin', 'sync_note'], as_dict=True, for_update=True)
    if old:
        if old['employee'] != employee['name'] or old['user'] != user or old['request_hash'] != fingerprint:
            frappe.throw('Request ID already belongs to a different claim.', frappe.ValidationError)
        return reply(old, True)
    if frappe.db.exists(attendance.RECEIPT, payload['request_id']):
        frappe.throw('Request ID already used for an online punch.', frappe.ValidationError)
    try: check_window(event, datetime.now(timezone.utc))
    except ValueError as error: frappe.throw(str(error), frappe.ValidationError)
    if rules['require_photo'] and not photo_bytes:
        frappe.throw('A photo is required for offline punches.', frappe.ValidationError)
    if photo_bytes:
        from maas_attendance_mobile.photos import check_timing
        captured = datetime.fromisoformat(payload['photo']['captured_at'].replace('Z', '+00:00'))
        try:
            location, sampled = parse_location(payload.get('location'))
            check_timing(event, captured, location, sampled, datetime.now(timezone.utc), offline=True)
        except ValueError as error: frappe.throw(str(error), frappe.ValidationError)
    gps_check(payload, event, current['company'], rules)
    # Bound each employee's review backlog; exact retries do not consume slots.
    if len(frappe.get_all(CLAIM, filters={'employee': employee['name'], 'status': 'Pending Review'}, fields=['name'], limit_page_length=100)) >= 100:
        frappe.throw('Offline review backlog is full. Contact HR.', frappe.ValidationError)
    from frappe.utils import get_system_timezone
    local = site_time(event, get_system_timezone())
    values = {'doctype': CLAIM, 'request_id': payload['request_id'], 'employee': employee['name'], 'user': user,
        'request_hash': fingerprint, 'payload_json': json.dumps(payload, sort_keys=True), 'registered_device': registration,
        'log_type': payload['log_type'], 'event_time': local,
        'status': 'Pending Review', 'checkin': None, 'event_company': current['company']}
    previous = frappe.flags.get('maas_offline_write')
    frappe.flags.maas_offline_write = payload['request_id']
    try:
        doc = frappe.get_doc(values).insert(ignore_permissions=True)
        if photo_bytes:
            from maas_attendance_mobile.photos import save_private
            save_private(CLAIM, doc.name, photo_bytes)
            # Photo attachment/audit fields are written by the File service.
            # Reload before a subsequent save so none are overwritten or differ
            # from the immutable persisted evidence snapshot.
            doc.reload()
    finally: frappe.flags.maas_offline_write = previous
    # Every valid offline punch becomes an Employee Checkin at its original time.
    # The employee/device/policy locks and evidence validation above remain in
    # the same POST transaction. Only an ordering conflict is left for HR.
    conflict = sequence_conflict(doc, payload, local)
    if conflict:
        doc.sync_note = conflict
    else:
        create_checkin(doc, payload, local)
        from frappe.utils import now_datetime
        doc.status = 'Approved'
        doc.reviewed_at = now_datetime()
        doc.sync_note = 'Synced with original capture time and evidence.'
    previous_sync = frappe.flags.get(SYNC)
    previous_write = frappe.flags.get('maas_offline_write')
    frappe.flags.maas_offline_write = doc.request_id
    frappe.flags[SYNC] = doc.request_id
    try: doc.save(ignore_permissions=True)
    finally:
        frappe.flags[SYNC] = previous_sync
        frappe.flags.maas_offline_write = previous_write
    return reply(doc.as_dict())


def sequence_conflict(doc, payload, local):
    """Only expected ordering conflicts become pending; persistence errors escape."""
    latest = attendance.latest_checkin(doc.employee, lock=True)
    expected = payload['base_checkin']
    if payload['previous_request_id']:
        prior = frappe.db.get_value(CLAIM, payload['previous_request_id'],
            ['employee', 'user', 'status', 'checkin', 'registered_device'],
            as_dict=True, for_update=True)
        if (not prior or prior['employee'] != doc.employee or prior['user'] != doc.user
                or prior['status'] != 'Approved' or not prior.get('checkin')
                or prior.get('registered_device') != doc.registered_device):
            return 'Review the preceding offline claim first.'
        expected = prior['checkin']
    if (latest['name'] if latest else None) != expected:
        return 'Attendance changed after capture. Resolve this conflict manually; the claim remains pending.'
    try: check_transition(latest, payload['log_type'], local)
    except ValueError as error: return str(error)
    return None


def create_checkin(doc, payload, local):
    previous_context = frappe.flags.get(attendance.CONTEXT)
    frappe.flags[attendance.CONTEXT] = {'employee': doc.employee, 'user': frappe.session.user, 'request_id': doc.request_id}
    try:
        checkin = frappe.get_doc({'doctype': 'Employee Checkin', 'employee': doc.employee,
            'log_type': payload['log_type'], 'time': local,
            'device_id': 'MAAS-OFFLINE:' + payload['device_id'], 'skip_auto_attendance': 0,
            **({'latitude': payload['location']['latitude'], 'longitude': payload['location']['longitude']}
               if payload.get('location') else {})}).insert(ignore_permissions=True)
        doc.checkin = checkin.name
    finally:
        frappe.flags[attendance.CONTEXT] = previous_context


def review(request_id, decision, reason):
    if frappe.session.user == 'Guest' or not {'HR Manager', 'System Manager'} & set(frappe.get_roles()):
        frappe.throw('HR Manager or System Manager is required.', frappe.PermissionError)
    if not frappe.db.get_value('User', frappe.session.user, 'enabled'):
        frappe.throw('This reviewer login is disabled.', frappe.PermissionError)
    try: uuid4_string(request_id)
    except ValueError: frappe.throw('Invalid request ID.', frappe.ValidationError)
    if decision not in ('Approved', 'Rejected') or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        frappe.throw('Choose a decision and enter a reason of 5 to 500 characters.', frappe.ValidationError)
    # Read identity first, then take the same employee -> claim lock order as uploads.
    identity = frappe.db.get_value(CLAIM, request_id, ['employee', 'user'], as_dict=True)
    if not identity: frappe.throw('Offline claim not found.', frappe.ValidationError)
    if identity['user'] == frappe.session.user:
        frappe.throw('A different HR reviewer must review your claims.', frappe.PermissionError)
    current = frappe.db.get_value('Employee', identity['employee'], ['user_id', 'status', 'company'], as_dict=True, for_update=True)
    frappe.db.get_value(CLAIM, request_id, 'name', for_update=True)
    doc = frappe.get_doc(CLAIM, request_id)
    doc.check_permission('read')
    if doc.status != 'Pending Review':
        if doc.status != decision: frappe.throw('This review is already final.', frappe.ValidationError)
        return reply(doc.as_dict(), True)
    if decision == 'Approved':
        rules = policy.resolve(doc.employee)
        if doc.get('event_company') and (not current or current['company'] != doc.get('event_company')):
            frappe.throw('The employee company changed after capture. HR must resolve this claim manually.', frappe.ValidationError)
        if rules['require_photo'] and not doc.get('punch_photo'):
            frappe.throw('Photo evidence is missing. Reject and arrange a manual correction.', frappe.ValidationError)
        if not current or current['user_id'] != doc.user or current['status'] != 'Active' or not frappe.db.get_value('User', doc.user, 'enabled') or 'Employee' not in frappe.get_roles(doc.user) or not rules['offline']:
            frappe.throw('Offline check-in is no longer enabled for this employee. Reject or review with HR.', frappe.PermissionError)
        device = frappe.db.get_value(DEVICE, doc.registered_device, ['status', 'employee', 'user'], as_dict=True, for_update=True)
        if not device or device['status'] != 'Approved' or device['employee'] != doc.employee or device['user'] != doc.user:
            frappe.throw('The original device is no longer approved.', frappe.PermissionError)
        payload = json.loads(doc.payload_json)
        event = datetime.fromisoformat(payload['timestamp_utc'].replace('Z', '+00:00'))
        from frappe.utils import get_system_timezone
        local = site_time(event, get_system_timezone())
        conflict = sequence_conflict(doc, payload, local)
        if conflict: frappe.throw(conflict, frappe.ValidationError)
        create_checkin(doc, payload, local)
    from frappe.utils import now_datetime
    doc.status, doc.reviewed_by, doc.reviewed_at, doc.review_reason = decision, frappe.session.user, now_datetime(), reason.strip()
    previous = frappe.flags.get('maas_offline_write')
    frappe.flags.maas_offline_write = request_id
    try: doc.save(ignore_permissions=True)
    finally: frappe.flags.maas_offline_write = previous
    # Review + optional checkin commit atomically in Frappe's POST transaction.
    return reply(doc.as_dict())
