import importlib
import types
import unittest
from unittest.mock import Mock, patch

import test_api


class IntegrationDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.frappe.session.user = 'Administrator'
        self.frappe.get_installed_apps.return_value += ['maas_attendance_mobile']
        self.frappe.local = types.SimpleNamespace(site='maas-attendance-integration.example')
        self.frappe.conf = {'maas_attendance_integration_tests': 1}
        self.frappe.get_hooks = Mock(return_value={'hourly': ['maas_attendance_mobile.photo_retention.run']})
        import maas_attendance_mobile.integration_checks
        self.service = importlib.reload(maas_attendance_mobile.integration_checks)
        self.fields = {d['name']: {f['fieldname']: types.SimpleNamespace(fieldtype=f['fieldtype'])
            for f in d.get('fields', [])} for d in self.service.schema_definitions()}
        self.frappe.get_meta.side_effect = lambda name, **kwargs: types.SimpleNamespace(
            get_field=lambda field: self.fields.get(name, {}).get(field), has_field=lambda field: True)
        self.guard = patch.object(self.service, '_file_guard', return_value=True); self.guard.start()

    def tearDown(self):
        self.guard.stop(); self.base.tearDown()

    def test_preflight_is_read_only_and_never_claims_production_readiness(self):
        result = self.service.preflight()
        self.assertTrue(result['preflight_passed']); self.assertFalse(result['production_ready'])
        self.assertEqual(result['versions']['hrms'], '16.0.0')
        self.frappe.db.get_singles_dict.assert_called_once()

    def test_employee_cannot_enumerate_deployment_configuration(self):
        self.frappe.session.user = 'employee@example.test'
        with self.assertRaises(test_api.FakePermissionError): self.service.preflight()
        self.frappe.get_meta.assert_not_called()

    def test_missing_hrms_blocks_preflight(self):
        self.frappe.get_installed_apps.return_value.remove('hrms')
        result = self.service.preflight()
        self.assertFalse(result['preflight_passed'])
        self.assertFalse(next(c for c in result['checks'] if c['code'] == 'installed:hrms')['passed'])

    def test_stale_schema_and_incompatible_file_override_block_preflight(self):
        self.fields['Mobile Attendance Settings'].pop('enable_production_attendance')
        with patch.object(self.service, '_file_guard', return_value=False): result = self.service.preflight()
        self.assertFalse(result['preflight_passed'])
        self.assertFalse(next(c for c in result['checks'] if c['code'] == 'storage:protected_file')['passed'])

    def test_active_gates_are_reported_without_changing_them(self):
        self.frappe.db.get_singles_dict.return_value = {'enable_production_attendance': 1}
        self.assertFalse(self.service.preflight()['preflight_passed'])
        self.assertEqual(self.frappe.db.get_singles_dict.return_value, {'enable_production_attendance': 1})

    def test_fixture_guard_rejects_existing_sites_and_missing_opt_in(self):
        self.service.require_isolated_test_site()
        self.frappe.local.site = 'erpnextconsult.m.frappe.cloud'
        self.service.require_isolated_test_site()
        self.frappe.local.site = 'dev.localhost'
        with self.assertRaises(RuntimeError): self.service.require_isolated_test_site()
        self.frappe.local.site = 'maas-attendance-integration.example'
        self.frappe.conf.clear()
        with self.assertRaises(RuntimeError): self.service.require_isolated_test_site()
