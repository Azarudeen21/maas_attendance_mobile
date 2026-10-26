"""Explicit employee enrollment; company and site gates default disabled."""
import frappe

ENROLLMENT = 'Mobile Attendance Employee'
POLICY = 'Mobile Attendance Company Policy'


def configuration(employee, site):
    row = frappe.db.get_value(ENROLLMENT, {'employee': employee}, ['enabled', 'company'], as_dict=True, for_update=True)
    company = frappe.db.get_value('Employee', employee, 'company')
    policy = frappe.db.get_value(POLICY, {'company': company},
        ['enable_production_attendance', 'enable_production_offline', 'production_require_photo', 'test_employee',
         'production_require_location'], as_dict=True, for_update=True) if company else None
    allowed = bool(row and policy and row.get('company') == company
        and employee not in (site.get('test_employee'), policy.get('test_employee'))
        and row.get('enabled') in (1, '1', True)
        and policy.get('enable_production_attendance') in (1, '1', True)
        and site.get('enable_production_attendance') in (1, '1', True))
    return dict(site, production_mode=True, test_employee=employee if allowed else None,
        enable_test_attendance=int(allowed),
        enable_offline_test=int(allowed and site.get('enable_production_offline') in (1, '1', True)
            and policy.get('enable_production_offline') in (1, '1', True)),
        require_test_photo=int(bool(policy) and policy.get('production_require_photo') in (1, '1', True)),
        require_test_location=int(bool(policy) and policy.get('production_require_location') in (1, '1', True)),
        require_approved_device=1)


def require_mode(config, mode):
    expected = 'production' if config.get('production_mode') else 'test'
    if mode != expected:
        frappe.throw('Attendance mode changed. Resolve the original request with HR before submitting a new punch.', frappe.ValidationError)


def enrolled_login(user):
    employees = frappe.get_all('Employee', filters={'user_id': user}, fields=['name'], limit_page_length=2)
    return len(employees) == 1 and bool(frappe.db.exists(ENROLLMENT, {'employee': employees[0]['name']}))
