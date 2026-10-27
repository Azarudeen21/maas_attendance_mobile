"""Supervisor face check-in: the face selects the employee, the supervisor confirms.

The supervisor's approved phone captures photo and GPS. The server identifies the
employee within the supervisor's company and, on confirmation, identifies the
same photo again before applying the employee's normal attendance policy.
"""
import json
from datetime import datetime, timezone

import frappe

from maas_attendance_mobile import policy
from maas_attendance_mobile.attendance import CONTEXT, RECEIPT, _reply, latest_checkin
from maas_attendance_mobile.faces import SETTINGS, FaceError, analyse, decide, limits, load_image, rank, truthy
from maas_attendance_mobile.punch_rules import check_fresh, check_transition, parse_request, request_hash, site_time

ROLE = 'MAAS Attendance Supervisor'
MESSAGES = {
    'match': None,
    'no_match': 'Face not recognised. Check the employee is enrolled, or scan again in better light.',
    'ambiguous': 'Two employees look too similar in this photo. Scan again facing the camera.',
}


class ScanRefused(frappe.ValidationError):
    """A saved offline scan that cannot become a check-in. Nothing is stored; the
    app drops the scan and tells the supervisor to scan the person again."""


def ensure_role():
    if not frappe.db.exists('Role', ROLE):
        frappe.get_doc({'doctype': 'Role', 'role_name': ROLE, 'desk_access': 0}).insert(ignore_permissions=True)


def available(site=None):
    site = site or frappe.db.get_singles_dict(SETTINGS)
    return (truthy(site.get('enable_face_recognition')) and truthy(site.get('enable_supervisor_checkin'))
            and ROLE in frappe.get_roles())


def _require():
    site = frappe.db.get_singles_dict(SETTINGS)
    if not available(site):
        frappe.throw('Supervisor face check-in is not enabled for your account.', frappe.PermissionError)
    return limits(site)


def _photo(photo):
    from maas_attendance_mobile.photos import parse_photo
    try:
        return parse_photo(photo)
    except (ValueError, OverflowError, OSError, TypeError):
        frappe.throw('Take a new camera photo and try again.', frappe.ValidationError)


def _identify(supervisor, content):
    threshold, margin = limits(frappe.db.get_singles_dict(SETTINGS))
    try:
        vector, _ = analyse(load_image(content))
    except FaceError as error:
        return {'decision': error.code, 'message': str(error)}
    scores, employees = rank(vector, supervisor['company'], ignore_permissions=True)
    decision, ranked = decide(scores, threshold, margin)
    if decision != 'match':
        # Never reveal near-miss names to the supervisor.
        return {'decision': decision, 'message': MESSAGES[decision]}
    name, score = ranked[0]
    return {'decision': 'match', 'message': None, 'employee': name,
            'employee_name': employees[name]['employee_name'], 'score': round(score, 4)}


def _next_log_type(employee):
    latest = latest_checkin(employee)
    if not latest or latest['log_type'] == 'OUT':
        return 'IN'
    return 'OUT' if latest['log_type'] == 'IN' else None


def identify(supervisor, photo, device_id, device_secret):
    """Preview only: nothing is stored. Face search needs the supervisor's approved phone."""
    _require()
    from maas_attendance_mobile.devices import require_approved
    require_approved(supervisor, device_id, device_secret)
    _, content, _ = _photo(photo)
    result = _identify(supervisor, content)
    if result['decision'] == 'match':
        result['attendance_enabled'] = policy.resolve(result['employee'])['online']
        result['next_log_type'] = _next_log_type(result['employee'])
        if not result['attendance_enabled']:
            result['message'] = 'Online check-in is not enabled for this employee. Contact HR.'
    return dict(result, api_version=1)


