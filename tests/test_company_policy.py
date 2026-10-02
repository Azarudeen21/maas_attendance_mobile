import unittest
from unittest.mock import Mock
import test_api


class CompanyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests()
        self.base.setUp()
        from maas_attendance_mobile import company_policy
        self.policy = company_policy
        self.site = {'use_company_test_policies': 1, 'enable_test_attendance': 1,
                     'enable_offline_test': 1, 'test_employee': 'LEGACY'}
        self.row = {'enabled': 1, 'test_employee': 'EMP-A', 'enable_offline_test': 1}
        self.company = 'Company A'
        self.base.frappe.db.get_value.side_effect = self.lookup

    def tearDown(self):
        self.base.tearDown()

    def lookup(self, doctype, name, fields, **kwargs):
        if doctype == 'Employee':
            return self.company if fields == 'company' else 'test@example.test'
        self.assertEqual(doctype, self.policy.POLICY)
        self.assertEqual(name, {'company': self.company})
        return self.row

    def test_employee_company_is_selected_by_server(self):
        result = self.policy.effective('EMP-A', self.site)
        self.assertEqual(result['test_employee'], 'EMP-A')
        self.assertTrue(self.base.attendance.enabled_for('EMP-A', result))
        self.assertFalse(self.base.attendance.enabled_for('EMP-B', result))

    def test_missing_disabled_or_missing_company_never_falls_back(self):
        for company, row in [('Company A', None), ('Company A', {'enabled': 0, 'test_employee': 'LEGACY'}), (None, self.row)]:
            self.company, self.row = company, row
            result = self.policy.effective('LEGACY', self.site)
            self.assertFalse(self.base.attendance.enabled_for('LEGACY', result))

    def test_global_online_and_offline_switches_remain_gates(self):
        self.site['enable_test_attendance'] = 0
        self.site['enable_offline_test'] = 0
        result = self.policy.effective('EMP-A', self.site)
        self.assertEqual(result['enable_test_attendance'], 0)
        self.assertEqual(result['enable_offline_test'], 0)

    def test_company_cannot_weaken_site_evidence_minimums(self):
        for field in self.policy.REQUIREMENTS:
            self.site[field] = 1
            self.row[field] = 0
        result = self.policy.effective('EMP-A', self.site)
        for field in self.policy.REQUIREMENTS:
            self.assertEqual(result[field], 1)

    def test_company_can_require_stronger_evidence_and_disable_offline(self):
        self.row.update(require_test_photo=1, require_test_location=1,
                        require_approved_device=1, enable_offline_test=0)
        result = self.policy.effective('EMP-A', self.site)
        for field in self.policy.REQUIREMENTS:
            self.assertEqual(result[field], 1)
        self.assertEqual(result['enable_offline_test'], 0)

    def test_legacy_mode_does_not_query_company_policies(self):
        self.site['use_company_test_policies'] = 0
        self.assertIs(self.policy.effective('LEGACY', self.site), self.site)
        self.base.frappe.db.get_value.assert_not_called()

    def test_disabled_policy_account_still_cannot_bypass_via_orm(self):
        self.base.frappe.get_all = Mock(return_value=[{'test_employee': 'EMP-A'}])
        self.site['test_employee'] = None
        self.assertTrue(self.policy.test_login(self.site, 'test@example.test'))
        self.base.frappe.get_all.assert_called_once_with(self.policy.POLICY, fields=['test_employee'])

    def test_invalid_employee_company_is_rejected(self):
        self.base.frappe.db.get_value.side_effect = None
        self.base.frappe.db.get_value.return_value = {'status': 'Active', 'user_id': 'u', 'company': 'Company B'}
        with self.assertRaises(ValueError):
            self.policy.validate_employee('EMP-B', 'Company A')
