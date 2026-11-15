import asyncio
import json
import io
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

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


def auto_workbook(rows=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Page 12 Data'
    sheet.append(['Percent of Balance 90+ Days Delinquent by Loan Type '])
    sheet.append(['Percent'])
    sheet.append(['Return to Table of Contents'])
    sheet.append([None, 'MORTGAGE', 'AUTO', 'CC'])
    for quarter, value in rows or [('25:Q3', 5.02), ('26:Q3', 5.66)]:
        sheet.append([quarter, 1.13, value, 12.9])
    contents = workbook.create_sheet('TABLE OF CONTENTS')
    contents.append([None, 'November 2026'])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class CalculationTests(unittest.TestCase):
    def test_allowlist_contains_exactly_seventeen_requested_series(self):
        self.assertEqual(set(BY_ID), set('UNRATE ICSA PCEPILFE GDPC1 DGS10 MORTGAGE30US DSPIC96 DRCCLACBS BAMLH0A0HYM2 T10Y2Y IPMAN SP500 VIXCLS DRSFRMACBS TDSP PSAVERT NYFED_AUTO90'.split()))

    def test_market_group_contains_equities_volatility_and_credit_risk(self):
        self.assertEqual([item.id for item in INDICATORS if item.group == 'Market conditions'], ['SP500', 'VIXCLS', 'BAMLH0A0HYM2'])

    def test_equity_change_is_a_price_return_not_index_point_difference(self):
        card = snapshot('SP500', raw(('2026-10-09', 5000), ('2026-10-12', None), ('2026-10-13', 5100), ('2026-11-11', 5500)))
        self.assertEqual(card['latest']['value'], 5500)
        self.assertAlmostEqual(card['change']['value'], 10)
        self.assertEqual(card['change']['unit'], '%')
        self.assertEqual(card['change']['comparison_date'], '2026-10-09')
        self.assertEqual(card['unit'], 'index')
        self.assertIsNone(snapshot('SP500', raw(('2026-11-11', 5500)))['change']['value'])
        self.assertIsNone(snapshot('SP500', raw(('2026-10-09', 0), ('2026-11-11', 5500)))['change']['value'])

    def test_vix_level_and_change_remain_index_points(self):
        card = snapshot('VIXCLS', raw(('2026-10-09', 16), ('2026-10-12', None), ('2026-10-13', 17), ('2026-11-11', 18.7)))
        self.assertEqual(card['latest']['value'], 18.7)
        self.assertAlmostEqual(card['change']['value'], 2.7)
        self.assertEqual(card['change']['unit'], 'points')
        self.assertEqual(card['change']['comparison_date'], '2026-10-09')

    def test_equity_drawdown_uses_52_week_closing_high_not_old_or_future_peaks(self):
        card = snapshot('SP500', raw(('2025-01-01', 9000), ('2025-11-12', 5000), ('2025-11-13', 5500), ('2026-06-01', 6000), ('2026-11-12', 5400), ('2026-11-13', None)))
        self.assertAlmostEqual(card['drawdown']['value'], -10)
        self.assertEqual(card['drawdown']['high'], 6000)
        self.assertEqual(card['drawdown']['high_date'], '2026-06-01')
        self.assertEqual(card['drawdown']['window_start'], '2025-11-13')
        self.assertEqual(card['latest']['date'], '2026-11-12')
        at_high = snapshot('SP500', raw(('2025-11-12', 5000), ('2026-11-12', 6000)))
        self.assertEqual(at_high['drawdown']['value'], 0)

    def test_equity_drawdown_is_unavailable_without_full_window_coverage(self):
        self.assertIsNone(snapshot('SP500', raw(('2026-06-01', 6000), ('2026-11-12', 5400)))['drawdown'])
        self.assertIsNone(snapshot('VIXCLS', raw(('2026-11-12', 18.7)))['drawdown'])

    def test_household_rates_use_year_over_year_percentage_point_changes(self):
        for series in ('DRSFRMACBS', 'TDSP', 'NYFED_AUTO90'):
            with self.subTest(series=series):
                card = snapshot(series, raw(('2025-07-01', 3), ('2026-04-01', 4), ('2026-07-01', 4.5)))
                self.assertEqual(card['latest']['value'], 4.5)
                self.assertEqual(card['change']['value'], 1.5)
                self.assertEqual(card['change']['unit'], 'pp')
                self.assertEqual(card['change']['comparison_date'], '2025-07-01')
                self.assertIsNone(snapshot(series, raw(('2025-04-01', 3), ('2026-07-01', 4.5)))['change']['value'])
        saving = snapshot('PSAVERT', raw(('2025-09-01', 4), ('2026-08-01', 4.2), ('2026-09-01', 3.8)))
        self.assertAlmostEqual(saving['change']['value'], -.2)
        self.assertEqual(saving['latest']['value'], 3.8)  # no compounding or annualizing a published rate
        self.assertEqual(saving['change']['comparison_date'], '2025-09-01')

    def test_household_sources_and_definitions_distinguish_delinquency_measures(self):
        for series in ('DRCCLACBS', 'DRSFRMACBS'):
            self.assertIn('30+', BY_ID[series].basis)
            self.assertIn('nonaccrual', BY_ID[series].basis)
        auto = snapshot('NYFED_AUTO90', [])
        self.assertEqual(auto['provider'], 'nyfed')
        self.assertEqual(auto['source_label'], 'New York Fed')
        self.assertEqual(auto['source_url'], 'https://www.newyorkfed.org/microeconomics/hhdc')
        self.assertIn('90+', auto['basis'])

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
        self.auto_data = auto_workbook()
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
            if request.url.host == 'www.newyorkfed.org':
                if request.url.path.endswith('hhdc-iframe'):
                    return httpx.Response(200, text='<a href="/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_2026Q3">Data</a>')
                return httpx.Response(200, content=self.auto_data)
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
        self.assertEqual(len(first['indicators']), len(INDICATORS))
        self.assertTrue(first['refreshing'])
        self.assertTrue(all(item['latest'] is None for item in first['indicators']))
        task = self.gateway.task
        await self.gateway.snapshot()
        self.assertIs(self.gateway.task, task)
        await task
        second = await self.gateway.snapshot()
        self.assertFalse(second['refreshing'])
        self.assertEqual(len(self.calls), 2 * len(INDICATORS))
        self.assertLessEqual(self.peak, 2)
        self.assertIsNotNone(second['indicators'][0]['checked_at'])
        self.assertTrue(all(request.url.host in ('fred.stlouisfed.org', 'www.newyorkfed.org') for request in self.calls))

    async def test_existing_cache_is_preserved_when_market_series_are_added(self):
        await self.gateway.refresh_due()
        original = self.row('UNRATE')
        with main.database() as db:
            db.execute("DELETE FROM economic_series WHERE id IN ('SP500', 'VIXCLS')")
            self.assertEqual(db.execute('SELECT COUNT(*) FROM economic_series').fetchone()[0], len(INDICATORS) - 2)
        initialize_economy()
        self.assertEqual(self.row('UNRATE'), original)
        self.assertIsNone(self.row('SP500')['observations'])
        self.assertIsNone(self.row('VIXCLS')['fetched_at'])
        self.calls.clear()
        await self.gateway.refresh_due()
        self.assertEqual(len(self.calls), 4)  # CSV and metadata for just the two new series
        self.assertIsNotNone(self.row('SP500')['observations'])
        self.assertIsNotNone(self.row('VIXCLS')['observations'])
        self.assertEqual(self.row('UNRATE'), original)

    async def test_household_upgrade_preserves_existing_thirteen_series(self):
        await self.gateway.refresh_due()
        original = self.row('SP500')
        new_ids = ('DRSFRMACBS', 'TDSP', 'PSAVERT', 'NYFED_AUTO90')
        with main.database() as db:
            db.executemany('DELETE FROM economic_series WHERE id=?', [(item,) for item in new_ids])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM economic_series').fetchone()[0], 13)
        initialize_economy()
        self.calls.clear()
        await self.gateway.refresh_due()
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(self.row('SP500'), original)
        self.assertTrue(all(self.row(item)['observations'] for item in new_ids))

    async def test_auto_cache_revisions_failure_retention_and_source_metadata(self):
        indicator = BY_ID['NYFED_AUTO90']
        await self.gateway.collect(indicator)
        original = self.row(indicator.id)
        self.assertEqual(original['source_published_month'], '2026-11')
        self.assertTrue(original['source_data_url'].endswith('2026Q3'))
        self.assertIsNone(original['fred_updated_at'])
        self.assertIsNone(original['metadata_warning'])
        self.now += timedelta(days=1)
        self.auto_data = b'not an XLSX file'
        await self.gateway.collect(indicator)
        failed = self.row(indicator.id)
        self.assertEqual(failed['observations'], original['observations'])
        self.assertEqual(failed['source_published_month'], original['source_published_month'])
        self.assertEqual(failed['fetched_at'], original['fetched_at'])
        self.assertIn('New York Fed', failed['error'])
        self.auto_data = auto_workbook([('25:Q3', 4.9), ('26:Q3', 5.66)])
        await self.gateway.collect(indicator)
        card = present(indicator, self.row(indicator.id), self.now)
        self.assertAlmostEqual(card['change']['value'], .76)
        self.assertIsNone(card['error'])

    async def test_persistent_daily_cache_survives_gateway_restart_and_revises_history(self):
        await self.gateway.refresh_due()
        original = snapshot('PCEPILFE', json.loads(self.row('PCEPILFE')['observations']))['latest']['value']
        replacement = EconomyGateway(self.client, lambda:self.now)
        self.addAsyncCleanup(replacement.close)
        await replacement.snapshot()
        self.assertIsNone(replacement.task)
        self.assertEqual(len(self.calls), 2 * len(INDICATORS))
        self.now += timedelta(seconds=DAY)
        self.revision = 1
        await replacement.refresh_due()
        changed = snapshot('PCEPILFE', json.loads(self.row('PCEPILFE')['observations']))['latest']['value']
        self.assertNotEqual(original, changed)
        self.assertAlmostEqual(changed, 100 * (105 / 101 - 1))
        self.assertEqual(len(self.calls), 4 * len(INDICATORS))

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
        self.assertTrue(all(request.url.host in ('fred.stlouisfed.org', 'www.newyorkfed.org') for request in self.calls))

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
