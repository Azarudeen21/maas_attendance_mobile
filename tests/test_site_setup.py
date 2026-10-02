import importlib
import types
import unittest
from unittest.mock import Mock
import test_api

class SiteSetupTests(unittest.TestCase):
    def setUp(self):
        self.base = test_api.ApiTests(); self.base.setUp(); self.frappe = self.base.frappe
        import maas_attendance_mobile.site_setup
        self.service = importlib.reload(maas_attendance_mobile.site_setup)
        self.values = {'client_id':'public-client','client_secret':'DO NOT RETURN','grant_type':'Authorization Code','response_type':'Code','token_endpoint_auth_method':'None','default_redirect_uri':self.service.REDIRECT,'redirect_uris':self.service.REDIRECT,'scopes':'all openid'}
        self.frappe.get_doc = Mock(return_value=types.SimpleNamespace(get=self.values.get))
        self.config = {'enable_mobile_site_setup':1,'mobile_oauth_client':'public-client','mobile_site_label':'Customer site','test_employee':'PRIVATE'}
        self.frappe.db.get_singles_dict.return_value = self.config
    def tearDown(self): self.base.tearDown()
    def test_guest_metadata_has_only_public_fields_no_identity_secret_or_endpoint(self):
        self.frappe.session.user = 'Guest'
        response = self.service.discovery()
        self.assertEqual(set(response), {'api_version','client_id','redirect_uri','label'})
        self.assertNotIn('DO NOT RETURN', str(response)); self.assertNotIn('PRIVATE', str(response))
        self.assertEqual(response['client_id'], 'public-client')
    def test_disabled_setup_does_not_read_client(self):
        self.config['enable_mobile_site_setup'] = '0'
        with self.assertRaises(ValueError): self.service.discovery()
        self.frappe.get_doc.assert_not_called()
    def test_invalid_client_type_scope_and_redirect_rejected(self):
        for key, bad in [('token_endpoint_auth_method','Client Secret Basic'),('grant_type','Implicit'),('response_type','Token'),('default_redirect_uri','https://evil.example'),('redirect_uris',''),('scopes','openid'),('client_id','../bad')]:
            original = self.values[key]; self.values[key] = bad
            with self.assertRaises(ValueError): self.service.discovery()
            self.values[key] = original
    def test_invalid_site_label_rejected(self):
        self.config['mobile_site_label'] = 'A'*101
        with self.assertRaises(ValueError): self.service.discovery()

if __name__ == '__main__': unittest.main()
