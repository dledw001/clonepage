"""Bookmark choices must stop fetching without losing the saved item."""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import worker
import saved_copies
from extractor import ExtractedDocument
from wud import ContainerSnapshot

TEXT = 'Searchable snapshot retained for offline reading. ' * 8


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(folder) / 'reading.sqlite3'))
        self.stack.enter_context(patch('main.fetch_title', return_value='Detected title'))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.stack.enter_context(patch.object(main.app.state.availability, 'snapshot', return_value={'readings': []}))
        async def wud_snapshot():
            return ContainerSnapshot(configured=False)
        self.stack.enter_context(patch.object(main.app.state.wud, 'snapshot', side_effect=wud_snapshot))
        self.article_id = self.client.post('/api/reading', json={'url': 'https://gitlab.com/example/game', 'title': 'Factory game'}).json()['id']
        self.path = '/api/reading/' + self.article_id

    def article(self):
        return next(item for item in self.client.get('/api/reading').json() if item['id'] == self.article_id)

    def mode(self, enabled, **changes):
        response = self.client.patch(self.path, json={'bookmark_only': enabled, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def document(self):
        return ExtractedDocument(TEXT, html=f'<html><body><p>{TEXT}</p></body></html>')

    def test_failure_becomes_bookmark_and_stops_both_queues_and_attention_warnings(self):
        with patch('worker.extract_article_text', side_effect=ValueError('No readable article text found')):
            self.assertTrue(worker.process_next())
        self.assertEqual(self.client.get('/api/home/attention').json()['counts']['copy'], 1)
        with main.database() as db:
            db.execute('UPDATE articles SET hn_story_id=12345 WHERE id=?', (self.article_id,))
        current = self.mode(True, is_read=True)
        self.assertIs(current['bookmark_only'], True)
        self.assertEqual(current['index_status'], 'skipped')
        self.assertEqual(current['copy_status'], 'skipped')
        for name in ('index_error', 'copy_error', 'index_retry_at', 'copy_retry_at'):
            self.assertIsNone(current[name])
        self.assertEqual(current['hn_discussion_url'], 'https://news.ycombinator.com/item?id=12345')
        with patch('worker.extract_article_text') as fetch:
            self.assertFalse(worker.process_next())
            fetch.assert_not_called()
        self.assertEqual(self.client.get('/api/home/attention').json()['counts']['copy'], 0)
        for endpoint in ('/retry', '/copy/refresh'):
            self.assertEqual(self.client.post(self.path + endpoint).status_code, 409)
        self.assertIs(self.article()['is_read'], True)
        self.assertIs(self.client.get(self.path + '/copy').json()['bookmark_only'], True)

    def test_bookmark_is_searchable_and_read_archive_rename_controls_keep_working(self):
        original = self.article()
        self.assertIs(original['bookmark_only'], False)
        self.mode(True)
        self.client.patch(self.path, json={'is_read': True, 'archived': True, 'title': 'Favorite game'})
        for query in ('Favorite', 'gitlab'):
            results = self.client.get('/api/reading/search', params={'q': query, 'archived': True}).json()
            self.assertEqual([item['id'] for item in results], [self.article_id])
            self.assertIs(results[0]['bookmark_only'], True)
        self.assertIsNotNone(self.article()['archived_at'])
        self.assertEqual(self.article()['saved_at'], original['saved_at'])
        self.client.patch(self.path, json={'archived': False, 'is_read': False})
        self.assertIsNone(self.article()['archived_at'])
        self.assertEqual(self.client.delete(self.path).status_code, 204)
        self.assertEqual(self.client.get('/api/reading/search', params={'q': 'Favorite'}).json(), [])

    def test_enabling_capture_resets_attempts_and_recovers_normally(self):
        self.mode(True)
        current = self.mode(False)
        self.assertEqual((current['index_status'], current['copy_status']), ('pending', 'pending'))
        self.assertEqual((current['index_attempts'], current['copy_attempts']), (0, 0))
        with patch('worker.extract_article_text', return_value=self.document()) as fetch:
            self.assertTrue(worker.process_next())
            fetch.assert_called_once()
        current = self.article()
        self.assertEqual((current['index_status'], current['copy_status']), ('ready', 'ready'))
        self.assertTrue(current['copy_available'])

    def test_old_worker_cannot_publish_after_bookmark_choice_or_reenable(self):
        for reenable in (False, True):
            with self.subTest(reenable=reenable):
                if self.article()['bookmark_only']:
                    self.mode(False)
                def choose_during_fetch(url):
                    self.mode(True)
                    if reenable:
                        self.mode(False)
                    return self.document()
                with patch('worker.extract_article_text', side_effect=choose_during_fetch):
                    self.assertTrue(worker.process_next())
                current = self.article()
                self.assertFalse(current['copy_available'])
                self.assertFalse((saved_copies.copy_root() / self.article_id).exists())
                self.assertEqual(current['copy_status'], 'pending' if reenable else 'skipped')
                self.assertEqual(self.client.get('/api/reading/search', params={'q': 'snapshot'}).json(), [])

    def test_existing_snapshot_and_search_text_survive_bookmark_choice(self):
        with patch('worker.extract_article_text', return_value=self.document()):
            worker.process_next()
        original = self.client.get(self.path + '/copy/download').content
        with main.database() as db:
            db.execute("UPDATE articles SET copy_status='failed',copy_error='Refresh failed' WHERE id=?", (self.article_id,))
        current = self.mode(True)
        self.assertTrue(current['copy_available'])
        self.assertEqual(self.client.get(self.path + '/copy/download').content, original)
        self.assertEqual(self.client.get('/api/reading/search', params={'q': 'snapshot'}).json()[0]['id'], self.article_id)
        self.assertEqual(self.client.get('/api/home/attention').json()['counts']['copy'], 0)

    def test_repeated_choice_does_not_cancel_active_capture_or_queue_another(self):
        self.mode(True)
        self.mode(True)
        self.mode(False)
        def repeat_during_fetch(url):
            self.mode(False)
            return self.document()
        with patch('worker.extract_article_text', side_effect=repeat_during_fetch) as fetch:
            self.assertTrue(worker.process_next())
            self.assertEqual(self.mode(False)['copy_status'], 'ready')
            self.assertFalse(worker.process_next())
            fetch.assert_called_once()

    def test_invalid_missing_and_uploaded_items_cannot_change_mode(self):
        self.assertEqual(self.client.patch(self.path, json={'bookmark_only': None}).status_code, 422)
        self.assertEqual(self.client.patch('/api/reading/missing', json={'bookmark_only': True}).status_code, 404)
        pdf = PdfWriter()
        pdf.add_blank_page(width=100, height=100)
        stream = BytesIO()
        pdf.write(stream)
        uploaded = self.client.post('/api/reading/upload', content=stream.getvalue(), headers={'Content-Type': 'application/pdf', 'X-Filename': 'local.pdf'}).json()
        for mode in (True, False):
            self.assertEqual(self.client.patch('/api/reading/' + uploaded['id'], json={'bookmark_only': mode}).status_code, 409)
        self.assertFalse(next(item for item in self.client.get('/api/reading').json() if item['id'] == uploaded['id'])['bookmark_only'])


class BookmarkMigrationTests(unittest.TestCase):
    def test_old_database_defaults_to_capture_and_bookmark_choice_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(main, 'DB_PATH', Path(folder) / 'old.sqlite3'):
            with closing(sqlite3.connect(main.DB_PATH)) as db:
                db.execute('CREATE TABLE articles (id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL UNIQUE,is_read INTEGER NOT NULL DEFAULT 0,saved_at TEXT NOT NULL)')
                db.execute("INSERT INTO articles VALUES ('old','Old bookmark','https://example.com/old',0,'2026-01-01')")
                db.commit()
            with TestClient(main.app) as client:
                self.assertIs(client.get('/api/reading').json()[0]['bookmark_only'], False)
                self.assertEqual(client.patch('/api/reading/old', json={'bookmark_only': True}).status_code, 200)
            with TestClient(main.app) as client:
                current = client.get('/api/reading').json()[0]
                self.assertIs(current['bookmark_only'], True)
                self.assertEqual(current['copy_status'], 'skipped')
                with patch('worker.extract_article_text') as fetch:
                    self.assertFalse(worker.process_next())
                    fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
