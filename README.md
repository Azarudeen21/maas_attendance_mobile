# MAAS Attendance Mobile — Frappe app

Version 0.17.0 adds per-employee check-in checkboxes; 0.16.0 added supervisor face check-in.
Version 0.10.0 adds customer-site mobile onboarding with public PKCE setup.
Version 0.8.0 added an HR control screen and stronger settings validation.
Version 0.7.0 added private punch photos with GPS and the original button time,
without face recognition or liveness. It also includes offline test claims with explicit HR review, alongside
employee check-in history, device approval and server GPS workplace checks.
Authentication, profile and basic online test punches were
verified on the v16 test site. This release requires migration and live GPS
policy validation. It is not a complete production attendance app.

## Installation and update

Keep pyproject.toml and the maas_attendance_mobile package at repository root.
Frappe, ERPNext and HRMS must already be installed. Bench metadata allows
v15/v16; v15 live installation and compatibility remain unverified.
Pillow is required for JPEG pixel validation and metadata removal. The Flutter
app uses camera for foreground photos and geolocator for foreground GPS capture.

For an existing installation deploy the new revision and migrate the test site
through Frappe Cloud; do not uninstall/reinstall. Migration adds Mobile
Attendance Workplace, Mobile Attendance Device, and GPS/device fields to
settings and audit transactions. Both enforcement requirements default off.
No checkin is created by installation.
Keep a current test-site backup before migration.

## API contract

Frappe wraps returns in message. OAuth bearer authentication and browser
sessions both use the server session identity. Paths below are prefixed by
/api/method/maas_attendance_mobile.api.

- GET profile: active employee linked to current enabled Employee-role login.
- GET capabilities: actual host schema/version and capability flags.
- GET attendance_status: current employee's latest checkin and test gate.
- GET attendance_history: current employee's check-in records, 20 per page.
- POST punch: allowlisted online test IN/OUT only.
- POST register_device: register the current employee's installation as Pending.
- POST device_status: verify credentials and return approval status.
- POST submit_offline_claim: immutable delayed claim from an approved device.
- GET offline_claim_status: own request UUID's review state, without evidence.
- POST review_offline_claim: HR Manager/System Manager approves or rejects with a reason.
- GET hr_overview: permission-scoped counts and policy for HR/System Managers.
- GET mobile_setup: opt-in guest-readable public client ID/label only.

## Employee check-in checkboxes (v0.17.0)

Employee → Mobile Check-in section (created on migrate, after Attendance Device
ID): Online Check in, Offline Check In, Check in with Photo, Check in Without
Photo. Applied on the server for every route (online punch, offline claim, HR
review, automatic acceptance, supervisor scan, status).

- Online/Offline: only ticked channels are allowed; none ticked blocks app
  attendance. Site/company gates still apply: a box never re-enables a switched
  off gate. Both ticked: the app punches online when connected and saves an
  offline punch only when there is no connection.
- Photo: with-only requires a photo; without-only needs none, even when the
  site/company requires photos (offline punches then wait for HR review because
  automatic acceptance needs photo evidence); both makes it optional (the app
  asks); neither follows the site/company setting.

Upgrading blocks every employee until HR ticks their channel boxes.

## Supervisor face check-in (v0.16.0)

A supervisor scans an employee with their own approved phone; the face picks
the employee and the supervisor confirms. Off by default.

Setup (System Manager / HR):
1. Mobile Attendance Settings: enable Face Recognition Pilot, then Enable
   Supervisor Face Check-in.
2. Give the supervisor's login the MAAS Attendance Supervisor role (created on
   migrate). The supervisor needs an active Employee record; scans only match
   active employees of that same company.
3. The supervisor registers their phone in the app; HR approves the device.
4. Enroll employee faces (see the pilot section). Each scanned employee must
   still be eligible under the normal test or production attendance policy.

API (POST, prefix maas_attendance_mobile.api):
- supervisor_identify(photo, device_id, device_secret): returns decision and,
  only for a clear match, employee, employee_name, score, next_log_type and
  attendance_enabled. Nothing is stored. Near-miss names are never returned.
- supervisor_punch(employee, request_id, log_type, timestamp, device_id,
  app_version, location, device_secret, photo): identifies the same photo again
  and requires it to match the confirmed employee, then applies the employee's
  normal policy (enabled gate, IN/OUT order, freshness, workplace GPS when
  required). Photo and precise GPS are always required. The receipt records
  capture_mode Supervisor, the supervisor login and the face score; exact
  retries return the original receipt.

