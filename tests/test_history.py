import importlib
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock

import test_api


class Expression:
    def __init__(self, value): self.value = value
    def __and__(self, other): return Expression(('and', self.value, other.value))
    def __or__(self, other): return Expression(('or', self.value, other.value))


class Field:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return Expression(('eq', self.name, value))
    def __lt__(self, value): return Expression(('lt', self.name, value))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.context = test_api.ApiTests()
        self.context.setUp()
        self.frappe = self.context.frappe
        self.previous = {name: sys.modules.get(name) for name in ('frappe.query_builder', 'frappe.utils')}
        builder = types.ModuleType('frappe.query_builder')
        builder.Order = types.SimpleNamespace(desc='desc')
        utils = types.ModuleType('frappe.utils')
        utils.get_system_timezone = lambda: 'Asia/Riyadh'
        sys.modules['frappe.query_builder'] = builder
        sys.modules['frappe.utils'] = utils
        table = types.SimpleNamespace(**{name: Field(name) for name in ('name', 'time', 'log_type', 'employee', 'skip_auto_attendance')})
        self.query = Mock()
        for method in ('select', 'where', 'orderby', 'limit'):
            getattr(self.query, method).return_value = self.query
        self.query.run.return_value = []
        self.frappe.qb = types.SimpleNamespace(DocType=Mock(return_value=table), from_=Mock(return_value=self.query))
        import maas_attendance_mobile.history
        self.history = importlib.reload(maas_attendance_mobile.history)

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value
        self.context.tearDown()

    def test_identity_scoped_read_and_private_fields_excluded(self):
        self.query.run.return_value = [{'name': 'CK-1', 'time': datetime(2026, 10, 1, 23), 'log_type': 'IN', 'skip_auto_attendance': 1, 'device_secret': 'secret', 'latitude': 42}]
        response = self.context.api.attendance_history()
        self.assertEqual(self.query.where.call_args_list[0].args[0].value, ('eq', 'employee', 'HR-EMP-TEST'))
        self.assertEqual(set(response['records'][0]), {'name', 'time', 'log_type', 'skip_auto_attendance'})
        self.assertEqual(response['user'], 'employee@example.test')
        self.assertEqual(response['timezone'], 'Asia/Riyadh')
        self.assertTrue(response['records'][0]['skip_auto_attendance'])
        self.query.limit.assert_called_once_with(21)
        self.query.run.assert_called_once_with(as_dict=True)

    def test_pagination_uses_time_and_name_with_employee_scope(self):
        at = datetime(2026, 10, 1, 23)
        self.query.run.return_value = [{'name': f'CK-{i:03}', 'time': at, 'log_type': 'OUT'} for i in range(21)]
        first = self.context.api.attendance_history()
        self.assertEqual(len(first['records']), 20)
        self.assertEqual(self.history.decode_cursor(first['next_cursor']), (at, 'CK-019'))
        self.query.where.reset_mock()
        self.query.run.return_value = []
        second = self.context.api.attendance_history(first['next_cursor'])
        self.assertEqual(self.query.where.call_args_list[0].args[0].value, ('eq', 'employee', 'HR-EMP-TEST'))
        self.assertEqual(self.query.where.call_args_list[1].args[0].value, ('or', ('lt', 'time', at), ('and', ('eq', 'time', at), ('lt', 'name', 'CK-019'))))
        self.assertIsNone(second['next_cursor'])

    def test_invalid_cursors_fail_before_query(self):
        for cursor in ('invalid!', 'a' * 1001, 42, self.history.encode_cursor({'time': '2026-10-01T23:00:00Z', 'name': 'CK-1'})):
            with self.assertRaises(ValueError): self.context.api.attendance_history(cursor)
        self.frappe.qb.from_.assert_not_called()

    def test_guest_and_employee_selector_rejected(self):
        with self.assertRaises(TypeError): self.context.api.attendance_history(employee='OTHER')
        self.frappe.session.user = 'Guest'
        with self.assertRaises(test_api.FakeAuthenticationError): self.context.api.attendance_history()
        self.frappe.qb.from_.assert_not_called()

    def test_legacy_schema_and_empty_history(self):
        self.query.run.return_value = [{'name': 'CK-1', 'time': datetime(2026, 10, 1), 'log_type': None}]
        response = self.context.api.attendance_history()
        self.assertIsNone(response['records'][0]['skip_auto_attendance'])
        self.assertEqual(len(self.query.select.call_args.args), 3)
        self.query.run.return_value = []
        self.assertEqual(self.context.api.attendance_history()['records'], [])


if __name__ == '__main__': unittest.main()
