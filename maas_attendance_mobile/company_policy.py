"""Server-selected company test policies with site-wide emergency gates."""
import frappe

POLICY = 'Mobile Attendance Company Policy'
REQUIREMENTS = ('require_test_photo', 'require_test_location', 'require_approved_device')


def effective(employee, site):
    if site.get('enable_production_attendance') in (1, '1', True):
        test = effective(employee, {**site, 'enable_production_attendance': 0})
        # Dedicated test employees never silently become production employees.
        if test.get('test_employee') == employee:
            return test
        from maas_attendance_mobile.production_policy import configuration
        return configuration(employee, site)
    if str(site.get('use_company_test_policies', 0)) != '1':
        return site
    company = frappe.db.get_value('Employee', employee, 'company')
    row = frappe.db.get_value(POLICY, {'company': company},
        ['enabled', 'test_employee', 'enable_offline_test', *REQUIREMENTS], as_dict=True) if company else None
    result = dict(site)
    result['test_employee'] = row.get('test_employee') if row else None
    result['enable_test_attendance'] = int(bool(row) and str(row.get('enabled', 0)) == '1'
        and str(site.get('enable_test_attendance', 0)) == '1')
    result['enable_offline_test'] = int(bool(row) and str(row.get('enable_offline_test', 0)) == '1'
        and str(site.get('enable_offline_test', 0)) == '1')
    for field in REQUIREMENTS:
        result[field] = int(str(site.get(field, 0)) == '1' or bool(row) and str(row.get(field, 0)) == '1')
    return result


def test_login(site, user):
    employees = [site.get('test_employee')]
    if str(site.get('use_company_test_policies', 0)) == '1':
        employees += [row['test_employee'] for row in frappe.get_all(POLICY, fields=['test_employee'])]
    return any(employee and frappe.db.get_value('Employee', employee, 'user_id') == user
               for employee in employees)


def validate_employee(employee, company=None):
    row = frappe.db.get_value('Employee', employee, ['status', 'user_id', 'company'], as_dict=True) if employee else None
    if not row or row['status'] != 'Active' or not row['user_id'] or company and row['company'] != company:
        frappe.throw('Select an active employee in this company with a linked Employee login.')
    if not frappe.db.get_value('User', row['user_id'], 'enabled') or 'Employee' not in frappe.get_roles(row['user_id']):
        frappe.throw('The employee login must be enabled and have the Employee role.')
    if len(frappe.get_all('Employee', filters={'user_id': row['user_id']}, limit_page_length=2)) != 1:
        frappe.throw('The employee login must have exactly one linked employee.')
    return row


def validate_workplaces(company):
    from maas_attendance_mobile.geofence import workplace_values
    rows = frappe.get_all('Mobile Attendance Workplace', filters={'company': company, 'enabled': 1},
        fields=['latitude', 'longitude', 'radius_m', 'max_accuracy_m']) if company else []
    if not rows:
        frappe.throw('Configure an enabled workplace for this company before requiring GPS checks.')
    for row in rows:
        try:
            values = workplace_values(row)
            if values['max_accuracy_m'] >= values['radius_m']:
                raise ValueError('GPS uncertainty must be smaller than the workplace radius.')
        except ValueError as error:
            frappe.throw(str(error), frappe.ValidationError)
