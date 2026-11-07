import os
import socket
import ssl
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dsm
import dsm_services as services


class ServiceHTTPSConfigTests(unittest.TestCase):
    def test_proxy_ip_is_optional_and_validated_as_an_ip(self):
        for value, expected in [("", None), ("10.0.0.149", "10.0.0.149"), ("2001:db8::1", "2001:db8::1")]:
            with self.subTest(value=value), patch.dict(os.environ, {"DSM_PROXY_IP": value}):
                self.assertEqual(dsm.DSMConfig.from_env().proxy_ip, expected)
        for value in ["https://10.0.0.149", "nas.example.com", "10.0.0.149:443"]:
            with self.subTest(value=value), patch.dict(os.environ, {"DSM_PROXY_IP": value}):
                with self.assertRaisesRegex(ValueError, "DSM_PROXY_IP"):
                    dsm.DSMConfig.from_env()


class ServiceHTTPSChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.real_client = httpx.AsyncClient
        self.requests = []
        self.options = []
        self.status = 200
        self.error = None

    def client(self, **kwargs):
        self.options.append(kwargs)
        return self.real_client(transport=httpx.MockTransport(self.respond), **kwargs)

    def respond(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, headers={"Location": "https://elsewhere.example/"})

    async def test_lan_check_preserves_service_host_and_tls_hostname_without_credentials(self):
        with patch.object(services.httpx, "AsyncClient", side_effect=self.client):
            await services.verify_https("plex.dledwith.com", "10.0.0.149")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://10.0.0.149/")
        self.assertEqual(request.headers["Host"], "plex.dledwith.com")
        self.assertEqual(request.extensions["sni_hostname"], "plex.dledwith.com")
        self.assertTrue(self.options[0]["verify"])
        self.assertFalse(self.options[0]["follow_redirects"])
        self.assertFalse(self.options[0]["trust_env"])
        for header in ["Authorization", "Cookie", "X-SYNO-TOKEN"]:
            self.assertNotIn(header, request.headers)

    async def test_no_override_uses_normal_service_dns(self):
        with patch.object(services.httpx, "AsyncClient", side_effect=self.client):
            await services.verify_https("plex.dledwith.com")
        self.assertEqual(self.requests[0].url.host, "plex.dledwith.com")
        self.assertEqual(self.requests[0].extensions["sni_hostname"], "plex.dledwith.com")

    async def test_ipv6_lan_address_keeps_service_hostname(self):
        with patch.object(services.httpx, "AsyncClient", side_effect=self.client):
            await services.verify_https("plex.dledwith.com", "2001:db8::1")
        self.assertEqual(str(self.requests[0].url), "https://[2001:db8::1]/")
        self.assertEqual(self.requests[0].headers["Host"], "plex.dledwith.com")

    async def test_protected_services_and_redirects_do_not_trigger_authenticated_followups(self):
        for status in [302, 401, 403]:
            with self.subTest(status=status):
                self.requests.clear()
                self.status = status
                with patch.object(services.httpx, "AsyncClient", side_effect=self.client):
                    await services.verify_https("plex.dledwith.com", "10.0.0.149")
                self.assertEqual(len(self.requests), 1)

    async def test_upstream_server_errors_block_completion_and_log_status(self):
        self.status = 502
        with patch.object(services.httpx, "AsyncClient", side_effect=self.client), self.assertLogs("dsm_services") as logs:
            with self.assertRaisesRegex(dsm.DSMError, "server error"):
                await services.verify_https("plex.dledwith.com", "10.0.0.149")
        self.assertIn("status=502", logs.output[0])

    async def test_network_failures_log_safe_reasons_without_raw_exception_data(self):
        cases = [(httpx.ConnectTimeout("secret-canary"), "timeout", "timed out"),
                 (httpx.ConnectError("secret-canary"), "connection", "could not connect"),
                 (httpx.ReadError("secret-canary"), "http", "communicating")]
        certificate = httpx.ConnectError("secret-canary")
        certificate.__cause__ = ssl.SSLCertVerificationError("secret-canary")
        cases.append((certificate, "certificate", "certificate could not be verified"))
        dns = httpx.ConnectError("secret-canary")
        dns.__cause__ = socket.gaierror("secret-canary")
        cases.append((dns, "dns", "could not be resolved"))
        for error, reason, message in cases:
            with self.subTest(reason=reason):
                self.error = error
                with patch.object(services.httpx, "AsyncClient", side_effect=self.client), self.assertLogs("dsm_services") as logs:
                    with self.assertRaisesRegex(dsm.DSMError, message) as raised:
                        await services.verify_https("plex.dledwith.com", "10.0.0.149")
                output = str(raised.exception) + " ".join(logs.output)
                self.assertIn("reason=" + reason, output)
                self.assertIn("hostname=plex.dledwith.com", output)
                self.assertIn("connect_host=10.0.0.149", output)
                self.assertNotIn("secret-canary", output)
