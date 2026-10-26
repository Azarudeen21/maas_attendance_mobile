"""Who may check in, and how. The only place attendance rules are decided.

An employee may use a channel when the site switch is on, their company has an
enabled Mobile Attendance Company Policy, and the matching Employee box is
ticked. Offline additionally needs the site Offline Sync switch and the
company's Allow Offline. A box never re-enables a switch HR turned off.

Photo: With Photo only requires a photo; Without Photo only needs none (even if
the company requires photos); both makes it optional; neither follows the
company. Online + Without Photo (not With Photo) uses normal HRMS check-in.
"""
import frappe

SETTINGS = 'Mobile Attendance Settings'
POLICY = 'Mobile Attendance Company Policy'
ONLINE = 'maas_online_checkin'
OFFLINE = 'maas_offline_checkin'
WITH_PHOTO = 'maas_checkin_with_photo'
WITHOUT_PHOTO = 'maas_checkin_without_photo'
FIELDS = (ONLINE, OFFLINE, WITH_PHOTO, WITHOUT_PHOTO)


def on(value):
    return value in (1, '1', True)


def site():
    # Never cached: turning a switch off applies to the very next request.
    return frappe.db.get_singles_dict(SETTINGS)


def _ticked(row):
    return {field: on(row.get(field)) for field in FIELDS}


def _standard(ticked):
    return ticked[ONLINE] and ticked[WITHOUT_PHOTO] and not ticked[WITH_PHOTO]


def resolve(employee):
    """Effective rules for one employee."""
    config = site()
    row = frappe.db.get_value('Employee', employee, ['company', *FIELDS], as_dict=True) or {}
    company = frappe.db.get_value(POLICY, {'company': row.get('company'), 'enabled': 1},
        ['require_photo', 'require_location', 'require_approved_device', 'allow_offline'],
        as_dict=True) if row.get('company') else None
    company = company or {}
    ticked = _ticked(row)
    active = on(config.get('enable_mobile_attendance')) and bool(company)
    rules = {
        'online': active and ticked[ONLINE],
        'offline': active and ticked[OFFLINE] and on(config.get('enable_offline_sync')) and on(company.get('allow_offline')),
        'require_photo': on(company.get('require_photo')),
        'photo_optional': False,
        'require_location': on(company.get('require_location')),
        'require_device': on(company.get('require_approved_device')),
        'standard_checkin': _standard(ticked),
    }
    if ticked[WITH_PHOTO] or ticked[WITHOUT_PHOTO]:
        rules['require_photo'] = ticked[WITH_PHOTO] and not ticked[WITHOUT_PHOTO]
        rules['photo_optional'] = ticked[WITH_PHOTO] and ticked[WITHOUT_PHOTO]
    return rules


def own_employee(user):
    """The single Employee linked to a login, with its check-in boxes."""
    rows = frappe.get_all('Employee', filters={'user_id': user}, fields=['name', *FIELDS], limit_page_length=2)
    return rows[0] if len(rows) == 1 else None


def uses_app(user, employee):
    """True when this login's own Employee is set up for app check-in."""
    row = own_employee(user)
    return bool(row and row['name'] == employee and (on(row.get(ONLINE)) or on(row.get(OFFLINE))))


def allows_standard_checkin(employee, user):
    """Normal HRMS Employee Checkin insert, only for the employee's own login."""
    row = frappe.db.get_value('Employee', employee, ['user_id', *FIELDS], as_dict=True)
    return isinstance(row, dict) and row.get('user_id') == user and _standard(_ticked(row))


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
         'description': 'No photo needed, even if the company requires photos. With Online, uses normal HRMS check-in.'},
    ]}, update=True)


def validate_workplaces(company):
    from maas_attendance_mobile.geofence import workplace_values
    rows = frappe.get_all('Mobile Attendance Workplace', filters={'company': company, 'enabled': 1},
        fields=['latitude', 'longitude', 'radius_m', 'max_accuracy_m']) if company else []
    if not rows:
        frappe.throw('Configure an enabled workplace for this company before requiring GPS checks.')
    for row in rows:
        try:
            values = workplace_values(row)
            if values['max_accuracy_m'] >= values['radius_m']:
                raise ValueError('GPS uncertainty must be smaller than the workplace radius.')
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)
