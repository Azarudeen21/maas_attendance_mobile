import importlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import test_api
from test_attendance import Flags


class Record(dict):
    __getattr__ = dict.__getitem__

    def as_dict(self):
        return dict(self)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests(); self.base.setUp()
        self.frappe = self.base.frappe
        self.frappe.flags = Flags()
        import maas_attendance_mobile.photo_retention
        import maas_attendance_mobile.photos
        self.service = importlib.reload(maas_attendance_mobile.photo_retention)
        self.photos = importlib.reload(maas_attendance_mobile.photos)
        self.guard_patch = patch.object(self.photos, 'require_file_guard')
        self.guard_patch.start()
        self.now = datetime(2026, 10, 2, 12)
        self.policy = Record(enable_photo_cleanup=1, photo_retention_days=30)
        self.record = Record(doctype=self.photos.AUDIT_TYPES[0], name='ID', employee='EMP',
            punch_photo='/private/files/punch.jpg', photo_company='A',
            photo_uploaded_at=self.now - timedelta(days=31), photo_retention_hold=0)
        self.job = Record(status='Pending', source_doctype=self.record.doctype,
            source_name='ID', photo_url=self.record.punch_photo, company='A')
        self.file = dict(name='FILE', is_private=1, attached_to_doctype=self.record.doctype,
            attached_to_name='ID', attached_to_field='punch_photo', content_hash='HASH')
        self.frappe.get_roles.return_value = ['HR Manager']
        self.frappe.get_doc = Mock(side_effect=lambda dt, name: self.job if dt == self.service.JOB else self.record)
        self.frappe.db.get_value = Mock(return_value=self.policy)
        self.frappe.db.set_value = Mock()
        self.frappe.db.exists = Mock(return_value=False)
        self.frappe.get_all = Mock(return_value=[self.file])
        self.frappe.delete_doc = Mock()
        self.time_patch = patch.object(self.service, '_now', return_value=self.now)
        self.time_patch.start()

    def tearDown(self):
        self.guard_patch.stop(); self.time_patch.stop(); self.base.tearDown()

    def test_zero_hold_missing_company_or_upload_keep_photo(self):
        self.assertTrue(self.service.due(self.record, self.policy, self.now))
        for change in ({'photo_retention_hold': 1}, {'photo_uploaded_at': None},
                       {'photo_company': None}, {'photo_removed_at': self.now}, {'punch_photo': None}):
            self.assertFalse(self.service.due(self.record | change, self.policy, self.now))
        self.assertFalse(self.service.due(self.record, {'photo_retention_days': 0}, self.now))

    def test_days_reject_fractions_negative_huge_and_noninteger(self):
        for value in (-1, 3651, 0.5, '30.0', 'abc'):
            with self.assertRaises(ValueError): self.service.retention_days(value)
        self.assertEqual(self.service.retention_days('30'), 30)

    def test_offline_pending_and_recently_reviewed_cannot_expire(self):
        record = self.record | {'doctype': self.photos.AUDIT_TYPES[1], 'status': 'Pending Review'}
        self.assertFalse(self.service.due(record, self.policy, self.now))
        for status in ('Approved', 'Rejected'):
            final = record | {'status': status, 'reviewed_at': self.now - timedelta(days=1)}
            self.assertFalse(self.service.due(final, self.policy, self.now))
            self.assertTrue(self.service.due(final | {'reviewed_at': self.now - timedelta(days=30)}, self.policy, self.now))

    def test_cleanup_requires_current_policy_same_company_and_link(self):
        for policy, record in ((self.policy | {'enable_photo_cleanup': 0}, self.record),
                              (self.policy, self.record | {'photo_company': 'B'}),
                              (self.policy, self.record | {'punch_photo': '/private/files/other.jpg'}),
                              (self.policy, self.record | {'photo_retention_hold': 1})):
            self.frappe.db.get_value.return_value = policy
            self.record = Record(record)
            self.service._finish('JOB')
            self.frappe.delete_doc.assert_not_called()

    def test_shared_public_unattached_wrong_field_or_unhashed_files_stay(self):
        original = dict(self.file)
        for files in ([original, original], [], [original | {'is_private': 0}],
                      [original | {'attached_to_name': 'OTHER'}],
                      [original | {'attached_to_field': 'other'}], [original | {'content_hash': None}]):
            self.frappe.get_all.return_value = files
            self.service._finish('JOB')
            self.frappe.delete_doc.assert_not_called()
        self.frappe.get_all.return_value = [original]
        self.frappe.db.exists.return_value = True
        self.service._finish('JOB')
        self.frappe.delete_doc.assert_not_called()

    def test_success_preserves_receipt_and_hash_and_completes_durable_intent(self):
        def delete(dt, name, **kwargs):
            self.assertEqual((dt, name), ('File', 'FILE'))
            self.assertEqual(self.frappe.flags.get('maas_photo_delete'),
                {'doctype': self.record.doctype, 'name': 'ID', 'file': 'FILE', 'url': self.record.punch_photo})
        self.frappe.delete_doc.side_effect = delete
        self.service._finish('JOB')
        self.assertEqual(self.frappe.db.set_value.call_count, 2)
        audit = self.frappe.db.set_value.call_args_list[0].args[2]
        self.assertEqual(set(audit), {'punch_photo', 'photo_removed_at', 'photo_cleanup'})
        self.assertEqual(self.frappe.db.set_value.call_args_list[1].args[2]['status'], 'Completed')
        self.assertIsNone(self.frappe.flags.get('maas_photo_delete'))

    def test_storage_failure_never_marks_completed_and_restores_context(self):
        self.frappe.delete_doc.side_effect = OSError('storage failure')
        with self.assertRaises(OSError): self.service._finish('JOB')
        self.frappe.db.set_value.assert_not_called()
        self.assertIsNone(self.frappe.flags.get('maas_photo_delete'))

    def test_deletion_context_is_bound_to_exact_file_and_only_trash(self):
        values = self.file | {'file_url': self.record.punch_photo}
        doc = types.SimpleNamespace(get=values.get, get_doc_before_save=lambda: None)
        self.frappe.flags.maas_photo_delete = {'doctype': self.record.doctype, 'name': 'ID',
            'file': 'FILE', 'url': self.record.punch_photo}
        self.photos.guard_file(doc, 'on_trash')
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(doc)
        values['name'] = 'OTHER'
        with self.assertRaises(test_api.FakePermissionError): self.photos.guard_file(doc, 'on_trash')

    def test_guest_or_employee_cannot_preview_or_change_holds(self):
        self.frappe.get_roles.return_value = ['Employee']
        with self.assertRaises(test_api.FakePermissionError): self.service.preview('A')
        with self.assertRaises(test_api.FakePermissionError): self.service.set_hold(self.record.doctype, 'ID', 1, 'Keep for investigation')
        self.frappe.get_doc.assert_not_called()

    def test_preview_checks_policy_access_and_uses_permission_scoped_queries(self):
        self.policy.update(company='A', check_permission=Mock())
        # Record methods are attributes, not keys, in the real ORM.
        policy = types.SimpleNamespace(check_permission=Mock(), get=self.policy.get, as_dict=self.policy.as_dict)
        self.frappe.get_doc.return_value = policy; self.frappe.get_doc.side_effect = None
        self.frappe.get_list.return_value = [dict(self.record)]
        result = self.service.preview('A')
        policy.check_permission.assert_called_once_with('read')
        self.assertEqual(result['counts']['eligible'], 1)
        self.frappe.get_all.assert_not_called()
        for call in self.frappe.get_list.call_args_list:
            self.assertEqual(call.kwargs['filters']['photo_company'], 'A')
        self.assertNotIn('punch.jpg', str(result))

    def test_hold_locked_permission_checked_and_reason_audited(self):
        self.record['check_permission'] = Mock(); self.record['add_comment'] = Mock()
        result = self.service.set_hold(self.record.doctype, 'ID', 1, ' Retain during investigation ')
        self.assertTrue(result['hold'])
        self.record.check_permission.assert_called_once_with('read')
        self.assertTrue(self.frappe.db.get_value.call_args.kwargs['for_update'])
        self.record.add_comment.assert_called_once()
        self.assertEqual(self.frappe.db.set_value.call_args.args[2]['photo_hold_reason'], 'Retain during investigation')

    def test_scheduler_plans_intent_without_deleting_in_same_transaction(self):
        self.record['insert'] = Mock(return_value=self.record)
        def all_rows(dt, **kwargs):
            if dt == self.service.JOB: return []
            if dt == self.service.POLICY: return [dict(self.policy, company='A')]
            if dt == self.photos.AUDIT_TYPES[0]: return [dict(self.record)]
            self.assertEqual(kwargs['filters']['status'], ['in', ['Approved', 'Rejected']])
            return []
        self.frappe.get_all.side_effect = all_rows
        self.frappe.get_doc.side_effect = lambda data: Record(data, insert=Mock())
        self.service.run()
        created = self.frappe.get_doc.call_args.args[0]
        self.assertEqual(created['status'], 'Pending')
        self.assertEqual(created['company'], 'A')
        self.frappe.delete_doc.assert_not_called()
        self.frappe.db.set_value.assert_called_once_with(self.record.doctype, 'ID', 'photo_cleanup', 'online-ID', update_modified=False)
        self.assertIsNone(self.frappe.flags.get('maas_photo_cleanup_write'))

    def test_scheduler_failure_rolls_back_one_intent_and_continues_others(self):
        self.frappe.get_all.side_effect = [[{'name': 'FIRST'}, {'name': 'SECOND'}], []]
        self.frappe.db.savepoint = Mock(); self.frappe.db.rollback = Mock()
        self.frappe.log_error = Mock()
        with patch.object(self.service, '_finish', side_effect=[OSError('secret path'), None]) as finish:
            self.service.run()
        self.assertEqual(finish.call_count, 2)
        self.frappe.db.rollback.assert_called_once_with(save_point='maas_photo_cleanup')
        self.assertNotIn('secret path', str(self.frappe.log_error.call_args))
        self.assertEqual(self.frappe.db.set_value.call_count, 2)

    def test_file_controller_rejects_before_physical_delete_or_overwrite(self):
        effects = []
        class BaseFile:
            def get(self, key): return values.get(key)
            def get_doc_before_save(self): return None
            def on_trash(self): effects.append('delete')
            def save_file(self, *args, **kwargs): effects.append('write')
        module = types.ModuleType('frappe.core.doctype.file.file'); module.File = BaseFile
        values = self.file | {'file_url': self.record.punch_photo}
        with patch.dict(sys.modules, {'frappe.core.doctype.file.file': module}):
            import maas_attendance_mobile.protected_file
            protected = importlib.reload(maas_attendance_mobile.protected_file).AttendanceProtectedFile()
            with self.assertRaises(test_api.FakePermissionError): protected.on_trash()
            with self.assertRaises(test_api.FakePermissionError): protected.save_file(content=b'changed')
            self.assertEqual(effects, [])
            self.frappe.flags.maas_photo_delete = {'doctype': self.record.doctype, 'name': 'ID',
                'file': 'FILE', 'url': self.record.punch_photo}
            protected.on_trash()
            self.assertEqual(effects, ['delete'])
            values['attached_to_doctype'] = 'Unrelated Document'
            protected.save_file(content=b'normal attachment')
            self.assertEqual(effects, ['delete', 'write'])


if __name__ == '__main__': unittest.main()
