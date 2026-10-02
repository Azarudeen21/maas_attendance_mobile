"""Public OAuth metadata for the selected HTTPS site, never credentials."""
import re
import frappe

REDIRECT = 'com.maasconsult.attendance:/oauth2redirect'


def validate_client(name):
    client = frappe.get_doc('OAuth Client', name)
    if (client.get('token_endpoint_auth_method') != 'None'
        or client.get('grant_type') != 'Authorization Code'
        or client.get('response_type') != 'Code'
        or client.get('default_redirect_uri') != REDIRECT
        or REDIRECT not in (client.get('redirect_uris') or '').split()
        or 'all' not in (client.get('scopes') or '').split()
        or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', client.get('client_id') or '')):
        frappe.throw('Select a public Authorization Code OAuth client with PKCE, all scope and the MAAS mobile redirect URI.')
    return client.get('client_id')


def discovery():
    config = frappe.db.get_singles_dict('Mobile Attendance Settings')
    if config.get('enable_mobile_site_setup') not in (1, '1', True) or not config.get('mobile_oauth_client'):
        frappe.throw('Mobile app connection is not configured for this site.', frappe.ValidationError)
    client_id = validate_client(config['mobile_oauth_client'])
    label = (config.get('mobile_site_label') or 'Company attendance').strip()
    if not label or len(label) > 100:
        frappe.throw('Configure a mobile site label of at most 100 characters.')
    return {'api_version': 1, 'client_id': client_id, 'redirect_uri': REDIRECT, 'label': label}
