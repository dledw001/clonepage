import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from economy import (INDICATORS, DAY, EconomyGateway, change_for, fred_timestamp, growth,
                     initialize_economy, months_before, parse_csv, present, transformed)

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
BY_ID = {item.id: item for item in INDICATORS}
META = b'<span class="series-meta-updated-date">Nov 13, 2026</span><span class="series-meta-updated-time">3:16 PM CST</span>'


def raw(*pairs):
    return [{"date": day, "value": value} for day, value in pairs]


def snapshot(series_id, observations):
    return present(BY_ID[series_id], {"observations": json.dumps(observations), "fetched_at": NOW.isoformat()}, NOW)


class CalculationTests(unittest.TestCase):
    def test_allowlist_contains_exactly_eleven_requested_series(self):
        self.assertEqual(set(BY_ID), set('UNRATE ICSA PCEPILFE GDPC1 DGS10 MORTGAGE30US DSPIC96 DRCCLACBS BAMLH0A0HYM2 T10Y2Y IPMAN'.split()))

    def test_calendar_month_subtraction_clamps_end_of_month_and_leap_days(self):
        self.assertEqual(months_before(date(2024, 5, 31), 3), date(2024, 2, 29))
        self.assertEqual(months_before(date(2024, 2, 29), 12), date(2023, 2, 28))

    def test_yoy_and_three_month_annualized_use_levels_and_distinct_bases(self):
        observations = raw(('2025-09-01', 100), ('2026-05-01', 102), ('2026-06-01', 103), ('2026-08-01', 104), ('2026-09-01', 105))
        card = snapshot('PCEPILFE', observations)
        self.assertAlmostEqual(card['latest']['value'], 5)
        self.assertAlmostEqual(card['latest']['secondary'], 100 * ((105 / 103) ** 4 - 1))
        self.assertIsNone(card['change']['value'])  # no August 2025 base
        for series in ('DSPIC96', 'IPMAN'):
            self.assertAlmostEqual(snapshot(series, observations)['latest']['value'], 5)

    def test_missing_month_is_not_replaced_by_twelfth_previous_available_row(self):
        observations = [{'date': months_before(date(2026, 10, 1), i).isoformat(), 'value': 100 + i} for i in range(14) if i != 12]
        points = transformed(BY_ID['PCEPILFE'], sorted(observations, key=lambda item:item['date']))
        self.assertIsNone(points[-1]['value'])
        self.assertIsNotNone(points[-1]['secondary'])

    def test_quarterly_gdp_is_compounded_and_not_multiplied_twice(self):
        card = snapshot('GDPC1', raw(('2026-01-01', 100), ('2026-04-01', 101), ('2026-07-01', 103)))
        expected = 100 * ((103 / 101) ** 4 - 1)
        self.assertAlmostEqual(card['latest']['value'], expected)
        self.assertAlmostEqual(card['change']['value'], expected - 100 * (1.01 ** 4 - 1))
        self.assertEqual(card['change']['comparison_date'], '2026-04-01')

    def test_missing_quarter_is_not_silently_skipped(self):
        points = transformed(BY_ID['GDPC1'], raw(('2026-01-01', 100), ('2026-07-01', 110)))
        self.assertIsNone(points[-1]['value'])

    def test_claims_average_uses_four_consecutive_weeks_and_prior_week_average(self):
        observations = raw(('2026-10-10', 200000), ('2026-10-17', 195000), ('2026-10-24', 195000), ('2026-10-31', 191000), ('2026-11-07', 200000))
        card = snapshot('ICSA', observations)
        self.assertEqual(card['latest']['value'], 195250)
        self.assertEqual(card['change']['value'], 0)
        self.assertIsNone(transformed(BY_ID['ICSA'], observations[:1] + observations[2:])[-1]['value'])

    def test_unemployment_uses_two_nonoverlapping_three_month_windows(self):
        observations = raw(('2026-05-01', 3.8), ('2026-06-01', 3.9), ('2026-07-01', 4), ('2026-08-01', 4.1), ('2026-09-01', 4.2), ('2026-10-01', 4.3))
        card = snapshot('UNRATE', observations)
        self.assertEqual(card['latest']['value'], 4.3)
        self.assertAlmostEqual(card['change']['value'], .3)
        self.assertAlmostEqual(card['change']['current_average'], 4.2)
        self.assertAlmostEqual(card['change']['previous_average'], 3.9)
        self.assertIsNone(snapshot('UNRATE', observations[1:])['change']['value'])

    def test_rate_comparison_is_on_or_before_calendar_target_and_skips_holidays(self):
        card = snapshot('DGS10', raw(('2026-10-09', 4), ('2026-10-12', None), ('2026-10-13', 4.2), ('2026-11-11', 4.8)))
        self.assertEqual(card['change']['comparison_date'], '2026-10-09')
        self.assertAlmostEqual(card['change']['value'], .8)
        self.assertEqual(card['change']['unit'], 'pp')

    def test_weekly_mortgage_uses_three_calendar_months_not_ninety_days(self):
        card = snapshot('MORTGAGE30US', raw(('2026-08-06', 7.5), ('2026-08-13', 7.6), ('2026-11-12', 7.11)))
        self.assertEqual(card['change']['comparison_date'], '2026-08-06')
        self.assertAlmostEqual(card['change']['value'], -.39)

    def test_spreads_and_spread_changes_are_basis_points_including_negative_curve(self):
        for series_id in ('BAMLH0A0HYM2', 'T10Y2Y'):
            card = snapshot(series_id, raw(('2026-10-14', -.25), ('2026-11-13', .27)))
            self.assertEqual(card['latest']['value'], 27)
            self.assertEqual(card['change']['value'], 52)
            self.assertEqual(card['change']['unit'], 'bps')

    def test_delinquency_yoy_change_is_percentage_points_four_quarters_back(self):
        card = snapshot('DRCCLACBS', raw(('2025-04-01', 3.04), ('2026-01-01', 2.91), ('2026-04-01', 2.85)))
        self.assertAlmostEqual(card['change']['value'], -.19)
        self.assertEqual(card['change']['comparison_date'], '2025-04-01')

    def test_missing_latest_observation_does_not_manufacture_a_new_period(self):
        card = snapshot('UNRATE', raw(('2026-09-01', 4.2), ('2026-10-01', 4.1), ('2026-11-01', None)))
        self.assertEqual(card['latest']['date'], '2026-10-01')
        self.assertIsNone(card['points'][-1]['value'])

    def test_ice_history_is_trimmed_to_rolling_three_year_window(self):
        card = snapshot('BAMLH0A0HYM2', raw(('2022-01-03', 4), ('2023-11-13', 4), ('2023-11-14', 4), ('2026-11-13', 3.2)))
        self.assertEqual([point['date'] for point in card['points']], ['2023-11-14', '2026-11-13'])
        self.assertEqual(card['history_limit_years'], 3)

    def test_invalid_or_zero_growth_bases_do_not_produce_infinite_results(self):
        for previous in (None, 0, -1):
            self.assertIsNone(growth(100, previous))
        self.assertIsNone(growth(1e300, 1e-300, 4))

    def test_csv_accepts_missing_values_without_filling_and_ignores_future(self):
        rows = parse_csv(b'observation_date,UNRATE\n2026-10-01,4.1\n2026-09-01,.\n2026-12-01,5\n', 'UNRATE', NOW.date())
        self.assertEqual(rows, raw(('2026-09-01', None), ('2026-10-01', 4.1)))
        for content in (b'<html>Blocked</html>', b'DATE,WRONG\n2026-01-01,1', b'DATE,UNRATE\n2026-01-01,nan', b'DATE,UNRATE\n2026-01-01,1\n2026-01-01,2'):
            with self.assertRaises(ValueError):parse_csv(content, 'UNRATE', NOW.date())

    def test_fred_metadata_preserves_publication_timezone_and_can_be_missing(self):
        self.assertEqual(fred_timestamp(META), '2026-11-13T15:16:00-06:00')
        self.assertEqual(fred_timestamp(META.replace(b'CST', b'CDT')), '2026-11-13T15:16:00-05:00')
        self.assertIsNone(fred_timestamp(b'<html>Maintenance</html>'))


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(folder) / 'economic.sqlite3'))
        initialize_economy()
        self.now = NOW
        self.calls = []
        self.mode = 'ok'
        self.revision = 0
        self.in_flight = 0
        self.peak = 0
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle), follow_redirects=False)
        self.addAsyncCleanup(self.client.aclose)
        self.gateway = EconomyGateway(self.client, lambda:self.now)
        self.addAsyncCleanup(self.gateway.close)

    async def handle(self, request):
        self.calls.append(request)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0)
            if request.url.path.startswith('/series/'):
                return httpx.Response(503 if self.mode == 'metadata' else 200, content=META)
            series_id = request.url.params['id']
            if self.mode == 'failure' and series_id == 'UNRATE':return httpx.Response(429)
            if self.mode == 'redirect':return httpx.Response(302, headers={'Location':'http://localhost/'})
            if self.mode == 'oversize':return httpx.Response(200, content=b'x' * (2 * 1024 * 1024 + 1))
            if self.mode == 'malformed':return httpx.Response(200, content=b'<html>not CSV</html>')
            if self.mode == 'timeout':raise httpx.ReadTimeout('private connection detail', request=request)
            if self.mode == 'waiting':await asyncio.sleep(60)
            body = 'observation_date,' + series_id + '\n2025-10-01,' + str(100 + self.revision) + '\n2026-07-01,103\n2026-10-01,105\n'
            return httpx.Response(200, text=body)
        finally:
            self.in_flight -= 1

    def row(self, series_id='UNRATE'):
        with main.database() as db:return dict(db.execute('SELECT * FROM economic_series WHERE id=?', (series_id,)).fetchone())

    async def test_lazy_snapshot_starts_one_bounded_refresh_and_returns_immediately(self):
        first = await self.gateway.snapshot()
        self.assertEqual(len(first['indicators']), 11)
        self.assertTrue(first['refreshing'])
        self.assertTrue(all(item['latest'] is None for item in first['indicators']))
        task = self.gateway.task
        await self.gateway.snapshot()
        self.assertIs(self.gateway.task, task)
        await task
        second = await self.gateway.snapshot()
        self.assertFalse(second['refreshing'])
        self.assertEqual(len(self.calls), 22)
        self.assertLessEqual(self.peak, 2)
        self.assertIsNotNone(second['indicators'][0]['checked_at'])
        self.assertTrue(all(request.url.host == 'fred.stlouisfed.org' for request in self.calls))

    async def test_persistent_daily_cache_survives_gateway_restart_and_revises_history(self):
        await self.gateway.refresh_due()
        original = snapshot('PCEPILFE', json.loads(self.row('PCEPILFE')['observations']))['latest']['value']
        replacement = EconomyGateway(self.client, lambda:self.now)
        self.addAsyncCleanup(replacement.close)
        await replacement.snapshot()
        self.assertIsNone(replacement.task)
        self.assertEqual(len(self.calls), 22)
        self.now += timedelta(seconds=DAY)
        self.revision = 1
        await replacement.refresh_due()
        changed = snapshot('PCEPILFE', json.loads(self.row('PCEPILFE')['observations']))['latest']['value']
        self.assertNotEqual(original, changed)
        self.assertAlmostEqual(changed, 100 * (105 / 101 - 1))
        self.assertEqual(len(self.calls), 44)

    async def test_partial_failure_retains_data_and_retries_only_due_series(self):
        await self.gateway.refresh_due()
        original = self.row()
        self.now += timedelta(days=1)
        self.mode = 'failure'
        await self.gateway.refresh_due()
        failed = self.row()
        self.assertEqual(failed['observations'], original['observations'])
        self.assertEqual(failed['fetched_at'], original['fetched_at'])
        self.assertNotEqual(failed['checked_at'], original['checked_at'])
        self.assertIn('429', failed['error'])
        self.assertIsNone(self.row('GDPC1')['error'])
        before = len(self.calls)
        await self.gateway.refresh_due()
        self.assertEqual(len(self.calls), before)
        self.now += timedelta(hours=1)
        self.mode = 'ok'
        await self.gateway.refresh_due()
        self.assertEqual(len(self.calls), before + 2)
        self.assertIsNone(self.row()['error'])

    async def test_metadata_failure_keeps_new_observations_without_inventing_update_date(self):
        self.mode = 'metadata'
        await self.gateway.collect(BY_ID['UNRATE'])
        row = self.row()
        self.assertIsNotNone(row['observations'])
        self.assertIsNone(row['fred_updated_at'])
        self.assertIsNone(row['error'])
        self.assertIsNotNone(row['metadata_warning'])

    async def test_bad_responses_do_not_replace_a_good_snapshot_or_leak_diagnostics(self):
        await self.gateway.collect(BY_ID['UNRATE'])
        original = self.row()['observations']
        for mode in ('timeout', 'redirect', 'oversize', 'malformed'):
            self.mode = mode
            await self.gateway.collect(BY_ID['UNRATE'])
            self.assertEqual(self.row()['observations'], original)
            self.assertIsNotNone(self.row()['error'])
            self.assertNotIn('private connection detail', self.row()['error'])
        self.assertTrue(all(request.url.host == 'fred.stlouisfed.org' for request in self.calls))

    async def test_close_cancels_active_download_without_publishing_empty_data(self):
        self.mode = 'waiting'
        await self.gateway.snapshot()
        await asyncio.sleep(0)
        await self.gateway.close()
        self.assertTrue(self.gateway.task.done())
        self.assertIsNone(self.row()['observations'])
        self.assertIsNone(self.row()['fetched_at'])


class EconomicAPITests(unittest.TestCase):
    def test_dashboard_endpoint_has_all_indicators_and_migration_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(main, 'DB_PATH', Path(folder) / 'reading.sqlite3'), patch('economy.EconomyGateway.start_due'):
            with TestClient(main.app) as client:
                response = client.get('/api/economy')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual({item['id'] for item in response.json()['indicators']}, set(BY_ID))
                self.assertTrue(all(item['latest'] is None for item in response.json()['indicators']))
                with main.database() as db:
                    db.execute("UPDATE economic_series SET error='retained' WHERE id='UNRATE'")
            with TestClient(main.app) as client:
                self.assertEqual(client.get('/api/economy').json()['indicators'][0]['error'], 'retained')
                self.assertEqual(client.post('/api/economy').status_code, 405)


if __name__ == '__main__':
    unittest.main()
