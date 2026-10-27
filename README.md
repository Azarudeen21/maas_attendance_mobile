# MAAS Attendance Mobile

Frappe app that adds mobile check-in to Frappe HR (ERPNext + HRMS). It works with
the **Frappe HR mobile app** (`maas_hr_mobile`), which opens the site's normal
`/hrms` screens and adds camera, GPS, offline saving and a supervisor face scan.

## What it does

- **Online check-in/out** from the app, with optional selfie, GPS workplace check
  and approved-phone check.
- **Offline check-in**: the phone saves punches (encrypted) and uploads them when
  it is back online. Each valid upload becomes an Employee Checkin at its
  original time. Only ordering conflicts wait for HR review.
- **Per-employee rules** on the Employee form (Mobile Check-in section).
- **Normal HRMS check-in** for employees set to Online + Without Photo.
- **Supervisor face check-in**: a supervisor scans an employee with their own
  approved phone; the server identifies the face (bundled YuNet + SFace models).
  Without a connection the scan is saved and identified when it uploads; the
  server then records the employee's next IN or OUT at the scan time.
- **Face enrollment** from Employee photos, single or bulk, and **Test face match**.
- **Phone registration** with HR approval and revocation.
- **Private photos** with per-company retention and an hourly cleanup job.
- **Mobile Attendance workspace** for HR.

All app check-ins are normal Employee Checkins: they feed HRMS attendance.

## Requirements

Frappe, ERPNext and HRMS v15 (verified on 15.120 / 15.121 / 15.64.3). v16 is
allowed by the app metadata but not yet verified. Python packages Pillow, numpy
and opencv-python-headless are installed with the app. The worker (bulk face
enrollment) and scheduler (photo cleanup) must run.

## Setup (System Manager)

```bash
bench get-app <repository URL>
bench --site <site> install-app maas_attendance_mobile
bench --site <site> migrate
```

Every switch starts off. Then:

1. **Mobile Attendance Settings**
   - General → *Enable Mobile Attendance* (master switch).
   - Offline Sync → *Enable Offline Sync* if employees may check in offline.
   - Mobile Site Connection → enable it and select a public OAuth Client
     (Authorization Code, Code response, Token Endpoint Auth Method None,
     scope `all`, redirect URI `com.maasconsult.attendance:/oauth2redirect`).
   - Face Recognition → enable, adjust threshold (default 0.363) and margin
     (default 0.05). Supervisor Check-in → enable supervisor face check-in.
2. **Mobile Attendance Company Policy** (one per company): *Enable Mobile
   Attendance*, *Require Photo*, *Require GPS Workplace*, *Require Approved
   Device*, *Allow Offline*, photo retention days and automatic cleanup.
3. **Mobile Attendance Workplace** (if GPS is required): centre, radius and
   maximum GPS uncertainty per company.
4. **Employee → Mobile Check-in**: tick the boxes for each employee (below).
5. Give supervisors the **MAAS Attendance Supervisor** role.

### Who may check in

An employee may use a channel when **all** of these allow it:

| Channel | Site switch | Company policy | Employee box |
|---|---|---|---|
| Online | Enable Mobile Attendance | Enabled | Online Check in |
| Offline | Enable Mobile Attendance + Enable Offline Sync | Enabled + Allow Offline | Offline Check In |

A box never re-enables a switch that is off. With no channel box ticked the
employee cannot use the app. Both boxes: online when connected, offline when not.