def punch(supervisor, employee, request_id, log_type, timestamp, device_id, app_version, location, device_secret, photo):
    _require()
    try:
        payload, event = parse_request(request_id, log_type, timestamp, device_id, app_version)
        from maas_attendance_mobile.geofence import parse_location
        location, sampled_at = parse_location(location)
    except (ValueError, OverflowError, OSError, TypeError):
        frappe.throw('Invalid punch request. Use UUID v4 IDs, IN/OUT and an ISO timestamp with offset.', frappe.ValidationError)
    if not isinstance(employee, str) or not employee:
        frappe.throw('Confirm the employee before punching.', frappe.ValidationError)
    payload['photo'], content, photo_at = _photo(photo)
    payload.update(location=location, capture_mode='Supervisor')
    user = frappe.session.user

    # The device belongs to the supervisor; this also locks the supervisor's device row.
    from maas_attendance_mobile.devices import require_approved
    registered_device = require_approved(supervisor, device_id, device_secret)
    fingerprint = request_hash(payload, employee, user)
    receipt = frappe.db.get_value(RECEIPT, request_id, [
        'name', 'employee', 'request_hash', 'checkin', 'log_type', 'event_time', 'timestamp_utc',
    ], as_dict=True, for_update=True)
    if receipt:
        if receipt['employee'] != employee or receipt['request_hash'] != fingerprint:
            frappe.throw('This request ID was already used for a different punch.', frappe.ValidationError)
        return _reply(receipt, True)
    if frappe.db.exists('Mobile Attendance Offline Claim', request_id):
        frappe.throw('This request ID already belongs to an offline claim.', frappe.ValidationError)

    # Identify again: the confirmation never chooses the employee by itself.
    result = _identify(supervisor, content)
    if result.get('employee') != employee:
        frappe.throw(result.get('message') or 'This face does not match the confirmed employee. Scan again.', frappe.ValidationError)
    current = frappe.db.get_value('Employee', employee, ['status', 'company'], as_dict=True, for_update=True)
    if not current or current['status'] != 'Active' or current['company'] != supervisor['company']:
        frappe.throw('Only active employees in your company can be checked in.', frappe.PermissionError)
    rules = policy.resolve(employee)
    if not rules['online']:
        frappe.throw('Online check-in is not enabled for this employee. Contact HR.', frappe.PermissionError)

    from maas_attendance_mobile.photos import check_timing
    try:
        check_timing(event, photo_at, location, sampled_at, datetime.now(timezone.utc))
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)
    gps = None
    if rules['require_location']:
        from maas_attendance_mobile.geofence import evaluate
        workplaces = frappe.get_all('Mobile Attendance Workplace', filters={'company': current['company'], 'enabled': 1},
            fields=['name', 'latitude', 'longitude', 'radius_m', 'max_accuracy_m'])
        try:
            gps = evaluate(location, sampled_at, workplaces, sampled_at, photo_at)
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)

    from frappe.utils import get_system_timezone
    local_time = site_time(event, get_system_timezone())
    try:
        check_fresh(event, datetime.now(timezone.utc))
        check_transition(latest_checkin(employee, lock=True), log_type, local_time)
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)

    receipt = _record(employee, request_id, fingerprint, payload, log_type, local_time, 'MAAS:',
                      location, gps, registered_device, result['score'], content)
    return dict(_reply(receipt.as_dict(), False), employee_name=result['employee_name'])


def _record(employee, request_id, fingerprint, payload, log_type, local_time, prefix,
            location, gps, registered_device, score, content):
    """Employee Checkin + immutable receipt + private photo, in one transaction."""
    user = frappe.session.user
    previous = frappe.flags.get(CONTEXT)
    frappe.flags[CONTEXT] = {'employee': employee, 'user': user, 'request_id': request_id}
    try:
        checkin = frappe.get_doc({
            'doctype': 'Employee Checkin', 'employee': employee, 'time': local_time, 'log_type': log_type,
            'device_id': prefix + payload['device_id'], 'skip_auto_attendance': 0,
            'latitude': location['latitude'], 'longitude': location['longitude'],
        }).insert(ignore_permissions=True)
        receipt = frappe.get_doc({
            'doctype': RECEIPT, 'request_id': request_id, 'employee': employee,
            'request_hash': fingerprint, 'checkin': checkin.name, 'log_type': log_type,
            'event_time': local_time, 'timestamp_utc': payload['timestamp_utc'],
            'device_id': payload['device_id'], 'app_version': payload['app_version'],
            'location_json': json.dumps(location, sort_keys=True),
            'workplace': gps['workplace'] if gps else None,
            'distance_m': gps['distance_m'] if gps else None,
            'accuracy_m': gps['accuracy_m'] if gps else None,
            'registered_device': registered_device,
            'photo_sha256': payload['photo']['sha256'], 'photo_captured_at': payload['photo']['captured_at'],
            'capture_mode': 'Supervisor', 'supervisor': user, 'face_score': score,
        }).insert(ignore_permissions=True)
        from maas_attendance_mobile.photos import link_checkin, save_private
        link_checkin(checkin.name, save_private(RECEIPT, receipt.name, content))
    finally:
        frappe.flags[CONTEXT] = previous
    return receipt


OFFLINE_FIELDS = {'request_id', 'timestamp', 'device_id', 'app_version', 'photo', 'location'}