capabilities reports supervisor_checkin_enabled. Supervisor punches are online
only. No liveness check: the supervisor is responsible for watching for printed
or on-screen faces.

Verified locally: 181 mocked tests, and a rollback-only run on the local HRMS
site (enroll, approve device, identify, punch, stranger rejected, wrong face
refused).

## Face recognition pilot (v0.15.0)

First step towards supervisor face check-in. This release only enrolls faces and
lets HR test matches; it creates no checkins and the mobile API still reports
face_verification_enabled false. Enable Face Recognition Pilot in Mobile
Attendance Settings (off by default) before any face photo is processed.

The server uses OpenCV YuNet for detection and SFace for 128-value embeddings.
Both ONNX models are bundled in maas_attendance_mobile/face_models (about 38 MB,
MIT and Apache-2.0, pinned SHA-256 checked before loading) so no download runs at
request time. New Python dependencies: numpy and opencv-python-headless.

Mobile Attendance Face Enrollment holds one private photo per record; at most
five enabled photos per employee. The server derives company, name and the
embedding from the attached photo; client-supplied embeddings are recomputed.
Exactly one clear face (at least 64 px) is required. HR must confirm consent is
on file. Disable a photo to stop matching it, or delete the record to erase the
embedding and private photo. Only HR Manager/System Manager can use it.

From the enrollment list HR can:

- Enroll from Employee photos: background job for one company's active employees
  with an Employee image. It copies each image privately, skips unchanged photos
  and replaces older Employee Photo samples. Rerun after HR changes photos.
- Test face match: upload a photo (analysed in memory, never stored) and see the
  top three active employees with scores and the decision.

A match needs a cosine score of at least Face Match Threshold (default 0.363)
and a lead of at least Face Match Margin (default 0.05) over the next employee;
otherwise the result is no_match or ambiguous. Left employees and employees who
moved company are excluded. Tune both values with real staff photos and lighting
before supervisor check-in is built. Biometric data is sensitive personal data;
confirm consent, retention and access policy with the customer before enabling.

Local evidence: 18 new mocked tests; a real-image smoke test (public sample
photos) matched 5/5 known probes, rejected the unknown person and refused both
group photos, identically on OpenCV 4.14 and 5.0. This is not an accuracy
benchmark for your workforce. Supervisor kiosk endpoints and the mobile screen
are the next stages.

## Customer site connections (v0.10.0)

Install this package separately on each supported ERPNext/HRMS site. As System
Manager configure an OAuth Client: Authorization Code grant, Code response,
Token Endpoint Auth Method None (public PKCE), scope all, and both default and
allowed redirect URI com.maasconsult.attendance:/oauth2redirect. No mobile client
secret is used. In Mobile Attendance Settings select Mobile Public OAuth Client,
set Site Display Name and enable Mobile Site Connection. This opt-in metadata
endpoint does not enable attendance or expose employee/company lists, passwords,
client secrets, photos or alternate OAuth endpoints.

Employees enter the company's HTTPS root address and confirm the displayed site
before signing in. The app derives OAuth/API endpoints from that chosen origin;
API redirects are disabled. It does not trust an origin supplied by discovery.
Selected connection is persisted. Switch site from the signed-out screen; the
old signed-in widget and its foreground sync are disposed. Queues remain with
their original site and employee and are not transferred to the new site.

OAuth sessions are keyed by origin/client ID (one active account per connection).
Pending punches, device credentials and offline queues are keyed by origin and
verified account; installation audit IDs are keyed by origin. Existing test-site
data migrates only for the exact original test origin/client, with a verified
scoped copy before deleting the old key. Credentials are never relayed centrally.
The MAAS test shortcut appears only in debug builds.

This stage configures site connections, not production company policy. The test
lane still allows only the selected dedicated test employee and skips auto
attendance. Per-company production policies, customer deployment verification,
iOS/native secure-store migration and background uploads remain later checkpoints.

## HR attendance controls (v0.8.0)

Find MAAS Attendance Controls using Desk search, or open Mobile Attendance
Settings and click HR controls. HR Managers can read policy and review devices,
offline claims, workplaces and punch photo records. System Managers can edit
settings. Counts use permission-aware get_list queries and contain no employee
list, installation credentials or photo evidence. Both the Page and API restrict
access to HR Manager/System Manager; normal record permissions still apply.

