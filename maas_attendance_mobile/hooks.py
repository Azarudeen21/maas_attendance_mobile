app_name = "maas_attendance_mobile"
app_title = "MAAS Attendance Mobile"
app_publisher = "MAAS Consult"
app_description = "Employee mobile integration for Frappe HR"
app_email = "azar.maasconsult@gmail.com"
app_license = "Proprietary"

required_apps = ["erpnext", "hrms"]

doc_events = {
    "Employee Checkin": {
        "before_insert": "maas_attendance_mobile.attendance.guard_checkin",
        "before_validate": "maas_attendance_mobile.attendance.guard_checkin",
        "on_trash": "maas_attendance_mobile.attendance.guard_delete",
    }
}
