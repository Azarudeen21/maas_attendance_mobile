import sys
import types
import unittest
from unittest.mock import Mock

import test_api
import test_attendance

ONLINE, OFFLINE = 'maas_online_checkin', 'maas_offline_checkin'
WITH, WITHOUT = 'maas_checkin_with_photo', 'maas_checkin_without_photo'
POLICY = 'Mobile Attendance Company Policy'


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = test_attendance.AttendanceTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.policy = self.base.base.policy
        self.policy.resolve = self.base.base.real_resolve  # Exercise the real rules.
        self.employee = self.base.employee
        self.site = {'enable_mobile_attendance': 1, 'enable_offline_sync': 1}
        self.company = {'enabled': 1, 'require_photo': 1, 'require_location': 0, 'require_approved_device': 1, 'allow_offline': 1}
        self.frappe.db.get_singles_dict = Mock(side_effect=lambda doctype: self.site)
        original = self.base.get_value
        self.frappe.db.get_value.side_effect = lambda doctype, name, fields, **kw: (
            self.policy_row(name) if doctype == POLICY else original(doctype, name, fields, **kw))

    def tearDown(self):
        self.base.tearDown()

    def policy_row(self, filters):
        if filters != {'company': 'Test Company', 'enabled': 1} or not self.company.get('enabled'):
            return None
        return {key: value for key, value in self.company.items() if key != 'enabled'}

    def boxes(self, *ticked):
        for field in (ONLINE, OFFLINE, WITH, WITHOUT):
            self.employee[field] = int(field in ticked)

    def rules(self, *ticked):
        self.boxes(*ticked)
        return self.policy.resolve(self.employee['name'])

    def test_site_company_and_boxes_must_all_allow_a_channel(self):
        self.assertEqual({k: v for k, v in self.rules(ONLINE, OFFLINE).items() if k in ('online', 'offline')},
                         {'online': True, 'offline': True})
        self.assertFalse(self.rules()['online'])
        self.assertFalse(self.rules(OFFLINE)['online'])
        self.site['enable_mobile_attendance'] = 0
        self.assertEqual((self.rules(ONLINE, OFFLINE)['online'], self.rules(ONLINE, OFFLINE)['offline']), (False, False))
        self.site['enable_mobile_attendance'] = 1
        self.company['enabled'] = 0
        self.assertFalse(self.rules(ONLINE)['online'])

    def test_offline_needs_site_sync_and_company_allow(self):
        self.site['enable_offline_sync'] = 0
        self.assertFalse(self.rules(OFFLINE)['offline'])
        self.site['enable_offline_sync'] = 1
        self.company['allow_offline'] = 0
        self.assertFalse(self.rules(OFFLINE)['offline'])

    def test_photo_boxes_override_the_company(self):
        self.assertEqual(self.rules(ONLINE)['require_photo'], True)
        self.company['require_photo'] = 0
        self.assertEqual(self.rules(ONLINE)['require_photo'], False)
        self.assertEqual(self.rules(ONLINE, WITH)['require_photo'], True)
        self.company['require_photo'] = 1
        without = self.rules(ONLINE, WITHOUT)
        self.assertEqual((without['require_photo'], without['photo_optional']), (False, False))
        both = self.rules(ONLINE, WITH, WITHOUT)
        self.assertEqual((both['require_photo'], both['photo_optional']), (False, True))
        self.assertEqual((both['require_device'], both['require_location']), (True, False))

    def test_standard_checkin_is_online_plus_without_photo_only(self):
        self.assertTrue(self.rules(ONLINE, WITHOUT)['standard_checkin'])
        for ticked in ((ONLINE, WITH, WITHOUT), (OFFLINE, WITHOUT), (ONLINE, WITH)):
            self.assertFalse(self.rules(*ticked)['standard_checkin'])
        self.boxes(ONLINE, WITHOUT)
        user = self.employee['user_id']
        self.assertTrue(self.policy.allows_standard_checkin(self.employee['name'], user))
        self.assertFalse(self.policy.allows_standard_checkin(self.employee['name'], 'someone.else@example.test'))

    def test_app_user_means_own_employee_with_a_channel_box(self):
        row = {'name': self.employee['name'], ONLINE: 0, OFFLINE: 1}
        self.frappe.get_all = Mock(return_value=[row])
        self.assertTrue(self.policy.uses_app('employee@example.test', self.employee['name']))
        self.assertFalse(self.policy.uses_app('employee@example.test', 'SOMEONE-ELSE'))
        row[OFFLINE] = 0
        self.assertFalse(self.policy.uses_app('employee@example.test', self.employee['name']))
        self.frappe.get_all = Mock(return_value=[row, dict(row, name='SECOND')])
        self.assertIsNone(self.policy.own_employee('employee@example.test'))

    def test_status_reports_an_offline_only_employee(self):
        self.boxes(OFFLINE, WITH)
        status = self.base.service.status(self.employee)
        self.assertEqual((status['online_submission_enabled'], status['offline_submission_enabled'], status['photo_required']),
                         (False, True, True))
        self.assertEqual(status['attendance_mode'], 'production')
        for removed in ('test_mode', 'online_test_submission_enabled', 'offline_test_submission_enabled', 'offline_auto_accept_enabled'):
            self.assertNotIn(removed, status)

    def test_fields_are_created_on_employee_after_device_id(self):
        created = Mock()
        module = types.ModuleType('frappe.custom.doctype.custom_field.custom_field')
        module.create_custom_fields = created
        names = ('frappe.custom', 'frappe.custom.doctype', 'frappe.custom.doctype.custom_field', module.__name__)
        previous = {name: sys.modules.get(name) for name in names}
        for name in names[:-1]:
            sys.modules[name] = types.ModuleType(name)
        sys.modules[module.__name__] = module
        try:
            self.policy.ensure_fields()
        finally:
            for name, value in previous.items():
                if value is None: sys.modules.pop(name, None)
                else: sys.modules[name] = value
        fields = created.call_args.args[0]['Employee']
        self.assertEqual(fields[0]['insert_after'], 'attendance_device_id')
        self.assertEqual([f['label'] for f in fields if f['fieldtype'] == 'Check'],
                         ['Online Check in', 'Offline Check In', 'Check in with Photo', 'Check in Without Photo'])


if __name__ == '__main__':
    unittest.main()
