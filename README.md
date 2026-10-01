# MAAS Attendance Mobile — Frappe app

Version 0.1.0 is an installation and identity checkpoint for the Flutter project.
It exposes two authenticated, read-only APIs. It does not accept attendance writes.

## Hosting

The repository root must contain this `pyproject.toml` and the
`maas_attendance_mobile` Python package directly. Upload only this app folder's
contents, not the Flutter project or a parent `backend` directory.

Frappe, ERPNext and HRMS are installed by Bench/Frappe Cloud, not from PyPI.
The package uses Python syntax supported from 3.10 onwards so it can run with
v15 and v16 host runtimes. Bench metadata declares v15/v16 compatibility;
successful installation and live tests on each site remain required.

Add the private GitHub repository to your Frappe Cloud bench, deploy that app
revision to the test site's bench, then install the app on the test site.

## API tests

In a browser signed in as the normal linked employee, open:

- `/api/method/maas_attendance_mobile.api.capabilities`
- `/api/method/maas_attendance_mobile.api.profile`

Frappe wraps each return value in `message`. The profile returns the Employee
linked to the server's session identity. It has no employee ID input parameter.
OAuth bearer authentication uses the same Frappe session identity mechanism.
Guest, disabled, unlinked, inactive and ambiguous employee mappings are rejected.
Normal Frappe document permissions remain enforced by `get_list`.

Check the capabilities result before enabling any mobile submission feature.
All submission flags remain false in this release. No DocTypes, custom fields,
roles, standard permission changes or attendance records are created by the app.

## Local checks

Run `python -m unittest discover -s tests -v` from this repository root.
These tests mock Frappe; they verify decision logic, not a real database,
installation, whitelist dispatch or OAuth middleware. Test those on the test site.

## Next checkpoint

After successful deployment, verify both endpoints as the employee and rejection
in an unauthenticated browser. Then add the transaction schema, unique database
constraint, authorization, state validation and standard Employee Checkin writes
together. GPS, face evidence and offline attendance policy follow their phases.
Do not regard this foundation release as a production attendance backend.
