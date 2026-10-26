"""Per-employee check-in channels and photo rules (Employee checkboxes).

Online/Offline: an unticked channel is blocked; no channel ticked means no app
attendance. Site/company gates still apply, so a box never re-enables a gate
that HR switched off.
Photo: with-only requires a photo, without-only needs none (overriding the
site/company photo requirement), both makes it optional, neither follows the
site/company setting.
Online + without-only: the employee uses normal HRMS check-in/out.
"""
import frappe

ONLINE = 'maas_online_checkin'
OFFLINE = 'maas_offline_checkin'
WITH_PHOTO = 'maas_checkin_with_photo'
WITHOUT_PHOTO = 'maas_checkin_without_photo'
FIELDS = (ONLINE, OFFLINE, WITH_PHOTO, WITHOUT_PHOTO)


def ensure_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({'Employee': [
        {'fieldname': 'maas_checkin_section', 'fieldtype': 'Section Break', 'label': 'Mobile Check-in',
         'insert_after': 'attendance_device_id'},
        {'fieldname': ONLINE, 'fieldtype': 'Check', 'label': 'Online Check in', 'insert_after': 'maas_checkin_section',
         'description': 'Check in from the mobile app while connected.'},
        {'fieldname': OFFLINE, 'fieldtype': 'Check', 'label': 'Offline Check In', 'insert_after': ONLINE,
         'description': 'Save check-ins on the phone and upload them later. With Online also ticked, used only when there is no connection.'},
        {'fieldname': 'maas_checkin_column', 'fieldtype': 'Column Break', 'insert_after': OFFLINE},
        {'fieldname': WITH_PHOTO, 'fieldtype': 'Check', 'label': 'Check in with Photo', 'insert_after': 'maas_checkin_column',
         'description': 'A selfie is required. With "without Photo" also ticked, the photo is optional.'},
        {'fieldname': WITHOUT_PHOTO, 'fieldtype': 'Check', 'label': 'Check in Without Photo', 'insert_after': WITH_PHOTO,
         'description': 'No photo needed, even if the company requires photos.'},
    ]}, update=True)


def _ticked(row):
    return {field: row.get(field) in (1, '1', True) for field in FIELDS}


def _standard(ticked):
    return ticked[ONLINE] and ticked[WITHOUT_PHOTO] and not ticked[WITH_PHOTO]


def allows_standard_checkin(employee, user):
    """Normal HRMS Employee Checkin insert, only for the employee's own login."""
    row = frappe.db.get_value('Employee', employee, ['user_id', *FIELDS], as_dict=True)
    return isinstance(row, dict) and row.get('user_id') == user and _standard(_ticked(row))


def apply(employee, config):
    row = frappe.db.get_value('Employee', employee, list(FIELDS), as_dict=True) or {}
    ticked = _ticked(row)
    result = dict(config, online_allowed=ticked[ONLINE], photo_optional=False, standard_checkin=_standard(ticked),
        enable_offline_test=int(ticked[OFFLINE] and str(config.get('enable_offline_test', 0)) == '1'))
    if ticked[WITH_PHOTO] or ticked[WITHOUT_PHOTO]:
        result['require_test_photo'] = int(ticked[WITH_PHOTO] and not ticked[WITHOUT_PHOTO])
        result['photo_optional'] = ticked[WITH_PHOTO] and ticked[WITHOUT_PHOTO]
    return result