The settings screen groups test policy, punch evidence and offline review.
Disable test submissions saves both online/offline gates off through the standard
form. It does not delete saved data. GPS workplace enforcement cannot be enabled
with online testing until the selected employee's company has a valid enabled
workplace. Device forms offer approval/revocation actions; existing server rules
still block self-review, identity edits and reactivation of revoked devices.
Offline claim forms retain reasoned approval/rejection and conflict checks.

## Punch photo and GPS evidence (v0.7.0)

Require Punch Photo and GPS defaults off in Mobile Attendance Settings. When
enabled, both IN and OUT record the button time, open the front camera, let
the user take and accept one photo, and then capture precise GPS. Cancelling
or failing capture creates no new punch. No face matching or liveness runs.

The photo argument contains image_base64 (JPEG, maximum 2 MiB) and captured_at
(offset-bearing timestamp). Capture must occur within two minutes of button
time, and GPS must accompany the photo within 30 seconds. Online submission
uses the existing 15-minute original-event window, allowing unchanged photo/GPS
retries after connection failures. GPS-only punches retain the 30-second fresh
sample rule. Workplace checks still apply when configured.

The server decodes actual JPEG pixels, limits dimensions, strips EXIF metadata
and stores a resized JPEG as a private File attached to punch_photo on Mobile
Attendance Transaction, or Mobile Attendance Offline Claim for delayed claims.
HR Manager/System Manager can view those audit records. The transaction links
the standard Employee Checkin. Photo bytes are excluded from audit JSON, API
receipts and employee history; original-image hash and capture time are retained.
Normal file editing, public conversion, detachment and deletion are blocked.

The native app saves unresolved online photo requests and offline photos in
secure storage for exact retries. Photo-required offline queues are limited to
five events (otherwise twenty); raw queued photos are removed after a verified
server receipt or a permitted discard. Offline upload still creates a pending
HR claim, never automatic attendance. Device secrets remain separate.

Real camera permission/lifecycle tests, private file access, rollback behavior,
retention policy and Cloud migration remain deployment checkpoints. All current
punches skip auto attendance. Face enrollment, matching and liveness are deferred.

## Offline test claims (v0.6.0)

Enable Offline Test Claims defaults off and requires the online test policy
and dedicated test employee. An approved installation credential is always
required for upload, even if online approved-device enforcement is off.
The native app prepares while connected, caching next IN/OUT, the current
check-in reference, GPS policy and approved installation ID for at most 24
hours. During that signed-in session the user can explicitly save up to 20
offline events. Original UUID/time/GPS/predecessor are saved in secure storage
before upload; the installation secret remains in the separate credential vault.
There is no background capture or automatic background sync.
The mobile app now retries queued uploads automatically while open and signed
in (bounded backoff, up to one minute), and on returning to the foreground.
Empty queues cause no network requests. Manual sync remains available. Uploads
pause during capture/review operations and retain original UUID/time/photo/GPS.
If the app is closed, reopen it to resume sync; OS background jobs are not yet
implemented. A prepared offline session is still required for capture.

Uploads must arrive within 24 hours and no more than two minutes in the future.
GPS samples are compared to their original event time and current configured
company workplaces. Delayed phone time/GPS remain untrusted claims. Exact
retries recover the original review record; altered payloads cannot reuse an ID.
Pending backlog is limited to 100 records per employee. An offline claim and
an online receipt cannot deliberately reuse an existing request UUID.

Uploading creates only a Mobile Attendance Offline Claim in Pending Review.
HR opens this record and uses Approve test claim or Reject claim, with a reason.
Self-review is forbidden. Approval rechecks employee eligibility, enabled test
policy, original device approval, workplace rules and current attendance order.
The latest check-in must match the original base check-in or the approved
predecessor claim's check-in. Intervening activity or an unapproved predecessor
blocks approval. HR can reject conflicts and make a separate manual correction.
Approval creates one test Employee Checkin with skip_auto_attendance=1 and
stores reviewer/reason/time atomically. Reviews and original evidence cannot
be edited or deleted through normal ORM routes. Explicit HR review can occur
after the upload window; it is not an automatic delayed punch acceptance.

