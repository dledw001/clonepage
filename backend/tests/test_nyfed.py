import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from economy import INDICATORS, initialize_economy
from nyfed import NYFED, parse_auto_workbook, report_url
from test_economy import auto_workbook

TODAY = date(2026, 11, 15)
PATH = '/medialibrary/interactives/householdcredit/data/xls/HHD_C_Report_'


def edited_workbook(edit):
    workbook = load_workbook(io.BytesIO(auto_workbook()))
    edit(workbook)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class NewYorkFedTests(unittest.TestCase):
    def test_discovery_selects_latest_completed_report_without_guessing_urls(self):
        html = f'<a href="{PATH}2026Q2">Old</a><a href="{PATH}2026Q3">Data</a><a href="{PATH}2026Q4">Future</a>'
        self.assertEqual(report_url(html, TODAY), NYFED + PATH + '2026Q3')

    def test_discovery_rejects_other_hosts_schemes_ports_and_unapproved_paths(self):
        for url in ('http://www.newyorkfed.org' + PATH + '2026Q3',
                    'https://www.newyorkfed.org.evil.test' + PATH + '2026Q3',
                    'https://www.newyorkfed.org:444' + PATH + '2026Q3',
                    'https://user@www.newyorkfed.org' + PATH + '2026Q3',
                    'https://www.newyorkfed.org' + PATH + '2026Q3?token=secret',
                    'https://www.newyorkfed.org' + PATH + '2026Q3#fragment',
                    '/unrelated.xlsx'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                report_url(f'<a href="{url}">Data</a>', TODAY)

    def test_auto_values_are_percent_balances_with_native_quarters_and_unfilled_gaps(self):
        rows, month = parse_auto_workbook(auto_workbook([('25:Q3', 5.02), ('26:Q1', None), ('26:Q3', 5.66)]), TODAY)
        self.assertEqual(rows[0], {'date': '2025-07-01', 'value': 5.02})
        self.assertEqual(rows[-1], {'date': '2026-07-01', 'value': 5.66})
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['value'] is None for row in rows[1:-1]))
        self.assertEqual(month, '2026-11')

    def test_auto_column_is_selected_by_header_rather_than_position(self):
        content = edited_workbook(lambda workbook: workbook['Page 12 Data'].insert_cols(2))
        self.assertEqual(parse_auto_workbook(content, TODAY)[0][-1]['value'], 5.66)

    def test_transition_measure_and_wrong_units_or_headers_are_rejected(self):
        for cell, value in [('A1', 'Flow into Serious Delinquency (90+) by Loan Type'), ('A2', 'Fraction'), ('C4', 'OTHER')]:
            with self.subTest(cell=cell):
                content = edited_workbook(lambda workbook: setattr(workbook['Page 12 Data'][cell], 'value', value))
                with self.assertRaises(ValueError): parse_auto_workbook(content, TODAY)

    def test_bad_values_duplicates_and_quarter_labels_are_rejected(self):
        for rows in ([('26:Q3', -1)], [('26:Q3', 101)], [('26:Q3', True)],
                     [('26:Q3', '5.66')], [('26:Q3', 5), ('26:Q3', 6)], [('26:Q5', 5)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_auto_workbook(auto_workbook(rows), TODAY)

    def test_unreleased_quarters_and_unavailable_publication_month_are_not_invented(self):
        rows, month = parse_auto_workbook(auto_workbook([('26:Q3', 5.66), ('26:Q4', 7)]), TODAY)
        self.assertEqual(rows, [{'date': '2026-07-01', 'value': 5.66}])
        content = edited_workbook(lambda workbook: setattr(workbook['TABLE OF CONTENTS']['B1'], 'value', None))
        self.assertIsNone(parse_auto_workbook(content, TODAY)[1])

    def test_missing_contents_sheet_keeps_observations_without_inventing_a_date(self):
        content = edited_workbook(lambda workbook: workbook.remove(workbook['TABLE OF CONTENTS']))
        rows, month = parse_auto_workbook(content, TODAY)
        self.assertEqual(rows[-1]['value'], 5.66)
        self.assertIsNone(month)

    def test_invalid_and_expanded_oversized_archives_are_rejected(self):
        with self.assertRaises(ValueError): parse_auto_workbook(b'<html>Unavailable</html>', TODAY)
        stream = io.BytesIO()
        with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('oversized.xml', b' ' * (32 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError): parse_auto_workbook(stream.getvalue(), TODAY)

    def test_original_cache_schema_gains_source_columns_without_losing_observations(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(main, 'DB_PATH', Path(folder) / 'economic.sqlite3'):
            with main.database() as db:
                db.execute('CREATE TABLE economic_series (id TEXT PRIMARY KEY, observations TEXT, fetched_at TEXT, fred_updated_at TEXT, checked_at TEXT, next_check_at TEXT, error TEXT, metadata_warning TEXT)')
                db.execute("INSERT INTO economic_series(id,observations,checked_at) VALUES ('UNRATE','[]','2026-11-14')")
            initialize_economy()
            initialize_economy()
            with main.database() as db:
                row = dict(db.execute("SELECT * FROM economic_series WHERE id='UNRATE'").fetchone())
                self.assertEqual(row['observations'], '[]')
                self.assertEqual(row['checked_at'], '2026-11-14')
                self.assertIsNone(row['source_published_month'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM economic_series').fetchone()[0], len(INDICATORS))
