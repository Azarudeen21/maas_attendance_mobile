# Changelog

## 0.20.0
- One model: site switches, one Company Policy per company, Employee boxes.
  Test mode, test employees and production enrollment (Mobile Attendance
  Employee) removed. All app check-ins now feed HRMS attendance.
- Upgrade patch keeps access exactly as before: unticks Online/Offline (with a
  comment) for employees who could not use that channel; in a production
  company the old dedicated test account loses access. Old columns and the
  enrollment table are kept for rollback.
- Mobile Attendance workspace with number cards; HR controls page removed.
- Removed `capabilities`, `hr_overview`, deployment diagnostics and unused
  response keys (`test_mode`, `*_test_submission_enabled`,
  `offline_auto_accept_enabled`, `automatically_accepted`).
- Employee Checkin guard now applies only to an app user's own check-ins.

## 0.19.0
Every valid offline upload becomes an Employee Checkin automatically.

## 0.18.x
Normal HRMS check-in for Online + Without Photo; Create Face Enrollment button.

## 0.17.0
Employee check-in boxes (Online, Offline, With Photo, Without Photo).

## 0.15.0 – 0.16.0
Face enrollment, Test face match and supervisor face check-in.

## 0.3.0 – 0.14.0
Online punches, GPS workplaces, approved devices, history, offline claims with
HR review, private punch photos, mobile site connection, photo retention.
