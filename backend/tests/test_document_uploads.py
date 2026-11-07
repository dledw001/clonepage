import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import worker
import document_uploads as uploads
from saved_copies import copy_root, resolve_copy_path

TEXT = 'Periwinkle orchard research and practical notes for a personal document reading list. ' * 4


def pdf_bytes(text=TEXT, title='Local field guide', encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=500)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        contents = DecodedStreamObject()
        contents.set_data(('BT /F1 12 Tf 20 450 Td (' + text + ') Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(contents)
    if title:
        writer.add_metadata({'/Title': title})
    if encrypted:
        writer.encrypt('example-password')
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


class DocumentUploadTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(directory) / 'reading.sqlite3'))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.pdf = pdf_bytes()

    def upload(self, data=None, filename='field_notes.pdf', title='', headers=None):
        return self.client.post('/api/reading/upload', content=self.pdf if data is None else data,
                                headers={'Content-Type': 'application/pdf', 'X-Filename': quote(filename, safe=''),
                                         'X-Document-Title': quote(title, safe=''), **(headers or {})})

    def row(self, article_id):
        with main.database() as db:
            return dict(db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone())

    def article(self, article_id):
        return next(a for a in self.client.get('/api/reading').json() if a['id'] == article_id)

    def test_upload_preserves_original_and_uses_worker_for_full_text_and_metadata(self):
        with patch('main.fetch_title') as title, patch('worker.extract_article_text') as remote:
            response = self.upload()
            self.assertEqual(response.status_code, 201, response.text)
            item = response.json()
            self.assertEqual(item['source_kind'], 'upload')
            self.assertEqual(item['title'], 'field notes')
            self.assertEqual(item['url'], '/reading/' + item['id'])
            self.assertEqual(item['upload_filename'], 'field_notes.pdf')
            self.assertEqual(item['copy_status'], 'ready')
            self.assertEqual(item['index_status'], 'pending')
            self.assertNotIn('upload_sha256', item)
            self.assertNotIn('copy_path', item)
            download = self.client.get('/api/reading/' + item['id'] + '/copy/download')
            self.assertEqual(download.content, self.pdf)
            self.assertEqual(download.headers['content-type'], 'application/pdf')
            self.assertTrue(worker.process_next())
            title.assert_not_called()
            remote.assert_not_called()
        indexed = self.article(item['id'])
        self.assertEqual(indexed['index_status'], 'ready')
        self.assertEqual(indexed['title'], 'Local field guide')
        matches = self.client.get('/api/reading/search', params={'q': 'periwinkle orchard'}).json()
        self.assertEqual([a['id'] for a in matches], [item['id']])
        self.assertEqual(matches[0]['source_kind'], 'upload')
        info = self.client.get('/api/reading/' + item['id'] + '/copy').json()
        self.assertEqual(info['upload_filename'], 'field_notes.pdf')
        self.assertTrue(info['copy_available'])

    def test_manual_titles_survive_processing_and_reindexing_never_downloads_a_url(self):
        item = self.upload(title='My custom title').json()
        with patch('worker.extract_article_text') as remote:
            worker.process_next()
            self.assertEqual(self.article(item['id'])['title'], 'My custom title')
            with main.database() as db:
                db.execute("UPDATE articles SET index_status='failed' WHERE id=?", (item['id'],))
            self.assertEqual(self.client.post('/api/reading/' + item['id'] + '/retry').status_code, 200)
            worker.process_next()
            remote.assert_not_called()
        self.assertEqual(self.article(item['id'])['index_status'], 'ready')

    def test_scanned_and_encrypted_documents_remain_readable_without_retry_loops(self):
        for encrypted in [False, True]:
            with self.subTest(encrypted=encrypted):
                data = pdf_bytes(text='', encrypted=encrypted)
                item = self.upload(data=data, filename='scan.pdf').json()
                with patch('worker.extract_article_text') as remote:
                    worker.process_next()
                    remote.assert_not_called()
                current = self.article(item['id'])
                self.assertEqual(current['index_status'], 'failed')
                self.assertIsNone(current['index_retry_at'])
                self.assertEqual(current['index_attempts'], worker.MAX_INDEX_ATTEMPTS)
                self.assertEqual(current['copy_status'], 'ready')
                self.assertEqual(self.client.get('/api/reading/' + item['id'] + '/copy/download').content, data)

    def test_duplicate_detection_uses_file_bytes_and_includes_archived_documents(self):
        item = self.upload().json()
        self.client.patch('/api/reading/' + item['id'], json={'archived': True})
        duplicate = self.upload(filename='different_name.pdf', title='Different title')
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(len(self.client.get('/api/reading').json()), 1)
        self.assertEqual(len(list(copy_root().glob('*/*.pdf'))), 1)

    def test_concurrent_duplicate_uploads_create_only_one_record_and_original(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: self.upload(), range(2)))
        self.assertEqual(sorted(r.status_code for r in responses), [201, 409])
        self.assertEqual(len(self.client.get('/api/reading').json()), 1)
        self.assertEqual(len(list(copy_root().glob('*/*.pdf'))), 1)

    def test_missing_original_can_be_restored_without_losing_title_or_read_archive_state(self):
        item = self.upload(title='Keep my title').json()
        self.client.patch('/api/reading/' + item['id'], json={'is_read': True, 'archived': True})
        resolve_copy_path(self.row(item['id'])['copy_path']).unlink()
        self.assertFalse(self.client.get('/api/reading/' + item['id'] + '/copy').json()['copy_available'])
        restored = self.upload(filename='restored.pdf', title='Ignore this replacement title')
        self.assertEqual(restored.status_code, 201, restored.text)
        result = restored.json()
        self.assertEqual(result['id'], item['id'])
        self.assertEqual(result['title'], 'Keep my title')
        self.assertTrue(result['is_read'])
        self.assertTrue(result['archived_at'])
        self.assertEqual(self.client.get('/api/reading/' + item['id'] + '/copy/download').content, self.pdf)
        self.assertEqual(len(self.client.get('/api/reading').json()), 1)

    def test_uploaded_files_cannot_be_refreshed_from_a_website(self):
        item = self.upload().json()
        response = self.client.post('/api/reading/' + item['id'] + '/copy/refresh')
        self.assertEqual(response.status_code, 409)
        self.assertIn('no website', response.json()['detail'])
        self.assertEqual(self.article(item['id'])['copy_status'], 'ready')

    def test_delete_removes_original_and_search_index(self):
        item = self.upload().json()
        worker.process_next()
        path = resolve_copy_path(self.row(item['id'])['copy_path'])
        self.assertEqual(self.client.delete('/api/reading/' + item['id']).status_code, 204)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get('/api/reading/search', params={'q': 'periwinkle'}).json(), [])
        self.assertEqual(self.upload().status_code, 201)

    def test_rejects_invalid_empty_and_non_pdf_files_without_creating_records(self):
        for data, filename, content_type in [(b'not a PDF', 'fake.pdf', 'application/pdf'),
                (b'%PDF-1.4 broken', 'broken.pdf', 'application/pdf'), (b'', 'empty.pdf', 'application/pdf'),
                (self.pdf, 'file.txt', 'application/pdf'), (self.pdf, 'file.pdf', 'text/plain')]:
            with self.subTest(filename=filename, content_type=content_type):
                response = self.upload(data=data, filename=filename, headers={'Content-Type': content_type})
                self.assertIn(response.status_code, [415, 422], response.text)
        self.assertEqual(self.client.get('/api/reading').json(), [])
        self.assertEqual(list(copy_root().glob('*/*.pdf')), [])

    def test_size_limit_checks_both_declared_and_streamed_bytes(self):
        with patch.object(uploads, 'MAX_PDF_BYTES', len(self.pdf) - 1):
            self.assertEqual(self.upload().status_code, 413)
            response = self.upload(data=iter([self.pdf[:32], self.pdf[32:]]))
            self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.client.get('/api/reading').json(), [])

    def test_filenames_never_control_storage_paths_and_unicode_metadata_is_preserved(self):
        item = self.upload(filename='../outside/résumé.pdf', title='A résumé').json()
        self.assertEqual(item['upload_filename'], 'résumé.pdf')
        self.assertEqual(item['title'], 'A résumé')
        path = resolve_copy_path(self.row(item['id'])['copy_path'])
        self.assertEqual(path.parent.parent, copy_root())
        self.assertNotIn('résumé', path.name)
        self.assertEqual(self.upload(filename='test.pdf', title='x' * 201).status_code, 422)

    def test_storage_failures_do_not_create_an_article(self):
        with patch.object(uploads, 'store_copy', side_effect=OSError('Disk full')):
            response = self.upload()
        self.assertEqual(response.status_code, 507)
        self.assertEqual(self.client.get('/api/reading').json(), [])
