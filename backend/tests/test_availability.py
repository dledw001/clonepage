import asyncio
import json
import socket
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import shortcuts
from availability import AvailabilityGateway, initialize_availability, router


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, 'DB_PATH', Path(self.directory.name) / 'reading.sqlite3')
        self.db_patch.start()
        shortcuts.initialize_shortcuts()
        initialize_availability()
        self.now = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
        self.calls = []
        self.handler = lambda request: httpx.Response(200)
        async def upstream(request):
            self.calls.append(request)
            result = self.handler(request)
            return await result if asyncio.iscoroutine(result) else result
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        self.gateway = AvailabilityGateway(self.upstream, 'example.com', '10.0.0.149', now=lambda: self.now)
        app = FastAPI(); app.include_router(router); app.state.availability = self.gateway
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://clonepage')

    async def asyncTearDown(self):
        await self.gateway.close()
        await self.client.aclose()
        await self.upstream.aclose()
        self.db_patch.stop()
        self.directory.cleanup()

    def add(self, id='service', value='https://plex.example.com/', enabled=1, action='open'):
        with main.database() as db:
            db.execute("INSERT INTO shortcuts (id,name,description,aliases,icon,color,action,value,position,availability) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (id,id,'','[]','Server','#a4b9e4',action,value,100,enabled))

    async def sample(self):
        await self.gateway.refresh_due()
        return self.gateway.snapshot()['readings']

    async def test_only_saved_opted_in_websites_are_checked_and_endpoint_is_read_only(self):
        self.add(); self.add('disabled', enabled=0); self.add('copy', value='secret command', action='copy')
        response = await self.client.get('/api/shortcuts/availability?url=http://untrusted.invalid')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        await self.gateway._task
        result = (await self.client.get('/api/shortcuts/availability')).json()
        self.assertEqual([item['id'] for item in result['readings']],['service'])
        self.assertEqual(len(self.calls),1)
        self.assertEqual((await self.client.post('/api/shortcuts/availability')).status_code,405)

    async def test_reverse_proxy_mapping_keeps_host_port_and_tls_name_without_query_or_fragment(self):
        self.add(value='https://plex.example.com:8443/web?token=private#section')
        reading = (await self.sample())[0]
        request = self.calls[0]
        self.assertEqual(str(request.url),'https://10.0.0.149:8443/web')
        self.assertEqual(request.headers['host'],'plex.example.com:8443')
        self.assertEqual(request.extensions['sni_hostname'],'plex.example.com')
        self.assertEqual(reading['state'],'reachable')
        self.assertNotIn('private',str(request.url))

    async def test_unrelated_hosts_and_lan_urls_use_their_own_addresses(self):
        for value in ['https://example.com.evil.test/', 'http://10.0.0.20:9000/', 'https://example.com/']:
            request = self.gateway.request(value,'HEAD')
            self.assertEqual(str(request.url),value)
            self.assertNotIn('sni_hostname',request.extensions)

    async def test_redirects_and_login_responses_are_reachable_without_following_redirects(self):
        for status in [200,204,301,302,307,401,403]:
            self.add(str(status), value=f'https://plex.example.com/{status}')
        self.handler = lambda request: httpx.Response(int(request.url.path[1:]),headers={'Location':'https://sso.invalid/'})
        readings = await self.sample()
        self.assertTrue(all(item['state']=='reachable' for item in readings))
        self.assertEqual(len(self.calls),7)
        self.assertTrue(all(request.url.host=='10.0.0.149' for request in self.calls))

    async def test_http_failures_are_distinct_from_connection_failures(self):
        self.add()
        for status in [404,429,500,502,503]:
            self.handler = lambda request, status=status: httpx.Response(status)
            self.now += timedelta(seconds=61)
            reading = (await self.sample())[0]
            self.assertEqual((reading['state'],reading['status_code']),('http_error',status))
            self.assertIsNone(reading['last_success_at'])

    async def test_get_fallback_for_servers_without_head_support_closes_body_without_reading(self):
        class UnreadBody(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                raise AssertionError('Response body must not be read')
                yield b''
            async def aclose(self): self.closed = True
        body = UnreadBody()
        self.add()
        self.handler = lambda request: httpx.Response(405) if request.method=='HEAD' else httpx.Response(200,stream=body)
        self.assertEqual((await self.sample())[0]['state'],'reachable')
        self.assertEqual([request.method for request in self.calls],['HEAD','GET'])
        self.assertTrue(body.closed)

    async def test_cookies_from_one_check_are_never_sent_on_another_check(self):
        self.add(); self.add('other')
        self.handler = lambda request: httpx.Response(200,headers={'Set-Cookie':'secret=session; Path=/'})
        await self.sample()
        self.assertTrue(all('cookie' not in request.headers and 'authorization' not in request.headers for request in self.calls))

    async def test_timeout_dns_and_certificate_errors_keep_safe_details(self):
        self.add()
        cases=[httpx.ReadTimeout('private URL and token'), httpx.ConnectError('private URL'), httpx.RemoteProtocolError('private body')]
        for cause in [ssl.SSLCertVerificationError('private cert'),socket.gaierror('private DNS')]:
            error=httpx.ConnectError('private URL'); error.__cause__=cause; cases.append(error)
        for error in cases:
            def fail(request, error=error): raise error
            self.handler=fail; self.now+=timedelta(seconds=61)
            reading=(await self.sample())[0]
            self.assertEqual(reading['state'],'unreachable')
            self.assertNotIn('private',reading['detail'])
        self.assertIn('resolved',reading['detail'])

    async def test_success_time_persists_through_failure_and_gateway_restart(self):
        self.add(); first=(await self.sample())[0]
        self.now += timedelta(seconds=61)
        def fail(request): raise httpx.ConnectError('offline')
        self.handler=fail
        failed=(await self.sample())[0]
        self.assertEqual(failed['last_success_at'],first['checked_at'])
        other=AvailabilityGateway(self.upstream,now=lambda:self.now)
        self.assertEqual(other.snapshot()['readings'][0]['last_success_at'],first['checked_at'])
        await other.close()

    async def test_cache_prevents_duplicate_checks_and_old_results_are_marked_stale(self):
        self.add(); await self.sample(); await self.gateway.refresh_due()
        self.assertEqual(len(self.calls),1)
        self.now+=timedelta(seconds=151)
        reading=self.gateway.snapshot()['readings'][0]
        self.assertTrue(reading['stale'])
        await self.gateway._task
        self.assertFalse(self.gateway.snapshot()['readings'][0]['stale'])

    async def test_simultaneous_readers_share_background_work_and_checks_are_bounded(self):
        for i in range(9): self.add(str(i))
        gate=asyncio.Event(); started=asyncio.Event(); active=0; peak=0
        async def wait(request):
            nonlocal active,peak
            active+=1; peak=max(peak,active)
            if active==4: started.set()
            await gate.wait(); active-=1
            return httpx.Response(200)
        self.handler=wait
        first,second=await asyncio.gather(self.client.get('/api/shortcuts/availability'),self.client.get('/api/shortcuts/availability'))
        await asyncio.wait_for(started.wait(),1)
        self.assertTrue(first.json()['checking']); self.assertTrue(second.json()['checking'])
        self.assertEqual(peak,4)
        gate.set(); await self.gateway._task
        self.assertEqual(len(self.calls),9)

    async def test_changed_disabled_or_deleted_shortcut_cannot_receive_inflight_results(self):
        for change in ['url','disabled','deleted']:
            with main.database() as db: db.execute("DELETE FROM shortcuts WHERE id='service'")
            self.add()
            async def change_during_probe(request):
                with main.database() as db:
                    if change=='url': db.execute("UPDATE shortcuts SET value='https://other.invalid/' WHERE id='service'")
                    elif change=='disabled': db.execute("UPDATE shortcuts SET availability=0 WHERE id='service'")
                    else: db.execute("DELETE FROM shortcuts WHERE id='service'")
                return httpx.Response(200)
            self.handler=change_during_probe
            await self.gateway.refresh_due()
            with main.database() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM shortcut_availability').fetchone()[0],0)

    async def test_shutdown_cancels_an_active_probe(self):
        self.add(); started=asyncio.Event()
        async def wait(request):
            started.set(); await asyncio.Event().wait()
        self.handler=wait
        self.gateway.schedule(); await asyncio.wait_for(started.wait(),1)
        await self.gateway.close()
        self.assertTrue(self.gateway._task.cancelled())


if __name__=='__main__': unittest.main()
