"""Enforce evidence guards BEFORE Frappe touches physical storage.

File.on_trash deletes bytes before ordinary doc_events hooks execute. A
controller override is required on supported v15/v16; an on_trash hook alone
cannot protect a photograph from deletion or a whitelisted optimization.
"""
from frappe.core.doctype.file.file import File

from maas_attendance_mobile.photos import guard_file


class AttendanceProtectedFile(File):
    def on_trash(self):
        guard_file(self, 'on_trash')
        return super().on_trash()

    def save_file(self, *args, **kwargs):
        guard_file(self, 'before_validate')
        return super().save_file(*args, **kwargs)
