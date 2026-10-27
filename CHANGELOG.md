# Changelog

## 0.20.4
- An offline punch whose photo does not match the employee is kept for HR
  review ("Face check failed: …") with its photo and original time, instead
  of being refused. HR approves it (check-in at the original time) or rejects
  it, keeping the photo as evidence. Online punches are still refused at once.

## 0.20.3
- Offline supervisor check-in: a scan saved without a connection uploads to
  `supervisor_offline_punch`; the server identifies the face and records the
  employee's next action (IN after OUT, OUT after IN) at the scan time. The
  employee needs Online or Offline check-in, and offline sync must be allowed
  for the site and company. Unrecognised or unrecordable scans are refused
  (ScanRefused) and nothing is stored.

## 0.20.2
- Own check-in photos must show the employee: with face recognition on, the
  photo is matched against the whole company and must be this employee's
  enrolled face. A punch whose photo is not the employee's (online or offline)
  is refused and nothing is stored; app 1.4.3 drops it, shows why and lets the
  employee check in again.
- Employee Checkin shows the punch photo (Check-in Photo) and a read-only
  With Photo box. Photo cleanup also removes it from the check-in.
- An unregistered phone reports "Not registered" instead of a permission error.

## 0.20.1
- Normal HRMS check-in only for employees whose Online check-in is enabled.
  In a company with mobile attendance on, employees with no boxes ticked can
  no longer check themselves in from Desk or /hrms.
- App 1.4.1: offline-only employees can punch only when there is no connection.

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
