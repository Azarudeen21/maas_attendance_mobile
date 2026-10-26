import sys
import types
import unittest
from unittest.mock import Mock

import test_api
import test_attendance

ONLINE, OFFLINE = 'maas_online_checkin', 'maas_offline_checkin'
WITH, WITHOUT = 'maas_checkin_with_photo', 'maas_checkin_without_photo'


class EmployeeRulesTests(unittest.TestCase):
    def setUp(self):
        self.base = test_attendance.AttendanceTests(); self.base.setUp()
        self.frappe = self.base.frappe
        import maas_attendance_mobile.employee_rules as rules
        self.rules = rules
        rules.apply = self.base.base.rules_apply  # Exercise the real rules.
        self.employee = self.base.employee
        self.config = self.base.config

    def tearDown(self):
        self.base.tearDown()

    def boxes(self, *ticked):
        for field in (ONLINE, OFFLINE, WITH, WITHOUT):
            self.employee[field] = int(field in ticked)

    def effective(self, *ticked, **site):
        self.boxes(*ticked)
        return self.rules.apply(self.employee['name'], dict({'enable_offline_test': '1', 'require_test_photo': '1'}, **site))

    def test_channels_follow_boxes_and_never_reenable_a_site_gate(self):
        none = self.effective()
        self.assertEqual((none['online_allowed'], none['enable_offline_test']), (False, 0))
        online = self.effective(ONLINE)
        self.assertEqual((online['online_allowed'], online['enable_offline_test']), (True, 0))
        offline = self.effective(OFFLINE)
        self.assertEqual((offline['online_allowed'], offline['enable_offline_test']), (False, 1))
        self.assertEqual(self.effective(OFFLINE, enable_offline_test='0')['enable_offline_test'], 0)

    def test_photo_boxes(self):
        self.assertEqual(self.effective(ONLINE)['require_test_photo'], '1')  # Neither: site/company decides.
        self.assertEqual(self.effective(ONLINE, require_test_photo='0')['require_test_photo'], '0')
        self.assertEqual(self.effective(ONLINE, WITH, require_test_photo='0')['require_test_photo'], 1)
        without = self.effective(ONLINE, WITHOUT)
        self.assertEqual((without['require_test_photo'], without['photo_optional']), (0, False))
        both = self.effective(ONLINE, WITH, WITHOUT)
        self.assertEqual((both['require_test_photo'], both['photo_optional']), (0, True))

    def test_status_reports_offline_only_employee(self):
        self.config['enable_offline_test'] = '1'
        self.boxes(OFFLINE, WITH)
        status = self.base.service.status(self.employee)
        self.assertFalse(status['online_submission_enabled'])
        self.assertTrue(status['offline_submission_enabled'])
        self.assertTrue(status['photo_required'])
        self.boxes(ONLINE, WITH, WITHOUT)
        status = self.base.service.status(self.employee)
        self.assertEqual((status['online_submission_enabled'], status['photo_required'], status['photo_optional']), (True, False, True))

    def test_online_punch_requires_online_box_before_any_write(self):
        self.boxes(OFFLINE)
        with self.assertRaisesRegex(test_api.FakePermissionError, 'Online check-in is not enabled'):
            self.base.submit()
        self.assertFalse(self.base.created)
        self.boxes(ONLINE)
        self.assertFalse(self.base.submit()['duplicate'])

    def test_without_photo_box_allows_punch_when_site_requires_photo(self):
        self.config['require_test_photo'] = '1'
        self.boxes(ONLINE)
        with self.assertRaisesRegex(ValueError, 'photo is required'):
            self.base.submit()
        self.boxes(ONLINE, WITHOUT)
        self.assertFalse(self.base.submit()['duplicate'])

    def test_without_photo_employee_uses_normal_hrms_checkin(self):
        import types
        new = types.SimpleNamespace(employee=self.employee['name'], is_new=lambda: True)
        self.boxes(ONLINE, WITHOUT)
        self.assertTrue(self.base.service.status(self.employee)['standard_checkin'])
        self.base.service.guard_checkin(new)  # Protected test login, no MAAS context: allowed.
        self.assertFalse(self.base.created)
        for ticked in ((ONLINE, WITH, WITHOUT), (OFFLINE, WITHOUT), (ONLINE, WITH)):
            self.boxes(*ticked)
            self.assertFalse(self.base.service.status(self.employee)['standard_checkin'])
            with self.assertRaises(test_api.FakePermissionError):
                self.base.service.guard_checkin(new)
        self.boxes(ONLINE, WITHOUT)
        with self.assertRaises(test_api.FakePermissionError):
            self.base.service.guard_checkin(types.SimpleNamespace(employee=self.employee['name'], is_new=lambda: False))
        self.assertTrue(self.rules.allows_standard_checkin(self.employee['name'], self.employee['user_id']))
        self.assertFalse(self.rules.allows_standard_checkin(self.employee['name'], 'someone.else@example.test'))

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
            self.rules.ensure_fields()
        finally:
            for name, value in previous.items():
                if value is None: sys.modules.pop(name, None)
                else: sys.modules[name] = value
        fields = created.call_args.args[0]['Employee']
        self.assertEqual(fields[0]['insert_after'], 'attendance_device_id')
        self.assertEqual([f['label'] for f in fields if f['fieldtype'] == 'Check'],
                         ['Online Check in', 'Offline Check In', 'Check in with Photo', 'Check in Without Photo'])
        self.assertTrue(created.call_args.kwargs['update'])


if __name__ == '__main__':
    unittest.main()