def _refuse(message):
    frappe.throw(message, ScanRefused)


def offline_punch(supervisor, request, device_secret):
    """A scan saved on the supervisor's phone without a connection.

    On upload the server identifies the face, then records that employee's next
    action (IN after OUT, OUT after IN) at the original scan time. Anything that
    cannot become a check-in is refused with ScanRefused and nothing is stored."""
    _require()
    try:
        if isinstance(request, str):
            if len(request) > 3 * 1024 * 1024:
                raise ValueError('Saved scan too large.')
            request = json.loads(request)
        if not isinstance(request, dict) or set(request) != OFFLINE_FIELDS:
            raise ValueError('Invalid saved scan fields.')
        # IN is a placeholder: the server picks the action once it knows the employee.
        payload, event = parse_request(request['request_id'], 'IN', request['timestamp'],
                                       request['device_id'], request['app_version'])
        del payload['log_type']
        from maas_attendance_mobile.geofence import parse_location
        location, sampled_at = parse_location(request['location'])
    except (ValueError, OverflowError, OSError, TypeError):
        frappe.throw('Invalid saved scan. Use UUID v4 IDs and an ISO timestamp with offset.', frappe.ValidationError)
    payload['photo'], content, photo_at = _photo(request['photo'])
    payload.update(location=location, capture_mode='Supervisor Offline')
    request_id = payload['request_id']

    from maas_attendance_mobile.devices import require_approved
    registered_device = require_approved(supervisor, payload['device_id'], device_secret)
    fingerprint = request_hash(payload, supervisor['name'], frappe.session.user)
    receipt = frappe.db.get_value(RECEIPT, request_id, [
        'name', 'employee', 'request_hash', 'checkin', 'log_type', 'event_time', 'timestamp_utc', 'supervisor',
    ], as_dict=True, for_update=True)
    if receipt:
        # Same upload again, or a live confirm whose reply was lost before the
        # phone saved it offline under the same ID: return that check-in.
        if receipt['request_hash'] != fingerprint and receipt['supervisor'] != frappe.session.user:
            frappe.throw('This request ID was already used for a different punch.', frappe.ValidationError)
        return dict(_reply(receipt, True),
                    employee_name=frappe.db.get_value('Employee', receipt['employee'], 'employee_name'))
    if frappe.db.exists('Mobile Attendance Offline Claim', request_id):
        frappe.throw('This request ID already belongs to an offline claim.', frappe.ValidationError)

    from maas_attendance_mobile.offline import check_window
    from maas_attendance_mobile.photos import check_timing
    now = datetime.now(timezone.utc)
    try:
        check_window(event, now)
        check_timing(event, photo_at, location, sampled_at, now, offline=True)
    except ValueError as error:
        _refuse(str(error))

    result = _identify(supervisor, content)
    if result['decision'] != 'match':
        _refuse(result['message'] or 'Face not recognised. Scan the person again.')
    employee, name = result['employee'], result['employee_name']
    current = frappe.db.get_value('Employee', employee, ['status', 'company'], as_dict=True, for_update=True)
    if not current or current['status'] != 'Active' or current['company'] != supervisor['company']:
        _refuse('Only active employees in your company can be checked in.')
    rules = policy.resolve(employee)
    if not (rules['offline_sync'] and (rules['online'] or rules['offline'])):
        _refuse('Offline check-in is not enabled for %s. Contact HR.' % name)

    gps = None
    if rules['require_location']:
        from maas_attendance_mobile.geofence import evaluate
        workplaces = frappe.get_all('Mobile Attendance Workplace', filters={'company': current['company'], 'enabled': 1},
            fields=['name', 'latitude', 'longitude', 'radius_m', 'max_accuracy_m'])
        try:
            gps = evaluate(location, sampled_at, workplaces, sampled_at, photo_at)
        except ValueError as error:
            _refuse(str(error))

    from frappe.utils import get_system_timezone
    local_time = site_time(event, get_system_timezone())
    latest = latest_checkin(employee, lock=True)
    log_type = 'OUT' if latest and latest['log_type'] == 'IN' else 'IN'
    try:
        check_transition(latest, log_type, local_time)
    except ValueError as error:
        _refuse('%s: %s' % (name, error))
    receipt = _record(employee, request_id, fingerprint, payload, log_type, local_time, 'MAAS-OFFLINE:',
                      location, gps, registered_device, result['score'], content)
    return dict(_reply(receipt.as_dict(), False), employee_name=name)
