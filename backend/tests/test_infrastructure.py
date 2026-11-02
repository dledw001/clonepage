import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI

from nas_temps import NasTempsGateway, router


class InfrastructureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.0
        self.calls = []
        self.responses = {
            "/api/metrics": [{"key": "cpu.usage", "label": "CPU Utilization", "unit": "%"}],
            "/api/latest": [{"metric": "cpu.usage", "ts": 1000, "value": 0, "unit": "%", "age_seconds": 2}],
            "/api/health": {"ok": True, "last_collection_ts": 1000},
            "/api/series": {"metric": "cpu.usage", "unit": "%", "points": [
                {"ts": 1000, "value": 20, "min": 0, "max": 90, "samples": 4}
            ]},
        }

        def upstream(request):
            self.calls.append(request)
            value = self.responses[request.url.path]
            if isinstance(value, Exception):
                raise value
            if isinstance(value, httpx.Response):
                return value
            return httpx.Response(200, json=value)

        self.upstream = httpx.AsyncClient(base_url="http://nas/", transport=httpx.MockTransport(upstream))
        app = FastAPI()
        app.include_router(router)
        self.gateway = NasTempsGateway(self.upstream, clock=lambda: self.now)
        app.state.nas_temps = self.gateway
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://clonepage")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()

    async def test_snapshot_and_concurrent_requests_share_cache(self):
        a, b = await asyncio.gather(self.client.get("/api/infrastructure/snapshot"), self.client.get("/api/infrastructure/snapshot"))
        self.assertEqual(a.status_code, 200)
        self.assertEqual(a.json(), b.json())
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(a.json()["readings"][0]["value"], 0)
        self.assertEqual(a.json()["errors"], {})

    async def test_snapshot_expiry_fetches_new_data(self):
        await self.client.get("/api/infrastructure/snapshot")
        self.now += 6
        self.responses["/api/latest"][0]["value"] = 34
        result = await self.client.get("/api/infrastructure/snapshot")
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(result.json()["readings"][0]["value"], 34)

    async def test_outage_preserves_last_readings_and_original_timestamps(self):
        await self.client.get("/api/infrastructure/snapshot")
        self.now += 6
        self.responses["/api/latest"] = httpx.ConnectError("offline")
        result = (await self.client.get("/api/infrastructure/snapshot")).json()
        self.assertEqual(result["readings"][0]["ts"], 1000)
        self.assertEqual(result["readings"][0]["value"], 0)
        self.assertIn("readings", result["errors"])
        self.assertEqual(len(result["metrics"]), 1)

    async def test_initial_outage_returns_explicit_errors_without_fake_data(self):
        for path in self.responses:
            self.responses[path] = httpx.ConnectError("offline")
        result = (await self.client.get("/api/infrastructure/snapshot")).json()
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["readings"], [])
        self.assertIsNone(result["health"])
        self.assertEqual(set(result["errors"]), {"metrics", "readings", "health"})

    async def test_invalid_readings_do_not_replace_cached_data(self):
        await self.client.get("/api/infrastructure/snapshot")
        self.now += 6
        self.responses["/api/latest"] = [{"metric": "cpu.usage", "ts": 1000, "value": "not-a-number", "unit": "%"}]
        result = (await self.client.get("/api/infrastructure/snapshot")).json()
        self.assertIn("readings", result["errors"])
        self.assertEqual(result["readings"][0]["value"], 0)

    async def test_recovery_clears_errors(self):
        self.responses["/api/health"] = httpx.ReadTimeout("slow collector")
        first = (await self.client.get("/api/infrastructure/snapshot")).json()
        self.assertIn("health", first["errors"])
        self.now += 6
        self.responses["/api/health"] = {"ok": True}
        recovered = (await self.client.get("/api/infrastructure/snapshot")).json()
        self.assertEqual(recovered["errors"], {})

    async def test_series_preserves_bucket_extremes_and_forwards_parameters(self):
        result = await self.client.get("/api/infrastructure/series", params={"metric": "cpu.usage", "since_seconds": 86400, "max_points": 360})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["points"][0], {"ts": 1000, "value": 20, "min": 0, "max": 90, "samples": 4})
        self.assertEqual(dict(self.calls[0].url.params), {"metric": "cpu.usage", "since_seconds": "86400", "max_points": "360"})

    async def test_series_cache_expires_and_does_not_mix_ranges(self):
        for seconds in (3600, 3600, 86400):
            await self.client.get("/api/infrastructure/series", params={"metric": "cpu.usage", "since_seconds": seconds})
        self.assertEqual(len(self.calls), 2)
        self.now += 16
        await self.client.get("/api/infrastructure/series", params={"metric": "cpu.usage"})
        self.assertEqual(len(self.calls), 3)

    async def test_series_timeout_and_wrong_metric_are_explicit_failures(self):
        self.responses["/api/series"] = httpx.ReadTimeout("timeout")
        result = await self.client.get("/api/infrastructure/series", params={"metric": "cpu.usage"})
        self.assertEqual(result.status_code, 504)
        self.responses["/api/series"] = {"metric": "other", "unit": "C", "points": []}
        result = await self.client.get("/api/infrastructure/series", params={"metric": "cpu.usage"})
        self.assertEqual(result.status_code, 502)

    async def test_series_parameters_are_validated_before_contacting_upstream(self):
        for params in ({}, {"metric": ""}, {"metric": "cpu.usage", "since_seconds": 10}, {"metric": "cpu.usage", "max_points": 5001}):
            self.assertEqual((await self.client.get("/api/infrastructure/series", params=params)).status_code, 422)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
