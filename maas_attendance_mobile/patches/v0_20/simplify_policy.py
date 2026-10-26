"""0.20: one site switch, one company policy and the Employee boxes.

Test/production lanes are removed. Nobody gains access: an employee keeps a
ticked Online/Offline box only if the old rules let them use that channel.
In a company that ran production, the old dedicated test account loses access
(production wins). Old columns are left in place for rollback.
"""
import frappe
from frappe.utils import now_datetime

SETTINGS = 'Mobile Attendance Settings'
POLICY = 'Mobile Attendance Company Policy'
ENROLLMENT = 'Mobile Attendance Employee'
ONLINE, OFFLINE = 'maas_online_checkin', 'maas_offline_checkin'
OLD_SITE = ('notice', 'enable_test_attendance', 'test_employee', 'use_company_test_policies',
            'require_test_photo', 'require_test_location', 'require_approved_device', 'enable_offline_test',
            'enable_production_attendance', 'enable_production_offline', 'auto_accept_test_offline')


def on(value):
    return str(value) == '1'


def execute():
    old = dict(frappe.db.sql('select field, value from `tabSingles` where doctype=%s and field in %s', (SETTINGS, OLD_SITE)))
    if old:
        migrate(old)
        frappe.db.sql('delete from `tabSingles` where doctype=%s and field in %s', (SETTINGS, OLD_SITE))
        frappe.clear_cache(doctype=SETTINGS)
    frappe.delete_doc('Page', 'maas-attendance-controls', force=True, ignore_missing=True)
    # Dropping the table commits implicitly, so it runs after all data above is written.
    if frappe.db.exists('DocType', ENROLLMENT):
        frappe.delete_doc('DocType', ENROLLMENT, force=True, ignore_missing=True)


def migrate(old):
    site_test, site_prod = on(old.get('enable_test_attendance')), on(old.get('enable_production_attendance'))
    site_offline_test, site_offline_prod = on(old.get('enable_offline_test')), on(old.get('enable_production_offline'))
    use_company = on(old.get('use_company_test_policies'))
    site_test_employee = old.get('test_employee')
    columns = set(frappe.db.get_table_columns(POLICY))
    policies = {row['company']: row for row in frappe.db.sql(f'select * from `tab{POLICY}`', as_dict=True)}
    enrollments = {row['employee']: row for row in frappe.db.sql(
        f'select employee, company, enabled from `tab{ENROLLMENT}`', as_dict=True)} if frappe.db.table_exists(ENROLLMENT) else {}
    site_test_company = frappe.db.get_value('Employee', site_test_employee, 'company') if site_test_employee else None

    def col(company, name):
        row = policies.get(company)
        return bool(row) and name in columns and on(row.get(name))

    def test_employee_of(company):
        if use_company:
            return policies[company].get('test_employee') if col(company, 'enabled') else None
        return site_test_employee if site_test_company == company else None

    def test_active(company):
        return site_test and bool(test_employee_of(company))

    def prod_active(company):
        return site_prod and col(company, 'enable_production_attendance')

    def site_or_company(company, name):
        return on(old.get(name)) or (use_company and col(company, name))

    companies = set(policies) | ({site_test_company} if site_test_company else set())
    for company in companies:
        if prod_active(company):
            values = {'enabled': 1, 'require_photo': int(col(company, 'production_require_photo')),
                      'require_location': int(col(company, 'production_require_location')), 'require_approved_device': 1,
                      'allow_offline': int(site_offline_prod and col(company, 'enable_production_offline'))}
        elif test_active(company):
            values = {'enabled': 1, 'require_photo': int(site_or_company(company, 'require_test_photo')),
                      'require_location': int(site_or_company(company, 'require_test_location')),
                      'require_approved_device': int(site_or_company(company, 'require_approved_device')),
                      'allow_offline': int(site_offline_test and (col(company, 'enable_offline_test') if use_company else True))}
        else:
            # Not usable before; keep its old requirements for when HR enables it.
            values = {'enabled': 0,
                      'require_photo': int(col(company, 'production_require_photo') or site_or_company(company, 'require_test_photo')),
                      'require_location': int(col(company, 'production_require_location') or site_or_company(company, 'require_test_location')),
                      'require_approved_device': int(site_or_company(company, 'require_approved_device') or col(company, 'enable_production_attendance')),
                      'allow_offline': int(col(company, 'enable_offline_test') or col(company, 'enable_production_offline'))}
        if company in policies:
            frappe.db.set_value(POLICY, company, values, update_modified=False)
        else:
            doc = frappe.get_doc(dict(values, doctype=POLICY, company=company))
            doc.name, doc.owner, doc.modified_by = company, 'Administrator', 'Administrator'
            doc.creation = doc.modified = now_datetime()
            doc.db_insert()  # Skip validation: an existing setup must migrate even without workplaces.

    frappe.db.set_single_value(SETTINGS, {
        'enable_mobile_attendance': int(site_test or site_prod),
        'enable_offline_sync': int(site_offline_test or site_offline_prod),
    })

    if not frappe.db.has_column('Employee', ONLINE):
        return  # Before 0.17 nobody had boxes; they are created after migrate, unticked.
    for employee in frappe.db.sql(f'select name, company, {ONLINE}, {OFFLINE} from tabEmployee where {ONLINE}=1 or {OFFLINE}=1', as_dict=True):
        name, company = employee['name'], employee['company']
        designated = {site_test_employee, (policies.get(company) or {}).get('test_employee')} - {None}
        enrollment = enrollments.get(name)
        if prod_active(company):
            online = name not in designated and bool(enrollment) and on(enrollment['enabled']) and enrollment['company'] == company
            offline = online and site_offline_prod and col(company, 'enable_production_offline')
        else:
            online = test_active(company) and test_employee_of(company) == name
            offline = online and site_offline_test and (col(company, 'enable_offline_test') if use_company else True)
        cleared = [label for field, allowed, label in ((ONLINE, online, 'Online Check in'), (OFFLINE, offline, 'Offline Check In'))
                   if on(employee[field]) and not allowed]
        if not cleared:
            continue
        frappe.db.set_value('Employee', name, {ONLINE: int(on(employee[ONLINE]) and online),
                                               OFFLINE: int(on(employee[OFFLINE]) and offline)}, update_modified=False)
        frappe.get_doc({'doctype': 'Comment', 'comment_type': 'Comment', 'reference_doctype': 'Employee', 'reference_name': name,
            'content': 'Mobile attendance 0.20 upgrade unticked ' + ' and '.join(cleared)
                       + ': this employee could not use that channel under the previous test/production rules. Tick it again to allow it.'}
        ).insert(ignore_permissions=True)