With face recognition on, a check-in photo must match the employee's own
enrolled face (an employee without an enrolled face cannot check in with a
photo). An online punch whose face does not match is refused and nothing is
stored; the app shows why and the employee can check in again. An offline punch
whose face does not match is kept in Offline Claims to Review ("Face check
failed") with its photo and original time for HR to approve or reject. The
photo and a *With Photo* box appear on the Employee Checkin.

Photo boxes: *With Photo* only → photo required; *Without Photo* only → no photo
(even if the company requires photos); both → optional (the app asks);
neither → the company setting. *Online + Without Photo* (without *With Photo*)
uses the normal HRMS Check in button.

Online check-ins need an approved phone when the company requires it; offline
uploads always do. An employee whose Online check-in is not enabled cannot use
the normal HRMS Check in button, and the app lets offline-only employees punch
only when there is no connection. Employees with a box ticked, or in a company
where mobile attendance is on, cannot add or delete their own Employee
Checkins directly; HR users recording check-ins for others are not affected.

## Daily HR work

Open the **Mobile Attendance** workspace.

- **Pending Devices**: open a phone and *Approve device* or *Revoke device*.
  Nobody approves their own phone; revoked phones cannot be reactivated.
- **Offline Claims to Review**: ordering conflicts only. *Approve and create
  check-in* or *Reject claim* with a reason. A different HR user must review.
- **Face Enrollment**: Employee form → *Face Recognition → Create Face
  Enrollment*; list → *Enroll from Employee photos* (whole company) or *+ Add*
  with a private photo; list → *Test face match*. Confirm consent first.
- **Photo retention**: Company Policy → *Preview photo retention*; on a receipt
  or offline claim → *Keep photo on hold*. Removals are logged in Mobile
  Attendance Photo Cleanup.

## Roles

| Role | Access |
|---|---|
| Employee | Own check-ins, phone registration and history through the app |
| MAAS Attendance Supervisor | Face check-in of employees in their own company, from an approved phone |
| HR Manager | Devices, offline review, workplaces, face tools, photo holds; reads settings and policies |
| System Manager | Everything, including Settings and Company Policies |

## Records

| DocType | Purpose |
|---|---|
| Mobile Attendance Settings | Site switches, site connection, face recognition |
| Mobile Attendance Company Policy | Per-company rules and photo retention |
| Mobile Attendance Transaction | Immutable receipt per online or supervisor punch |
| Mobile Attendance Offline Claim | Evidence and result of each offline upload |
| Mobile Attendance Device | Registered phones and approval history |
| Mobile Attendance Workplace | GPS workplaces |
| Mobile Attendance Face Enrollment | Face photos and face codes |
| Mobile Attendance Photo Cleanup | Audit of removed photos |

## API

`/api/method/maas_attendance_mobile.api.<method>`, using the signed-in session.
The server always derives the employee from the login.

| Method | Caller | Purpose |
|---|---|---|
| `mobile_setup` (GET, guest) | App | Public OAuth client and site label |
| `profile`, `attendance_status` (GET) | App | Own employee and current rules |
| `punch` (POST) | App | Online check-in/out |
| `register_device`, `device_status` (POST) | App | Phone registration |
| `attendance_history` (GET) | App | Own check-ins, 20 per page |
| `submit_offline_claim` (POST), `offline_claim_status` (GET) | App | Offline upload and result |
| `supervisor_identify`, `supervisor_punch` (POST) | App (supervisor) | Face scan and confirmed check-in |
| `supervisor_offline_punch` (POST) | App (supervisor) | Upload a scan saved offline; the server identifies the employee |
| `review_offline_claim` (POST) | HR | Approve or reject a conflict |
| `face_test_match`, `face_enroll_employee`, `face_enroll_employee_photos` (POST) | HR | Face tools |
| `photo_retention_preview` (GET), `photo_retention_hold` (POST) | HR | Photo retention |

`attendance_status` still returns `attendance_mode: "production"` and `punch` /
`submit_offline_claim` still accept `attendance_mode` for app 1.3.x; both will be
removed in a later release.

## Security and privacy

Employee identity comes from the server session; supervisor scans are identified
again on the server before a check-in. Phone secrets are stored only as hashes
(not hardware attestation). Receipts, offline claims and cleanup audits are
immutable. Photos are private with EXIF removed. Face codes are biometric data:
record consent, limit access to HR and delete an enrollment to erase it. GPS and
photos are evidence, not proof of presence; there is no liveness detection.

## Tests

```bash
# Mocked unit tests (from the app repository root)
../../env/bin/python -m unittest discover -s tests
# Real-database tests (site needs allow_tests; everything is rolled back)
bench --site <site> run-tests --module maas_attendance_mobile.tests.test_integration
```

See CHANGELOG.md for version history.
