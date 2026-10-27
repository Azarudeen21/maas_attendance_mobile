"""Isolated decision-logic tests, without a Frappe server or database."""

import importlib
import sys
import types
import unittest
from unittest.mock import Mock


class FakePermissionError(Exception):
    pass


class FakeAuthenticationError(Exception):
    pass


def fail(message, exception=ValueError):
    raise exception(message)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = {name: sys.modules.get(name) for name in ("frappe", "erpnext", "hrms")}
        self.frappe = types.ModuleType("frappe")
        self.frappe.__version__ = "16.36.1"
        self.frappe.session = types.SimpleNamespace(user="employee@example.test")
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=1), get_singles_dict=Mock(return_value={}), set_value=Mock())
        self.frappe.PermissionError = FakePermissionError
        self.frappe.AuthenticationError = FakeAuthenticationError
        self.frappe.ValidationError = ValueError
        self.frappe.throw = fail
        self.frappe.whitelist = lambda **kwargs: lambda function: function
        self.frappe.get_roles = Mock(return_value=["Employee"])
        self.frappe.get_list = Mock(return_value=[{
            "name": "HR-EMP-TEST", "employee_name": "Mobile Test",
            "company": "Test Company", "status": "Active",
            "user_id": "employee@example.test",
        }])
        self.frappe.get_installed_apps = Mock(return_value=["frappe", "erpnext", "hrms"])
        self.fields = {"employee", "time", "log_type", "device_id"}
        self.frappe.get_meta = Mock(return_value=types.SimpleNamespace(has_field=self.fields.__contains__))
        sys.modules["frappe"] = self.frappe
        for name in ("erpnext", "hrms"):
            module = types.ModuleType(name)
            module.__version__ = "16.0.0"
            sys.modules[name] = module
        import maas_attendance_mobile.api
        self.api = importlib.reload(maas_attendance_mobile.api)
        import maas_attendance_mobile.attendance
        self.attendance = importlib.reload(maas_attendance_mobile.attendance)
        import maas_attendance_mobile.devices
        self.devices = importlib.reload(maas_attendance_mobile.devices)
        # Scenarios run with explicit effective rules; test_policy covers policy.resolve().
        import maas_attendance_mobile.policy
        self.policy = importlib.reload(maas_attendance_mobile.policy)
        self.real_resolve = self.policy.resolve
        self.rules = dict(online=True, offline=True, offline_sync=True, require_photo=False, photo_optional=False,
                          require_location=False, require_device=False, standard_checkin=False)
        self.policy.resolve = lambda employee: dict(self.rules)
        # Photo scenarios use synthetic images; test_faces covers own_face_problem().
        import maas_attendance_mobile.faces
        self.faces = maas_attendance_mobile.faces
        self.real_face_check = self.faces.own_face_problem
        self.face_problem = None
        self.faces.own_face_problem = lambda employee, company, content: self.face_problem

    def tearDown(self):
        self.policy.resolve = self.real_resolve
        self.faces.own_face_problem = self.real_face_check
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_profile_uses_server_identity(self):
        result = self.api.profile()
        self.assertEqual(result["employee"]["name"], "HR-EMP-TEST")
        self.assertEqual(self.frappe.get_list.call_args.kwargs["filters"],
                         {"user_id": self.frappe.session.user})
        self.assertNotIn("date_of_birth", result["employee"])

    def test_cannot_choose_another_employee(self):
        with self.assertRaises(TypeError):
            self.api.profile(employee="OTHER")

    def test_guest_is_rejected(self):
        self.frappe.session.user = "Guest"
        with self.assertRaises(FakeAuthenticationError):
            self.api.profile()
        self.frappe.get_list.assert_not_called()

    def test_disabled_user_is_rejected(self):
        self.frappe.db.get_value.return_value = 0
        with self.assertRaises(FakePermissionError):
            self.api.profile()

    def test_missing_role_is_rejected(self):
        self.frappe.get_roles.return_value = []
        with self.assertRaises(FakePermissionError):
            self.api.profile()

    def test_missing_or_ambiguous_mapping_is_rejected(self):
        for rows in ([], [{}, {}]):
            self.frappe.get_list.return_value = rows
            with self.assertRaises(FakePermissionError):
                self.api.profile()

    def test_inactive_or_wrong_employee_is_rejected(self):
        for change in ({"status": "Left"}, {"user_id": "other@example.test"}):
            row = {"status": "Active", "user_id": "employee@example.test"}
            row.update(change)
            self.frappe.get_list.return_value = [row]
            with self.assertRaises(FakePermissionError):
                self.api.profile()

    def test_database_permission_failure_is_not_bypassed(self):
        self.frappe.get_list.side_effect = FakePermissionError("denied")
        with self.assertRaises(FakePermissionError):
            self.api.profile()

if __name__ == "__main__":
    unittest.main()