The app syncs serially, looks up each UUID before upload, persists progress
after each receipt and retains remaining events on failure. Review states are
refreshed while connected. Unresolved offline events block new online punches
on this installation. Another installation or an HR edit can still introduce
a conflict, which the server detects during review. For an expired unuploaded
queue, the user may explicitly discard only after a connected lookup confirms
every queued event is absent from the server. Discarded events create no checkin;
uploaded claims require HR review. A bounded local archive retains final events.

Authentication must be established online. Saved queues survive restart/sign-out,
but a cold start while disconnected cannot restore a verified account yet.
Offline capture is currently limited to a prepared, already signed-in session.
Live migration, database locking, secure-storage persistence and real device
GPS checks remain checkpoints. This is a test workflow, not production offline
attendance or protection against a compromised phone/clock.

## Employee check-in history (v0.5.0)

attendance_history accepts only an optional opaque cursor. The API resolves the
active employee from the current enabled login and Employee role, then explicitly
scopes every database query to that employee. Employees cannot choose another
employee or submit free-form filters. This is a deliberate self-service read;
the Employee identity lookup still enforces normal Employee read permissions.

Responses contain api_version, user, timezone, records and next_cursor. Each
record contains name, time, log_type and skip_auto_attendance (null on a schema
without that field). GPS, installation credentials and transaction payloads
are excluded. Records use site time and descending time/name ordering. Pass
next_cursor unchanged to load older records; null means there are no more.
Refresh starts a new listing. HR edits to old timestamps may change pagination.

This lists Employee Checkin records, including test punches and other sources.
It does not report finalized Attendance, worked hours, leave or payroll status.
It creates no records and works while the online test submission gate is off.
Live database queries and OAuth permission checks remain deployment checkpoints.

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
UUID alone is audit metadata; approved-device enforcement additionally requires
the separate installation credential. No employee/user ID,
skip-attendance or face-verification input authorizes a punch. Employee
lookup enforces normal read permissions, enabled login, Employee role, exactly
one linked Employee and Active status.

## Test policy

## Optional GPS workplace requirement (v0.3.0)

HR Manager or System Manager can create Mobile Attendance Workplace records
with a company, enabled flag, centre coordinates, radius and maximum GPS
uncertainty. All enabled workplaces for the authenticated employee's company
are eligible; employee-specific workplace assignments are not implemented.
System Manager can turn on Require GPS Workplace Check for Test Punches in
Mobile Attendance Settings. Both IN and OUT then require location:
latitude, longitude, accuracy_m, offset-bearing timestamp, precise (JSON bool),
is_mocked (JSON bool). The client captures a fresh fix for each NEW punch.

The server requires a sample no more than 30 seconds old or 5 seconds ahead,
within 30 seconds of the punch timestamp. Precise must be true, is_mocked false,
and uncertainty positive and at most the configured limit. Distance from the
centre plus uncertainty must fit within the workplace radius. Missing company,
missing workplaces or invalid configuration reject the punch. The server
chooses company and workplace; no client-supplied workplace ID is accepted.
GPS evidence and the validated workplace are stored in the immutable receipt.
Exact retry hashes include GPS and return the original result without requiring
a new GPS sample or rechecking a changed workplace. The test gate still applies.

These rules validate reported coordinates. They do not establish spoof-proof
physical presence. Face verification and production attendance policies remain
pending. Offline test claims use the separate HR review workflow above. The standalone GPS diagnostic
card remains local-only; the location attached to an enabled GPS punch is sent
to ERPNext. Do not configure emulator coordinates as a real workplace.

## Online test gate

## Device registration and approval (v0.4.0)

POST register_device accepts installation UUID device_id, a 64-character
lowercase random hex device_secret, and device_label. The app generates 256
random bits and securely saves credentials per account BEFORE registration.
Registration derives employee/user from the authenticated session, serializes
on the employee row, and starts Pending. Lost-response retries with the same
credentials return the same record. The server stores only a SHA256 secret
hash. No secret/hash is returned through device status. A registration label
is immutable after enrollment; retries do not rotate credentials.

POST device_status checks authenticated ownership and the exact secret before
returning Pending/Approved/Revoked. HR Manager/System Manager reviews Mobile
Attendance Device records. Another reviewer must approve; self-approval is
blocked. Only Pending->Approved/Revoked or Approved->Revoked are allowed.
Reviewer/time are recorded automatically. Revoked cannot be reactivated;
audit records cannot be deleted. At most five non-revoked registrations per
employee are allowed. Replacement-installation enrollment UI remains pending.

