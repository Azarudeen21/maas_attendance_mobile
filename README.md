# MAAS Attendance Mobile — Frappe app

Version 0.2.0 adds a disabled-by-default online TEST punch service. This is a
staged development checkpoint, not a complete production attendance app.
Version 0.1.0 profile/capabilities were verified on the v16 test site; this
release still requires deployment and live database tests. Flutter currently
shows the profile only. Mobile attendance buttons follow backend validation.

## Installation and update

Keep pyproject.toml and the maas_attendance_mobile package at repository root.
Frappe, ERPNext and HRMS must already be installed. Bench metadata allows
v15/v16; v15 live installation and compatibility remain unverified.
No new pip or Flutter dependency is required by this release.

For an existing installation deploy the new revision and migrate the test site
through Frappe Cloud; do not uninstall/reinstall. Migration adds two DocTypes:
Mobile Attendance Settings (Single) and Mobile Attendance Transaction (audit
receipt). No test gate is enabled and no checkin is created by installation.
Keep a current test-site backup before migration.

## API contract

Frappe wraps returns in message. OAuth bearer authentication and browser
sessions both use the server session identity. Paths below are prefixed by
/api/method/maas_attendance_mobile.api.

- GET profile: active employee linked to current enabled Employee-role login.
- GET capabilities: actual host schema/version and capability flags.
- GET attendance_status: current employee's latest checkin and test gate.
- POST punch: allowlisted online test IN/OUT only.

Punch accepts request_id (canonical UUID v4), log_type (IN or OUT), timestamp
(ISO 8601 with timezone offset), device_id (installation UUID v4), app_version
(1–40 letters/digits/version punctuation). Example structure:

```json
{
  "request_id": "ad12fa40-4a3f-4ee5-a365-ff6e4dd11014",
  "log_type": "IN",
  "timestamp": "2026-10-01T19:44:28Z",
  "device_id": "ddbc4a14-88ce-4c94-8893-a6f6b3cbd056",
  "app_version": "0.2.0"
}
```

Example values are illustrative. Generate a fresh UUID/current timestamp for a
NEW punch. Retain every request value when retrying a lost response. Device
UUID is audit metadata, not trusted device approval. No employee/user ID,
skip-attendance, GPS or face-verification input authorizes a punch. Employee
lookup enforces normal read permissions, enabled login, Employee role, exactly
one linked Employee and Active status.

## Test policy

As System Manager open Mobile Attendance Settings. Leave Enable Online Test
Attendance unchecked for initial deployment/API validation. Then select ONLY
the dedicated Mobile Test employee and enable for supervised tests.
attendance_submission_enabled, offline_submission_enabled and
face_verification_enabled always remain false. Only
online_test_submission_enabled can become true for the selected employee.

Every service-created standard Employee Checkin has Skip Auto Attendance = 1
and a MAAS-TEST installation ID. HRMS validations still execute. HR Settings
requiring location may reject GPS-free tests; do not disable HR security policy
to force a test through. The service converts the ORIGINAL offset-bearing
instant to site timezone at second resolution (Asia/Riyadh on verified site).
New online events may be at most 15 minutes old or 2 minutes ahead; offline
queue acceptance is not implemented. No history requires IN first; then IN/OUT
alternate. Equal/older times and latest logs without IN/OUT fail closed.
State uses standard Employee Checkin history: avoid simultaneous manual HR
edits for the dedicated test employee during these tests.

Mobile requests lock the Employee row and revalidate mapping/status/test gate.
A UUID-named receipt and unique request_id column protect idempotency. Hash
binds authenticated user/employee and canonical payload. An exact retry returns
original receipt without another write even if the timestamp has become old.
Changed payloads and cross-employee UUID reuse reject. Standard checkin and
receipt use ONE Frappe POST transaction with no intermediate commit; exceptions
propagate so Frappe rolls back both writes. Real uniqueness/rollback/locking
still require live database validation, not just mocked tests.

Hooks block the CONFIGURED test login from standard ORM insert/edit/delete
routes, even with test gate disabled. Other HR/admin workflows remain standard.
This is scoped test enforcement, not site-wide production attendance policy.
Changing the configured test employee changes which login is guarded.
System Manager can read receipts; controllers reject ordinary edits/deletes
including Administrator. Receipts preserve original request audit information.

## Checks

From repository root: python -m unittest discover -s tests -v
These tests mock Frappe and verify pure rules/service contracts. They do not
prove database rollback, locks, hook dispatch, OAuth middleware or installation.
Required dedicated test-site checks before Flutter buttons:

1. Gate disabled: app_version 0.2.0, test flag false, status readable only by
   authenticated linked employee. Guests reject without employee details.
2. Enable only Mobile Test. Fresh IN creates one standard checkin + receipt,
   correct employee/local time and Skip Auto Attendance checked.
3. Same payload retry returns same checkin, duplicate true, counts unchanged.
   Two simultaneous identical requests must produce only one checkin/receipt.
4. Same UUID changed OUT/device/time rejects. New repeated IN rejects; fresh OUT
   succeeds. Equal/old timestamps reject without rows.
5. Other ordinary employee, disabled/inactive/unlinked logins cannot submit.
   Spoofed employee input cannot select another employee.
6. Direct standard resource writes as configured test login reject. Normal
   administrator workflow retains standard behavior; do not add user roles.
7. On an isolated test clone inject receipt-insert failure; confirm neither
   standard checkin nor receipt commits. This is not proven by a mocked test.
8. Disable the gate after tests. GPS, face, approved devices, offline sync,
   history, production policy and release hardening remain future phases.
