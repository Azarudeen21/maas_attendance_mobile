"""Permission-scoped HR overview. Mutations use existing DocType controllers."""
import frappe

from maas_attendance_mobile import __version__

POLICY_FIELDS = (
    'enable_test_attendance', 'require_test_location', 'require_approved_device',
    'enable_offline_test', 'require_test_photo',
)


def overview():
    roles = set(frappe.get_roles())
    if frappe.session.user == 'Guest' or not roles.intersection({'HR Manager', 'System Manager'}):
        frappe.throw('HR Manager or System Manager access is required.', frappe.PermissionError)
    config = frappe.get_doc('Mobile Attendance Settings')
    config.check_permission('read')
    # get_list preserves normal role and User Permission restrictions. Never
    # aggregate with get_all/db.count, which could reveal inaccessible records.
    def count(doctype, filters):
        rows = frappe.get_list(doctype, filters=filters, fields=['count(name) as total'])
        return int(rows[0]['total']) if rows else 0
    return {
        'api_version': 1, 'app_version': __version__, 'test_mode': True,
        'can_edit_settings': 'System Manager' in roles,
        'test_employee': config.get('test_employee'),
        'policy': {key: config.get(key) in (1, '1', True) for key in POLICY_FIELDS},
        'counts': {
            'pending_devices': count('Mobile Attendance Device', {'status': 'Pending'}),
            'approved_devices': count('Mobile Attendance Device', {'status': 'Approved'}),
            'pending_claims': count('Mobile Attendance Offline Claim', {'status': 'Pending Review'}),
            'enabled_workplaces': count('Mobile Attendance Workplace', {'enabled': 1}),
        },
    }
