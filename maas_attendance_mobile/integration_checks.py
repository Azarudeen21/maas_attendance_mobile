"""Read-only deployment diagnostics; never enables a gate or exposes credentials."""
import importlib
import json
from pathlib import Path

import frappe

from maas_attendance_mobile import __version__


def schema_definitions():
    root = Path(__file__).parent / 'maas_attendance_mobile' / 'doctype'
    return [json.loads(path.read_text(encoding='utf-8-sig')) for path in sorted(root.glob('*/*.json'))]


def _file_guard():
    from frappe.model.base_document import get_controller
    from maas_attendance_mobile.protected_file import AttendanceProtectedFile
    return issubclass(get_controller('File'), AttendanceProtectedFile)


def preflight():
    if frappe.session.user != 'Administrator' and 'System Manager' not in frappe.get_roles():
        frappe.throw('A System Manager must run deployment diagnostics.', frappe.PermissionError)
    checks = []
    versions = {'maas_attendance_mobile': __version__}

    def add(code, passed, detail):
        checks.append({'code': code, 'passed': bool(passed), 'detail': detail})

    installed = set(frappe.get_installed_apps())
    for app in ('frappe', 'erpnext', 'hrms', 'maas_attendance_mobile'):
        present = app in installed
        add('installed:' + app, present, 'Installed' if present else 'Install this required app on the test site')
        if present and app != 'maas_attendance_mobile':
            version = str(getattr(importlib.import_module(app), '__version__', 'unknown'))
            versions[app] = version
            add('version:' + app, version.split('.')[0] in ('15', '16'), 'Supported major version is 15 or 16')

    for definition in schema_definitions():
        name = definition['name']
        try:
            meta = frappe.get_meta(name, cached=False)
            wrong = []
            for field in definition.get('fields', []):
                if field['fieldtype'] in ('Section Break', 'Column Break', 'Tab Break', 'HTML', 'Button'):
                    continue
                actual = meta.get_field(field['fieldname'])
                if not actual or actual.fieldtype != field['fieldtype']:
                    wrong.append(field['fieldname'])
            add('schema:' + name, not wrong, 'Schema matches' if not wrong else 'Run migrate; missing/mismatched fields: ' + ', '.join(wrong))
        except Exception:
            add('schema:' + name, False, 'Run migrate; document metadata could not be loaded')

    try:
        meta = frappe.get_meta('Employee Checkin', cached=False)
        required = ('employee', 'time', 'log_type', 'device_id', 'skip_auto_attendance', 'latitude', 'longitude')
        missing = [field for field in required if not meta.has_field(field)]
        add('hrms:checkin_fields', not missing, 'Required fields present' if not missing else 'Missing checkin fields: ' + ', '.join(missing))
    except Exception:
        add('hrms:checkin_fields', False, 'Employee Checkin requires HRMS installation/migration')
    try:
        add('storage:protected_file', _file_guard(), 'The active File controller must retain MAAS evidence protection')
    except Exception:
        add('storage:protected_file', False, 'Inspect installed File overrides before testing photo storage')
    try:
        hourly = frappe.get_hooks('scheduler_events').get('hourly', [])
        add('scheduler:retention_hook', 'maas_attendance_mobile.photo_retention.run' in hourly,
            'Hook registration only; actual scheduler execution remains a separate test')
    except Exception:
        add('scheduler:retention_hook', False, 'Scheduler hooks could not be loaded')
    try:
        config = frappe.db.get_singles_dict('Mobile Attendance Settings')
        gates_off = all(str(config.get(key, 0)) != '1' for key in (
            'enable_test_attendance', 'enable_offline_test', 'enable_production_attendance', 'enable_production_offline'))
        add('baseline:submission_gates_off', gates_off, 'Keep submission gates OFF before the initial isolated integration run')
    except Exception:
        gates_off = False
        add('baseline:submission_gates_off', False, 'Settings could not be read; run migrate')
    return {'app_version': __version__, 'versions': versions,
        'checks': checks, 'preflight_passed': all(row['passed'] for row in checks),
        'production_ready': False,
        'note': 'Read-only compatibility check. SQL transactions, permissions, OAuth, photos, shifts and native delivery still require integration testing.'}


def require_isolated_test_site():
    """Fail before fixture writes unless explicitly enabled on a dedicated site."""
    site = getattr(frappe.local, 'site', '') or ''
    if not (site.startswith('maas-attendance-integration.') or site == 'erpnextconsult.m.frappe.cloud'):
        raise RuntimeError('Use a dedicated integration site or the user-authorized MAAS development site for these database tests')
    if not frappe.conf.get('maas_attendance_integration_tests'):
        raise RuntimeError('Enable maas_attendance_integration_tests only on this disposable integration site')
