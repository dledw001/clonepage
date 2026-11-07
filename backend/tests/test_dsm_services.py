import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import dsm
import dsm_services as services

ORIGIN = 'https://clonepage.dledwith.com'
CONFIG = dsm.DSMConfig(ORIGIN, 'https://dsm.dledwith.com', 'dledwith.com', frozenset({'10.0.0.149','localhost','127.0.0.1'}), '10.0.0.149')
SID = 'private-dsm-session-canary'
TOKEN = 'private-dsm-token-canary'
PASSWORD = 'private-password-canary'
RULE = {'UUID':'template', 'description':'qBittorrent', 'frontend':{'fqdn':'qbit.dledwith.com','port':443,'protocol':1,'https':{'hsts':False},'acl':'tailnet-profile'},
        'backend':{'fqdn':'10.0.0.149','port':8080,'protocol':0},'proxy_http_version':1,'proxy_read_timeout':60,
        'customize_headers':[{'name':'Upgrade','value':'$http_upgrade'},{'name':'Connection','value':'$connection_upgrade'}]}


class FakeDSM:
    def __init__(self):
        self.rules = [copy.deepcopy(RULE)]
        self.certs = [{'id':'old','desc':'Old certificate','subject':{'sub_alt_name':['old.example.com']},'services':[]},
                      {'id':'wild','desc':'My wildcard','subject':{'sub_alt_name':['*.dledwith.com']},'services':[]}]
        self.calls = []; self.login_error = None; self.create_timeout = False; self.bind_error = False
        self.permission_error = False; self.no_service = False

    async def request(self, request):
        params = {key:value[0] for key,value in parse_qs(request.content.decode(),keep_blank_values=True).items()}
        self.calls.append((str(request.url),dict(request.headers),params))
        api=params['api']; method=params['method']
        def result(data=None): return httpx.Response(200,json={'success':True,'data':data or {}})
        def error(code): return httpx.Response(200,json={'success':False,'error':{'code':code}})
        if api=='SYNO.API.Info': return result({key:{'path':'entry.cgi','minVersion':1,'maxVersion':7 if key==dsm.AUTH else 1} for key in [dsm.AUTH,dsm.PROXY,dsm.CERT,dsm.CERT_SERVICE]})
        if api==dsm.AUTH:
            if method=='logout': return result()
            if self.login_error: return error(self.login_error)
            return result({'sid':SID,'synotoken':TOKEN})
        if params.get('_sid') != SID: return error(119)
        if api==dsm.PROXY and method=='list':
            if self.permission_error:return error(105)
            return result({'entries':copy.deepcopy(self.rules)})
        if api==dsm.CERT:return result({'certificates':copy.deepcopy(self.certs)})
        if api==dsm.PROXY and method=='create':
            entry=json.loads(params['entry']);entry['UUID']='new-rule';self.rules.append(entry)
            if not self.no_service:self.certs[0]['services'].append({'service':'new-rule','subscriber':'ReverseProxy','display_name':entry['description'],'isPkg':False,'owner':'root'})
            if self.create_timeout:raise httpx.ReadTimeout('simulated lost response')
            return result({'UUID':'new-rule'})
        if api==dsm.CERT_SERVICE:
            if self.bind_error:return error(5503)
            for setting in json.loads(params['settings']):
                for cert in self.certs: cert['services']=[s for s in cert['services'] if s!=setting['service']]
                next(cert for cert in self.certs if cert['id']==setting['id'])['services'].append(setting['service'])
            return result()
        raise AssertionError((api,method))


class DSMServiceTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main,'DB_PATH',Path(folder)/'reading.sqlite3'))
        self.fake=FakeDSM()
        self.sessions=dsm.DSMSessions(CONFIG,lambda:dsm.DSMClient(CONFIG,httpx.AsyncClient(base_url=CONFIG.url,transport=httpx.MockTransport(self.fake.request))))
        self.stack.enter_context(patch.object(main,'DSMSessions',return_value=self.sessions))
        self.verify=self.stack.enter_context(patch.object(services,'verify_https',new=AsyncMock()))
        self.client=self.stack.enter_context(TestClient(main.app,base_url=ORIGIN))
        self.csrf=self.client.get('/api/dsm/connection').json()['csrf']

    @property
    def headers(self):return {'Origin':ORIGIN,'X-Clonepage-CSRF':self.csrf}

    def post(self,path,data=None):return self.client.post('/api/dsm/'+path,json=data or {},headers=self.headers)

    def connect(self):
        self.csrf=self.client.get('/api/dsm/connection').json()['csrf']
        response=self.post('connection',{'username':'dan','password':PASSWORD,'otp':'123456'})
        self.assertEqual(response.status_code,200,response.text)
        return response

    def draft(self,**changes):
        data={'name':'Sonarr','description':'TV series manager','aliases':['tv'],'icon':'Tv','color':'#83c7ce','subdomain':'sonarr',
              'upstream':'http://10.0.0.149:8989','template_id':'template','certificate_id':'wild'}
        data.update(changes);return data

    def preview(self,**changes):
        response=self.post('preview',self.draft(**changes));self.assertEqual(response.status_code,200,response.text);return response.json()

    def apply(self):return self.post('services',{'preview_id':self.preview()['preview_id']})

    def creates(self):return [params for _,_,params in self.fake.calls if params['api']==dsm.PROXY and params['method']=='create']

    def test_cookie_csrf_and_connection_response_do_not_expose_dsm_credentials(self):
        response=self.client.get('/api/dsm/connection')
        cookie=response.headers['set-cookie']
        for value in ['Secure','HttpOnly','SameSite=strict','Path=/']:self.assertIn(value,cookie)
        self.assertEqual(response.headers['cache-control'],'no-store')
        with self.assertLogs('httpx',level='INFO') as logs:self.connect()
        for secret in [PASSWORD,SID,TOKEN]:
            self.assertNotIn(secret,response.text)
            self.assertNotIn(secret,' '.join(logs.output))
            self.assertNotIn(secret,main.DB_PATH.read_bytes().decode('latin1'))
        login=next(call for call in self.fake.calls if call[2]['method']=='login')
        self.assertNotIn('?',login[0]);self.assertEqual(login[2]['passwd'],PASSWORD)
        self.assertEqual(login[2]['otp_code'],'123456');self.assertNotIn('enable_device_token',login[2])

    def test_mutations_reject_missing_or_wrong_origin_and_csrf(self):
        for headers in [{},{'Origin':ORIGIN},{'Origin':'https://evil.dledwith.com','X-Clonepage-CSRF':self.csrf},{'Origin':ORIGIN,'X-Clonepage-CSRF':'wrong'}]:
            response=self.client.post('/api/dsm/connection',json={'username':'dan','password':PASSWORD},headers=headers)
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.fake.calls,[])
        self.assertEqual(self.client.get('/api/dsm/connection',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/dsm/connection',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)

    def test_other_browser_does_not_inherit_the_connection(self):
        self.connect()
        with TestClient(main.app,base_url=ORIGIN) as other:
            state=other.get('/api/dsm/connection').json()
            self.assertFalse(state['connected']);self.assertNotEqual(state['csrf'],self.csrf)
            response=other.post('/api/dsm/options',json={},headers={'Origin':ORIGIN,'X-Clonepage-CSRF':state['csrf']})
            self.assertEqual(response.status_code,401)

    def test_invalid_login_bodies_never_echo_the_password(self):
        response=self.post('connection',{'username':'','password':PASSWORD,'extra':PASSWORD})
        self.assertEqual(response.status_code,400);self.assertNotIn(PASSWORD,response.text);self.assertEqual(self.fake.calls,[])

    def test_otp_failure_and_rate_limits_leave_no_authenticated_session(self):
        self.fake.login_error=403
        for _ in range(5):
            response=self.post('connection',{'username':'dan','password':PASSWORD})
            self.assertEqual(response.status_code,401);self.assertIn('authenticator',response.text)
        self.assertEqual(self.post('connection',{'username':'dan','password':PASSWORD}).status_code,429)
        self.assertFalse(self.client.get('/api/dsm/connection').json()['connected'])

    def test_idle_expiration_logs_out_without_status_polling_extending_it(self):
        self.connect();session=next(session for session in self.sessions.sessions.values() if session.client)
        session.touched-=301
        state=self.client.get('/api/dsm/connection').json()
        self.assertFalse(state['connected'])
        self.assertTrue(any(params['method']=='logout' for _,_,params in self.fake.calls))

    def test_permission_failure_logs_out_and_returns_a_safe_message(self):
        self.fake.permission_error=True
        response=self.post('connection',{'username':'dan','password':PASSWORD})
        self.assertEqual(response.status_code,403);self.assertIn('permission',response.text)
        self.assertTrue(any(params['method']=='logout' for _,_,params in self.fake.calls))

    def test_options_show_only_eligible_templates_and_wildcard_certificates(self):
        self.connect();response=self.post('options')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([v['id'] for v in response.json()['certificates']],['wild'])
        self.assertEqual(response.json()['templates'][0]['id'],'template')
        self.assertNotIn('customize_headers',response.text);self.assertNotIn(TOKEN,response.text)

    def test_preview_rejects_unsafe_or_out_of_scope_destinations_without_writes(self):
        self.connect()
        for changes in [{'upstream':'http://169.254.169.254/'},{'upstream':'https://evil.example:9000'},{'upstream':'http://10.0.0.149/private'},
                        {'upstream':'http://user:secret@10.0.0.149:8989'},{'subdomain':'clonepage'},{'subdomain':'dsm'},{'subdomain':'a.b'},
                        {'subdomain':'*'},{'certificate_id':'old'},{'icon':'Missing'},{'color':'red'}]:
            with self.subTest(changes=changes):self.assertEqual(self.post('preview',self.draft(**changes)).status_code,400)
        self.assertEqual(self.creates(),[])

    def test_existing_foreign_rules_are_never_overwritten(self):
        self.connect();response=self.post('preview',self.draft(subdomain='qbit'))
        self.assertEqual(response.status_code,409);self.assertEqual(self.creates(),[])

    def test_service_requires_a_fresh_review_bound_to_this_session(self):
        self.connect();self.assertEqual(self.post('services',{'preview_id':'a'*24}).status_code,409)
        self.assertEqual(self.creates(),[])
        self.preview();session=next(v for v in self.sessions.sessions.values() if v.client);session.preview['created']-=301
        self.assertEqual(self.post('services',{'preview_id':session.preview['id']}).status_code,409)
        self.assertEqual(self.creates(),[])

    def test_success_creates_assigns_verifies_publishes_and_disconnects(self):
        self.fake.rules[0]['description']='Clonepage'
        self.fake.rules[0]['frontend']['fqdn']='clonepage.dledwith.com'
        template=copy.deepcopy(self.fake.rules[0])
        self.connect();result=self.apply()
        self.assertEqual(result.status_code,200,result.text);self.assertTrue(result.json()['ok'])
        self.assertEqual(result.json()['shortcuts']['items'][-1]['value'],'https://sonarr.dledwith.com')
        entry=json.loads(self.creates()[0]['entry'])
        self.assertEqual(entry['description'],'Sonarr')
        self.assertEqual(self.fake.rules[0],template)
        self.assertEqual(entry['frontend']['acl'],'tailnet-profile');self.assertEqual(entry['frontend']['fqdn'],'sonarr.dledwith.com')
        self.assertEqual(entry['backend'],{'fqdn':'10.0.0.149','port':8989,'protocol':0})
        self.verify.assert_awaited_once_with('sonarr.dledwith.com', '10.0.0.149')
        self.assertFalse(self.client.get('/api/dsm/connection').json()['connected'])
        self.assertTrue(any(params['api']==dsm.CERT_SERVICE for _,_,params in self.fake.calls))
        self.assertEqual(self.fake.calls[-1][2]['method'],'logout')

    def test_retry_after_success_does_not_duplicate_the_rule_or_shortcut(self):
        self.connect();self.assertTrue(self.apply().json()['ok'])
        self.connect();self.assertTrue(self.apply().json()['ok'])
        self.assertEqual(len(self.creates()),1)
        self.assertEqual(len([v for v in self.client.get('/api/shortcuts').json()['items'] if v['value']=='https://sonarr.dledwith.com']),1)

    def test_https_failure_preserves_a_resumable_route_without_publishing_a_shortcut(self):
        self.verify.side_effect=dsm.DSMError('HTTPS could not be verified')
        self.connect();result=self.apply().json()
        self.assertFalse(result['ok']);self.assertEqual(result['stage'],'certificate_assigned')
        self.assertEqual(len(self.client.get('/api/shortcuts').json()['items']),7)
        self.verify.side_effect=None
        self.connect();self.assertTrue(self.apply().json()['ok']);self.assertEqual(len(self.creates()),1)

    def test_lost_create_response_is_reconciled_without_repeating_the_post(self):
        self.fake.create_timeout=True
        self.connect();result=self.apply().json();self.assertFalse(result['ok']);self.assertEqual(result['stage'],'creating_rule')
        self.fake.create_timeout=False
        self.connect();self.assertTrue(self.apply().json()['ok']);self.assertEqual(len(self.creates()),1)

    def test_lost_create_response_with_legacy_prefix_is_reconciled_without_renaming(self):
        self.fake.create_timeout=True
        self.connect();result=self.apply().json()
        self.assertFalse(result['ok']);self.assertEqual(result['stage'],'creating_rule')
        self.assertIsNone(services.operation('sonarr.dledwith.com')['rule_id'])
        # Simulate the rule an older Clonepage version left behind after a timeout.
        self.fake.rules[-1]['description']='Clonepage: Sonarr'
        self.fake.certs[0]['services'][-1]['display_name']='Clonepage: Sonarr'
        self.fake.create_timeout=False
        self.connect();self.assertTrue(self.apply().json()['ok'])
        self.assertEqual(len(self.creates()),1)
        self.assertEqual(self.fake.rules[-1]['description'],'Clonepage: Sonarr')

    def test_pending_create_does_not_adopt_a_rule_with_an_unexpected_name(self):
        self.fake.create_timeout=True
        self.connect();self.assertFalse(self.apply().json()['ok'])
        self.fake.rules[-1]['description']='Different service'
        self.fake.create_timeout=False
        self.connect();response=self.post('preview',self.draft())
        self.assertEqual(response.status_code,409);self.assertEqual(len(self.creates()),1)
        self.assertEqual(self.fake.rules[-1]['description'],'Different service')

    def test_retry_after_manual_rule_rename_preserves_the_name_and_rule(self):
        self.connect();self.assertTrue(self.apply().json()['ok'])
        self.fake.rules[-1]['description']='My Sonarr'
        self.connect();self.assertTrue(self.apply().json()['ok'])
        self.assertEqual(len(self.creates()),1)
        self.assertEqual(self.fake.rules[-1]['description'],'My Sonarr')

    def test_template_changes_after_review_block_creation_and_close_the_connection(self):
        self.connect();preview=self.preview();self.fake.rules[0]['frontend']['acl']='different-profile'
        result=self.post('services',{'preview_id':preview['preview_id']}).json()
        self.assertFalse(result['ok']);self.assertEqual(self.creates(),[])
        self.assertFalse(self.client.get('/api/dsm/connection').json()['connected'])

    def test_custom_authorization_headers_cannot_be_copied_to_a_new_service(self):
        self.fake.rules[0]['customize_headers']=[{'name':'Authorization','value':'private-header-canary'}]
        self.connect();response=self.post('options')
        self.assertEqual(response.json()['templates'],[]);self.assertNotIn('private-header-canary',response.text)
        self.assertEqual(self.post('preview',self.draft()).status_code,400)

    def test_disconnect_logs_out_and_clears_the_browser_cookie(self):
        self.connect();response=self.post('disconnect')
        self.assertEqual(response.status_code,200);self.assertIn('Max-Age=0',response.headers['set-cookie'])
        self.assertEqual(self.post('options').status_code,401)

    def test_absolute_session_limit_is_not_extended_by_activity(self):
        self.connect();session=next(v for v in self.sessions.sessions.values() if v.client)
        session.created-=901
        state=self.client.get('/api/dsm/connection').json();self.assertFalse(state['connected'])
        self.assertTrue(any(params['method']=='logout' for _,_,params in self.fake.calls))

    def test_reconnecting_and_failed_reviews_invalidate_previous_approval(self):
        self.connect();previous=self.preview()['preview_id']
        self.connect();self.assertEqual(self.post('services',{'preview_id':previous}).status_code,409)
        previous=self.preview()['preview_id']
        self.assertEqual(self.post('preview',self.draft(upstream='http://evil.example')).status_code,400)
        self.assertEqual(self.post('services',{'preview_id':previous}).status_code,409)
        self.assertEqual(self.creates(),[])

    def test_failed_certificate_assignment_never_publishes_or_alters_a_different_service(self):
        self.fake.bind_error=True
        self.fake.certs[0]['services'].append({'service':'unrelated','subscriber':'system','display_name':'DSM'})
        self.connect();result=self.apply().json()
        self.assertFalse(result['ok']);self.assertEqual(result['stage'],'rule_created')
        self.assertEqual(len(self.client.get('/api/shortcuts').json()['items']),7)
        self.assertTrue(any(s['service']=='unrelated' for s in self.fake.certs[0]['services']))
        self.assertFalse(self.client.get('/api/dsm/connection').json()['connected'])

    def test_resuming_detects_changed_access_control_on_an_existing_route(self):
        self.verify.side_effect=dsm.DSMError('unreachable')
        self.connect();self.assertFalse(self.apply().json()['ok'])
        self.fake.rules[-1]['frontend']['acl']=None
        self.connect();response=self.post('preview',self.draft())
        self.assertEqual(response.status_code,409);self.assertEqual(len(self.creates()),1)


if __name__=='__main__':unittest.main()