Require Approved Device for Test Punches defaults off. When enabled, the punch
service requires the UUID plus matching device_secret and Approved status under
a device row lock. This is checked even for exact punch retries, so revocation
also blocks receipt retries. A secret never enters the canonical punch hash,
pending punch journal, or audit receipt. The client attaches it transiently
from secure storage. Receipts retain the approved registration link.

An installation credential is not hardware attestation. Copying credentials
from a compromised device can impersonate that installation. Hardware-backed
keys/attestation and physical-device secure-storage checks are not implemented.
Secrets must never be copied into console commands, screenshots or logs.
The standalone GPS diagnostic card remains local-only; GPS punches separately
send their captured location to the server.

## Online test gate settings

As System Manager open Mobile Attendance Settings. Leave Enable Online Test
Attendance unchecked for initial deployment/API validation. Then select ONLY
the dedicated Mobile Test employee and enable for supervised tests.
attendance_submission_enabled, offline_submission_enabled and
face_verification_enabled always remain false. Only
online_test_submission_enabled can become true for the selected employee.
attendance_status additionally exposes offline_test_submission_enabled when
the separate offline claim gate is enabled. It never enables automatic offline attendance.

Every service-created standard Employee Checkin has Skip Auto Attendance = 1
and a MAAS-TEST installation ID. HRMS validations still execute. HR Settings
requiring location may reject GPS-free tests; do not disable HR security policy
to force a test through. The service converts the ORIGINAL offset-bearing
instant to site timezone at second resolution (Asia/Riyadh on verified site).
New online events may be at most 15 minutes old or 2 minutes ahead; offline
events use the separate claim/review API. With no history, IN is required first; then IN/OUT
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
Live test-site checks for this revision:

1. Gate disabled: app_version 0.12.0, test flag false, status readable only by
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
8. GPS/device requirements default off. Configure and exercise both optional
   requirements in supervised tests, including revoked-device retry rejection.
9. Disable both test gates after tests. History and offline test claim review
   are implemented; face verification is deferred and release hardening remains pending.

## Company test policies (v0.10.0)

Use Company Test Policies defaults off. In this opt-in mode, configure one
Mobile Attendance Company Policy per company with a dedicated eligible test
employee. Company is derived from Employee on the server. Missing or disabled
company policies never fall back to the legacy employee. Site online/offline
switches remain global gates; site photo/GPS/device flags remain minimums.
Company rules can strengthen them. HR Manager reads policy records; System
Manager edits them. These remain test policies: all punches skip automatic
attendance and offline claims require HR review. Migrate an isolated test site
before enabling this mode. Production eligibility remains a future stage.

## Photo retention (v0.11.0)

Company policies now expose retention days (0 keeps photos) and automatic
cleanup (OFF by default). HR can preview counts and hold an individual receipt
or offline photo with a reason. Offline photos remain protected while awaiting
review and for a full retention interval after final review. New photo records
snapshot company/upload time; legacy photos without these values remain kept.
The hourly scheduler first processes previously committed cleanup audit intents,
then plans new intents. It preserves punch/GPS/hash/review records, checks current
policy and holds under lock, skips shared or unknown files, and retries failed
intents. Backups/exports are outside this managed-photo cleanup.

The File controller subclass guards physical deletion and overwrite before
Frappe storage operations, including photo optimization. Other app File overrides
must retain MAAS protection; new photo storage and cleanup reject incompatible
controllers. Verify controller compatibility, real database locks, storage
failure recovery and scheduler execution on an isolated clone before enabling
cleanup. No production attendance gates were introduced by v0.11.0.

## Production enrollment (v0.12.0)

Production submission is OFF by default. Configure the site production gate,
company production policy and enabled Mobile Attendance Employee enrollment.
Company is derived from the employee; changed company enrollment fails closed.
Dedicated test employees stay in the test lane. Production requires an approved
device and defaults to photo/GPS evidence. Production requests explicitly carry
`attendance_mode: production`; test requests cannot silently become production.

Production online checkins set Skip Auto Attendance to 0, leaving attendance
processing to HRMS shift settings. Test checkins keep it at 1. Offline production
evidence still requires HR approval, unchanged enrolled company and matching
mode. Approval creates a checkin at the original captured time. Automatic
offline acceptance is not implemented.

