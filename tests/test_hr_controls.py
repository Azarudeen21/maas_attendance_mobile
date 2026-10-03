import importlib
import types
import unittest
from unittest.mock import Mock

import test_api
import test_device_controller


class HROverviewTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.frappe.get_roles.return_value = ['HR Manager']
        values = {'test_employee': 'EMP-TEST', 'enable_test_attendance': '0', 'enable_offline_test': '1', 'secret_hash': 'hidden'}
        self.config = types.SimpleNamespace(get=values.get, check_permission=Mock())
        self.frappe.get_doc = Mock(return_value=self.config)
        self.frappe.get_list = Mock(return_value=[{'total': 3}])
        import maas_attendance_mobile.hr_controls
        self.service = importlib.reload(maas_attendance_mobile.hr_controls)

    def tearDown(self): self.base.tearDown()

    def test_employees_and_guests_rejected_before_data_queries(self):
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.service.overview()
        self.frappe.get_roles.return_value = ['System Manager']
        self.frappe.session.user = 'Guest'
        with self.assertRaises(test_api.FakePermissionError): self.service.overview()
        self.frappe.get_doc.assert_not_called(); self.frappe.get_list.assert_not_called()

    def test_hr_readonly_counts_use_permission_scoped_queries_and_allowlisted_policy(self):
        data = self.service.overview()
        self.config.check_permission.assert_called_once_with('read')
        self.assertFalse(data['can_edit_settings'])
        self.assertFalse(data['policy']['enable_test_attendance'])
        self.assertTrue(data['policy']['enable_offline_test'])
        self.assertNotIn('secret_hash', str(data))
        self.assertEqual(data['counts']['pending_claims'], 3)
        self.assertEqual(len(self.frappe.get_list.call_args_list), 4)
        claim_call = self.frappe.get_list.call_args_list[2]
        self.assertEqual(claim_call.kwargs, {'filters': {'status': 'Pending Review'}, 'fields': [{'COUNT': 'name', 'as': 'total'}]})

    def test_v15_uses_legacy_count_without_bypassing_permissions(self):
        self.frappe.__version__ = '15.80.0'
        self.service.overview()
        self.assertEqual(self.frappe.get_list.call_args_list[2].kwargs,
                         {'filters': {'status': 'Pending Review'}, 'fields': ['count(name) as total']})

    def test_empty_permission_scoped_results_return_zero(self):
        self.frappe.get_list.return_value = []
        self.assertEqual(set(self.service.overview()['counts'].values()), {0})

    def test_permission_denial_propagates_and_system_manager_edit_flag(self):
        self.config.check_permission.side_effect = test_api.FakePermissionError()
        with self.assertRaises(test_api.FakePermissionError): self.service.overview()
        self.frappe.get_list.assert_not_called()
        self.config.check_permission.side_effect = None
        self.frappe.get_roles.return_value = ['System Manager']
        self.assertTrue(self.service.overview()['can_edit_settings'])


class HRSettingsTests(unittest.TestCase):
    def setUp(self):
        self.base = test_device_controller.DeviceControllerTests(); self.base.setUp()
        self.frappe = self.base.frappe
        module = importlib.import_module('maas_attendance_mobile.maas_attendance_mobile.doctype.mobile_attendance_settings.mobile_attendance_settings')
        self.controller = importlib.reload(module)
        self.doc = self.controller.MobileAttendanceSettings()
        self.doc.enable_test_attendance = 1; self.doc.test_employee = 'EMP-TEST'
        self.row = types.SimpleNamespace(status='Active', user_id='employee@example.test')
        self.frappe.db.get_value = Mock(side_effect=lambda dt, name, fields, **kw: self.row if isinstance(fields, list) else ('Company' if fields == 'company' else 1))
        self.frappe.get_roles.return_value = ['Employee']
        self.frappe.get_all = Mock(return_value=[{'name': 'EMP-TEST'}])

    def tearDown(self): self.base.tearDown()

    def test_both_gates_disabled_accept_string_zero_but_offline_needs_online(self):
        self.doc.enable_test_attendance = '0'; self.doc.enable_offline_test = '0'
        self.doc.validate(); self.frappe.db.get_value.assert_not_called()
        self.doc.enable_offline_test = '1'
        with self.assertRaisesRegex(ValueError, 'online test policy'): self.doc.validate()

    def test_enabled_policy_requires_active_unique_enabled_employee_login(self):
        self.doc.validate()
        self.row.status = 'Inactive'
        with self.assertRaisesRegex(ValueError, 'active test employee'): self.doc.validate()
        self.row.status = 'Active'; self.frappe.get_all.return_value = [{'name': 'A'}, {'name': 'B'}]
        with self.assertRaisesRegex(ValueError, 'exactly one'): self.doc.validate()
        self.frappe.get_all.return_value = [{'name': 'A'}]; self.frappe.get_roles.return_value = []
        with self.assertRaisesRegex(ValueError, 'Employee role'): self.doc.validate()

    def test_gps_requirement_checks_company_workplace_configuration(self):
        self.doc.require_test_location = 1
        self.frappe.get_all.side_effect = lambda dt, **kw: [{'name': 'EMP-TEST'}] if dt == 'Employee' else []
        with self.assertRaisesRegex(ValueError, 'enabled workplace'): self.doc.validate()
        workplace = {'latitude': 24.7, 'longitude': 46.7, 'radius_m': 100, 'max_accuracy_m': 20}
        self.frappe.get_all.side_effect = lambda dt, **kw: [{'name': 'EMP-TEST'}] if dt == 'Employee' else [workplace]
        self.doc.validate()
        self.assertEqual(self.frappe.get_all.call_args.kwargs['filters'], {'company': 'Company', 'enabled': 1})
        workplace['max_accuracy_m'] = 100
        with self.assertRaisesRegex(ValueError, 'uncertainty'): self.doc.validate()


if __name__ == '__main__': unittest.main()
