import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wud import WudGateway, normalize_container, router

TOKEN = 'private-wud-token-canary'


def fixture(**changes):
    item = {
        'id': 'container-one', 'name': 'plex', 'displayName': 'Plex', 'watcher': 'local', 'status': 'running',
        'labels': {'com.docker.compose.project': 'media', 'com.docker.compose.service': 'plex', 'private.label': TOKEN},
        'image': {'name': 'linuxserver/plex', 'tag': {'value': 'latest', 'semver': False},
                  'digest': {'watch': True, 'value': 'sha256:old', 'repo': 'sha256:old-index'}, 'version': '1.0'},
        'result': {'tag': 'latest', 'digest': 'sha256:new', 'version': '1.1'},
        'updateAvailable': True, 'updateKind': {'kind': 'digest', 'semverDiff': 'patch'},
    }
    item.update(changes)
    return item


class ContainerNormalizationTests(unittest.TestCase):
    def test_live_v98_shape_without_timestamps_keeps_reported_updates(self):
        reading = normalize_container(fixture())
        self.assertEqual(reading.state, 'update')
        self.assertIsNone(reading.checked_at)
        self.assertEqual((reading.project, reading.service), ('media', 'plex'))
        self.assertNotIn(TOKEN, reading.model_dump_json())

    def test_same_tag_digest_comparison_is_delegated_to_wud(self):
        reading = normalize_container(fixture(updateAvailable=False))
        self.assertEqual(reading.state, 'current')
        self.assertEqual(reading.current_tag, reading.available_tag)

    def test_registry_error_overrides_an_old_update_and_does_not_echo_details(self):
        reading = normalize_container(fixture(error={'message': 'Registry rejected ' + TOKEN}))
        self.assertEqual(reading.state, 'unknown')
        self.assertNotIn(TOKEN, reading.model_dump_json())

    def test_local_images_and_disabled_or_unfinished_checks_are_not_current(self):
        for mode in ['local', 'disabled', 'pending']:
            item = fixture(updateAvailable=False)
            if mode == 'local': item['image']['digest']['repo'] = None
            if mode == 'disabled': item['image']['digest']['watch'] = False
            if mode == 'pending': item['result']['digest'] = None
            with self.subTest(mode=mode): self.assertEqual(normalize_container(item).state, 'unknown')

    def test_no_result_or_non_boolean_update_flag_is_incomplete(self):
        self.assertEqual(normalize_container(fixture(result=None)).state, 'unknown')
        self.assertEqual(normalize_container(fixture(updateAvailable='false')).state, 'unknown')

    def test_semver_major_candidate_and_timestamp_are_preserved(self):
        item = fixture(checkedAt='2026-11-07T20:00:00Z')
        item['image']['tag'] = {'value': 'v2', 'semver': True}
        item['image']['digest']['watch'] = False
        item['result'] = {'tag': 'v3'}
        item['updateKind'] = {'kind': 'tag', 'semverDiff': 'major'}
        reading = normalize_container(item)
        self.assertEqual((reading.state, reading.change, reading.available_tag), ('update', 'major', 'v3'))
        self.assertEqual(reading.checked_at, '2026-11-07T20:00:00+00:00')

    def test_project_stack_fallback_and_standalone_containers(self):
        self.assertEqual(normalize_container(fixture(labels={}, stack='immich')).project, 'immich')
        self.assertIsNone(normalize_container(fixture(labels={})).project)
        self.assertIsNone(normalize_container(fixture(checkedAt='invalid')).checked_at)


class WudGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.0
        self.calls = []
        self.response = [fixture()]
        def upstream(request):
            self.calls.append(request)
            if isinstance(self.response, Exception): raise self.response
            if isinstance(self.response, httpx.Response): return self.response
            return httpx.Response(200, json=self.response)
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False)
        self.gateway = WudGateway(self.upstream, 'http://wud:3000', TOKEN, 'https://wud.example.com', clock=lambda: self.now)
        app = FastAPI(); app.include_router(router); app.state.wud = self.gateway
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://clonepage')

    async def asyncTearDown(self):
        await self.client.aclose(); await self.upstream.aclose()

    async def read(self):
        return await self.client.get('/api/infrastructure/containers')

    async def test_read_only_authenticated_requests_share_cache_without_exposing_token(self):
        first, second = await asyncio.gather(self.read(), self.read())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].method, 'GET')
        self.assertEqual(self.calls[0].url.path, '/api/containers')
        self.assertEqual(self.calls[0].headers['authorization'], 'Bearer ' + TOKEN)
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertNotIn(TOKEN, first.text)
        self.assertEqual(first.json()['url'], 'https://wud.example.com')
        self.assertEqual((await self.client.post('/api/infrastructure/containers')).status_code, 405)

    async def test_no_token_disables_monitoring_without_a_network_request(self):
        self.gateway._token = ''
        self.gateway._snapshot.configured = False
        response = await self.read()
        self.assertFalse(response.json()['configured'])
        self.assertEqual(self.calls, [])

    async def test_error_retains_prior_data_and_time_marks_it_stale_and_recovers(self):
        first = (await self.read()).json()
        self.response = httpx.ConnectError('private details ' + TOKEN)
        self.now += 16
        failed = (await self.read()).json()
        self.assertTrue(failed['stale'])
        self.assertEqual(failed['containers'], first['containers'])
        self.assertEqual(failed['fetched_at'], first['fetched_at'])
        self.assertNotIn(TOKEN, json.dumps(failed))
        self.assertEqual(len(self.calls), 2)
        await self.read(); self.assertEqual(len(self.calls), 2)
        self.response = []; self.now += 16
        recovered = (await self.read()).json()
        self.assertFalse(recovered['stale']); self.assertEqual(recovered['containers'], [])

    async def test_auth_errors_and_redirects_do_not_leak_upstream_text_or_follow_redirects(self):
        for status in [401, 403, 302, 500]:
            self.response = httpx.Response(status, text=TOKEN, headers={'Location': 'http://other-server'})
            self.now += 16
            response = await self.read()
            self.assertTrue(response.json()['stale'])
            self.assertNotIn(TOKEN, response.text)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(str(request.url).startswith('http://wud:3000/') for request in self.calls))

    async def test_invalid_or_duplicate_payload_is_an_error_instead_of_empty_success(self):
        for payload in [{}, [None], [fixture(image={})], [fixture(), fixture()]]:
            self.response = payload; self.now += 16
            response = (await self.read()).json()
            self.assertTrue(response['stale']); self.assertIsNone(response['fetched_at'])

    async def test_timeout_and_invalid_address_fail_safely(self):
        self.response = httpx.ReadTimeout('private ' + TOKEN)
        self.assertIn('in time', (await self.read()).json()['error'])
        invalid = WudGateway(self.upstream, 'http://user:password@wud', TOKEN)
        result = await invalid.snapshot()
        self.assertTrue(result.stale); self.assertIsNone(result.url)
        self.assertNotIn('password', result.model_dump_json())
