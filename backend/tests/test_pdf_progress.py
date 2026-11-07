import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from test_document_uploads import pdf_bytes


class PDFProgressTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(folder) / 'reading.sqlite3'))
        self.client = self.stack.enter_context(TestClient(main.app))
        response = self.client.post('/api/reading/upload', content=pdf_bytes(), headers={'Content-Type':'application/pdf','X-Filename':'notes.pdf'})
        self.article = response.json(); self.id = self.article['id']
        self.path = f'/api/reading/{self.id}/progress'
        self.version = self.article['copy_saved_at']
        self.position = {'page': 3, 'page_count': 10, 'left': 0, 'top': 234.5}

    def save(self, revision=0, position='default', version=None):
        return self.client.post(self.path, json={'copy_version': version or self.version, 'revision': revision,
            'position': self.position if position == 'default' else position})

    def test_progress_round_trip_appears_in_list_and_content_search_without_marking_read(self):
        initial = self.client.get(self.path)
        self.assertEqual(initial.json(), {'copy_version':self.version, 'revision':0, 'position':None})
        self.assertEqual(initial.headers['cache-control'], 'no-store')
        saved = self.save()
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['revision'], 1)
        item = self.client.get('/api/reading').json()[0]
        self.assertEqual(item['reading_progress']['page'], 3)
        self.assertFalse(item['is_read'])
        self.assertNotIn('pdf_progress_revision', item)
        found = self.client.get('/api/reading/search', params={'q':'notes'}).json()[0]
        self.assertEqual(found['reading_progress']['page_count'], 10)
        self.assertEqual(self.client.get(self.path).json()['position']['top'], 234.5)

    def test_stale_sessions_cannot_overwrite_newer_progress(self):
        self.assertEqual(self.save().status_code, 200)
        stale = self.save(position={**self.position, 'page':2})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['detail']['reason'], 'progress_changed')
        self.assertEqual(self.client.get(self.path).json()['position']['page'], 3)
        self.assertEqual(self.save(revision=1, position={**self.position,'page':2}).status_code, 200)
        self.assertEqual(self.client.get(self.path).json()['position']['page'], 2)

    def test_start_over_clears_position_and_rejects_older_pending_writes(self):
        self.save()
        cleared = self.save(revision=1, position=None)
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()['position'])
        self.assertEqual(cleared.json()['revision'], 2)
        self.assertEqual(self.save(revision=1).status_code, 409)
        self.assertIsNone(self.client.get('/api/reading').json()[0]['reading_progress'])

    def test_replacing_a_saved_copy_invalidates_progress_and_old_reader_requests(self):
        self.save()
        with main.database() as db:
            db.execute("UPDATE articles SET copy_saved_at='new-copy-version' WHERE id=?", (self.id,))
        latest = self.client.get(self.path).json()
        self.assertIsNone(latest['position'])
        self.assertEqual(self.save(revision=1).json()['detail']['reason'], 'copy_changed')
        old_content = self.client.get(f'/api/reading/{self.id}/copy/content', params={'version':self.version})
        self.assertEqual(old_content.status_code, 409)
        current = self.client.get(f'/api/reading/{self.id}/copy/content', params={'version':'new-copy-version'})
        self.assertEqual(current.status_code, 200)
        self.assertIsNone(self.client.get('/api/reading').json()[0]['reading_progress'])

    def test_coordinates_and_page_numbers_are_bounded(self):
        for changes in [{'page':0}, {'page':11}, {'page':True}, {'page_count':50001}, {'top':1e20}, {'left':'NaN'}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.save(position={**self.position, **changes}).status_code, 422)
        self.assertEqual(self.save(revision=-1).status_code, 422)
        self.assertEqual(self.client.get(self.path).json()['revision'], 0)

    def test_progress_is_pdf_only_and_deleted_with_the_article(self):
        with patch('main.fetch_title', return_value='Website'):
            website = self.client.post('/api/reading', json={'url':'https://example.com/article'}).json()
        self.assertEqual(self.client.get(f"/api/reading/{website['id']}/progress").status_code, 409)
        self.save()
        self.client.delete(f'/api/reading/{self.id}')
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.assertEqual(self.save().status_code, 404)


    def test_restoring_the_same_uploaded_pdf_keeps_its_reading_position(self):
        from saved_copies import resolve_copy_path
        self.save()
        with main.database() as db:
            row = db.execute('SELECT copy_path FROM articles WHERE id=?', (self.id,)).fetchone()
        resolve_copy_path(row['copy_path']).unlink()
        restored = self.client.post('/api/reading/upload', content=pdf_bytes(), headers={'Content-Type':'application/pdf','X-Filename':'restored.pdf'})
        self.assertEqual(restored.status_code,201,restored.text)
        self.assertEqual(restored.json()['reading_progress']['page'],3)
        self.assertNotEqual(restored.json()['copy_saved_at'],self.version)
        self.assertEqual(self.client.get(self.path).json()['position']['page'],3)