Local checks: 133 mocked Python contract tests, 7 HR UI tests and 121 Flutter
tests pass. Real migration, SQL locks/rollback, protected File override, shift
processing and customer permissions require an isolated HRMS site pilot. Do not
enable production or cleanup solely on the basis of these unit tests.

## Deployment diagnostics and database verification (v0.12.1)

Run the read-only diagnostics after migrating:
`bench --site SITE execute maas_attendance_mobile.integration_checks.preflight`.
It checks installed apps, schema field types, File protection, scheduler hook
configuration and disabled submission gates. A passing preflight does not
establish production readiness or prove scheduler execution.

The database suite uses temporary synthetic fixtures and rolls back each test;
fixture commits are rejected. It covers online retry/IN/OUT, failed receipt
rollback, direct-write protection, revoked devices and offline HR approval.
It requires the `maas_attendance_integration_tests` site-config flag and either
a `maas-attendance-integration.` site name or the explicitly authorized development
site `erpnextconsult.m.frappe.cloud`. Never enable that flag on a customer or real
production site. After migration, run:
`bench --site SITE run-tests --module maas_attendance_mobile.test_database_integration`.
Remove the opt-in flag afterwards. HTTP/OAuth, concurrent requests, finalized
daily Attendance and physical-device delivery still need separate verification.

Development site verification on 2026-10-03: all five real database tests passed
on Frappe 16.36.1 / ERPNext 16.37.0 / HRMS 16.20.1 with backend 0.12.1.
Original four checkins, settings and fixture counts were preserved after rollback.
Additional private-photo/GPS protection, outside-workplace rejection and employee
default-shift resolution checks passed. These do not prove automatic Attendance
processing or native background delivery. Backend 0.12.2 fixes the v16 HR overview
aggregate syntax while retaining permission-scoped queries and v15 compatibility.
141 mocked Python contract tests and seven HR UI tests pass; v15 has not been
verified against a real database.

## Automatic production offline acceptance (v0.13.0)

Company policies now include **Automatically Accept Valid Production Offline
Punches**, disabled by default. Enable it only with production online/offline
gates, employee enrollment, an approved device, required photo evidence and
workplace GPS. Existing deployments retain manual review until this company
option is deliberately enabled. Test claims always need HR review.

With this option, a valid upload creates a standard Employee Checkin at the
original button time and returns an Approved receipt. The immutable offline
claim retains its private photo, GPS and an Automatically Accepted marker;
it does not impersonate a human reviewer. Ordered IN/OUT claims must use the
same device and attendance mode and an accepted predecessor. Exact retries
return the existing result. Changed attendance or invalid sequencing leaves
valid evidence pending for HR; database/storage errors fail the transaction
and the phone retains its queued event for retry. Automatic acceptance does
not prove identity or finalize a daily Attendance record.

The 24-hour upload/preparation window and 20-event mobile capacity remain.
Two additional guarded SQL tests cover automatic photo IN/OUT/retry and
conflict evidence preservation. These new cases still require real database
execution before release; the earlier five SQL cases passed on v0.12.1.


## Unified employee attendance (v0.14.0 backend / v0.15.0 mobile)

The employee home uses a single Check In / Check Out action online and offline.
After initial sign-in and device registration the app refreshes its cached policy
without an employee preparation step. Every new punch is saved durably before
upload; original button time, private photo and GPS survive reconnects/restarts.
Android network-constrained work and iOS processing tasks retry saved evidence;
OS restrictions and force-stop can delay background delivery. No immediate
closed-app-delivery guarantee is made.

Cached account/policy and upload windows are seven days; the mobile queue allows
100 active events within its existing 64 MB encrypted storage bound. Production
automatic acceptance remains an explicit company policy with photo/workplace
requirements. A separate default-off `auto_accept_test_offline` setting permits
valid dedicated test claims with photo/GPS and approved device to autoaccept,
while keeping Skip Auto Attendance=1. Changed ordering, device/company or mode
is never silently accepted; failed evidence stays available for correction.

Deploy and migrate the backend before installing the new mobile pilot build.
Existing approved/pending claims remain immutable. Enable automatic test
acceptance only on the dedicated development site; real production policy is
configured separately for each customer company. No face verification is added.
