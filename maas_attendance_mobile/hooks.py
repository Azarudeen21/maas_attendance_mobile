app_name = "maas_attendance_mobile"
app_title = "MAAS Attendance Mobile"
app_publisher = "MAAS Consult"
app_description = "Employee mobile integration for Frappe HR"
app_email = "azar.maasconsult@gmail.com"
app_license = "Proprietary"

required_apps = ["erpnext", "hrms"]

override_doctype_class = {
    "File": "maas_attendance_mobile.protected_file.AttendanceProtectedFile",
}

scheduler_events = {
    "hourly": ["maas_attendance_mobile.photo_retention.run"],
}

doctype_js = {
    "Mobile Attendance Transaction": "public/js/photo_retention.js",
    "Mobile Attendance Offline Claim": "public/js/photo_retention.js",
}

doc_events = {
    "File": {
        "before_validate": "maas_attendance_mobile.photos.guard_file",
        "on_trash": "maas_attendance_mobile.photos.guard_file",
    },
    "Employee Checkin": {
        "before_insert": "maas_attendance_mobile.attendance.guard_checkin",
        "before_validate": "maas_attendance_mobile.attendance.guard_checkin",
        "on_trash": "maas_attendance_mobile.attendance.guard_delete",
    }
}

after_install = "maas_attendance_mobile.supervisor.ensure_role"
after_migrate = "maas_attendance_mobile.supervisor.ensure_role"
