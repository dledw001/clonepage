import base64
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import worker
import extractor
import saved_copies as copies
from extractor import ExtractedDocument

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
TEXT = 'An offline article with searchable information and useful details. ' * 8
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j/1sAAAAASUVORK5CYII=')


def document(text=TEXT, html=None):
    return ExtractedDocument(text=text, html=html or f'<html><body><h2>Useful details</h2><p>{text}</p></body></html>', source_url='https://example.com/final/')


class SavedCopyTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(directory) / 'reading.sqlite3'))
        self.stack.enter_context(patch('main.fetch_title', return_value='Detected title'))
        self.client = self.stack.enter_context(TestClient(main.app))

    def add(self, url='https://example.com/article', title='My saved article'):
        response = self.client.post('/api/reading', json={'url': url, 'title': title})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def article(self, article_id):
        return next(item for item in self.client.get('/api/reading').json() if item['id'] == article_id)

    def row(self, article_id):
        with main.database() as db:
            return dict(db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone())

    def process(self, doc=None, error=None, at=NOW):
        with patch('worker.datetime') as clock, patch('worker.extract_article_text') as extract:
            clock.now.return_value = at
            if error:
                extract.side_effect = error
            else:
                extract.return_value = doc or document()
            return worker.process_next()

    def test_automatic_html_copy_is_downloadable_and_searchable(self):
        article = self.add(title='My <special> article')
        self.assertEqual(article['copy_status'], 'pending')
        self.assertTrue(self.process())
        current = self.article(article['id'])
        self.assertEqual(current['copy_status'], 'ready')
        self.assertTrue(current['copy_available'])
        inline = self.client.get(f"/api/reading/{article['id']}/copy/content", params={'theme': 'dark'})
        self.assertEqual(inline.status_code, 200, inline.text)
        self.assertIn('data-theme="dark"', inline.text)
        self.assertIn('My &lt;special&gt; article', inline.text)
        self.assertIn('Useful details', inline.text)
        self.assertEqual(inline.headers['x-content-type-options'], 'nosniff')
        self.assertIn("default-src 'none'", inline.headers['content-security-policy'])
        download = self.client.get(f"/api/reading/{article['id']}/copy/download")
        self.assertIn('attachment', download.headers['content-disposition'])
        self.assertIn('<style>', download.text)
        self.assertNotIn('data-theme="dark"', download.text)
        matches = self.client.get('/api/reading/search', params={'q': 'searchable'}).json()
        self.assertTrue(matches[0]['copy_available'])

    def test_first_successful_copy_is_not_silently_recaptured(self):
        article = self.add()
        self.process()
        original = self.row(article['id'])
        with patch('worker.extract_article_text', side_effect=AssertionError('must not fetch')):
            self.assertFalse(worker.process_next())
            with main.database() as db:
                db.execute("UPDATE articles SET index_status='pending' WHERE id=?", (article['id'],))
            self.assertTrue(worker.process_next())
        current = self.row(article['id'])
        self.assertEqual(current['copy_path'], original['copy_path'])
        self.assertEqual(current['copy_saved_at'], original['copy_saved_at'])

    def test_failed_refresh_keeps_file_capture_date_and_search_text(self):
        article = self.add()
        self.process()
        original = self.row(article['id'])
        original_bytes = copies.resolve_copy_path(original['copy_path']).read_bytes()
        self.client.post(f"/api/reading/{article['id']}/copy/refresh")
        missing = httpx.HTTPStatusError('Internal HTTP diagnostics', request=httpx.Request('GET', 'https://example.com/article'), response=httpx.Response(404))
        self.process(error=missing, at=NOW + timedelta(days=1))
        current = self.row(article['id'])
        self.assertEqual(current['copy_status'], 'failed')
        self.assertEqual(current['copy_error'], 'The original page is no longer available (HTTP 404).')
        self.assertEqual(current['index_status'], 'ready')
        self.assertEqual(current['content'], original['content'])
        self.assertEqual(current['copy_saved_at'], original['copy_saved_at'])
        self.assertEqual(self.client.get(f"/api/reading/{article['id']}/copy/download").content, original_bytes)

    def test_explicit_refresh_replaces_only_after_success_and_preserves_article_state(self):
        article = self.add()
        self.process()
        original = self.row(article['id'])
        self.client.patch(f"/api/reading/{article['id']}", json={'is_read': True, 'archived': True})
        self.client.post(f"/api/reading/{article['id']}/copy/refresh")
        self.process(document('New searchable version. ' * 10), at=NOW + timedelta(days=1))
        current = self.row(article['id'])
        self.assertNotEqual(current['copy_path'], original['copy_path'])
        self.assertFalse(copies.resolve_copy_path(original['copy_path']).exists())
        self.assertEqual(current['copy_status'], 'ready')
        self.assertEqual(current['copy_saved_at'], (NOW + timedelta(days=1)).isoformat())
        self.assertTrue(current['is_read'])
        self.assertIsNotNone(current['archived_at'])

    def test_write_failure_does_not_prevent_new_article_search(self):
        article = self.add()
        with patch('worker.store_copy', side_effect=OSError('Disk full')):
            self.process()
        current = self.article(article['id'])
        self.assertEqual(current['index_status'], 'ready')
        self.assertEqual(current['copy_status'], 'failed')
        self.assertFalse(current['copy_available'])
        self.assertIn('NAS storage', current['copy_error'])

    def test_write_failure_during_refresh_retains_old_file_and_index(self):
        article = self.add()
        self.process()
        original = self.row(article['id'])
        self.client.post(f"/api/reading/{article['id']}/copy/refresh")
        with patch('worker.store_copy', side_effect=OSError('Read-only filesystem')):
            self.process(document('Changed content. ' * 10))
        current = self.row(article['id'])
        self.assertEqual(current['copy_path'], original['copy_path'])
        self.assertEqual(current['content'], original['content'])
        self.assertTrue(copies.resolve_copy_path(original['copy_path']).exists())

    def test_legacy_text_is_recovered_if_original_is_gone(self):
        article = self.add()
        with main.database() as db:
            db.execute("UPDATE articles SET content=?,index_status='ready',indexed_at=? WHERE id=?", (TEXT, NOW.isoformat(), article['id']))
        self.process(error=ValueError('Original page is gone'))
        current = self.article(article['id'])
        self.assertEqual(current['copy_status'], 'failed')
        self.assertTrue(current['copy_available'])
        self.assertTrue(current['copy_is_legacy'])
        self.assertIn('Recovered from indexed text', current['copy_warning'])
        self.assertIn(TEXT.strip(), self.client.get(f"/api/reading/{article['id']}/copy/download").text)

    def test_copy_retries_are_bounded_and_manual_retry_recovers(self):
        article = self.add()
        for seconds in (0, 60, 180):
            self.assertTrue(self.process(error=TimeoutError('offline'), at=NOW + timedelta(seconds=seconds)))
        self.assertFalse(self.process(at=NOW + timedelta(days=1)))
        current = self.article(article['id'])
        self.assertEqual(current['copy_attempts'], 3)
        self.assertIsNone(current['copy_retry_at'])
        self.client.post(f"/api/reading/{article['id']}/copy/refresh")
        self.assertTrue(self.process(at=NOW + timedelta(days=1)))
        self.assertEqual(self.article(article['id'])['copy_status'], 'ready')

    def test_pdf_original_survives_without_searchable_text(self):
        article = self.add(url='https://example.com/scanned.pdf')
        pdf = b'%PDF-1.4 original bytes retained'
        self.process(ExtractedDocument(text='', pdf=pdf, text_error='Scanned PDF needs OCR'))
        current = self.article(article['id'])
        self.assertEqual(current['index_status'], 'failed')
        self.assertEqual(current['copy_status'], 'ready')
        response = self.client.get(f"/api/reading/{article['id']}/copy/download")
        self.assertEqual(response.content, pdf)
        self.assertEqual(response.headers['content-type'], 'application/pdf')
        self.assertFalse(worker.process_next())

    def test_archive_preserves_copy_and_delete_removes_it(self):
        article = self.add()
        self.process()
        row = self.row(article['id'])
        path = copies.resolve_copy_path(row['copy_path'])
        self.client.patch(f"/api/reading/{article['id']}", json={'archived': True})
        self.assertTrue(path.exists())
        self.assertEqual(self.client.get(f"/api/reading/{article['id']}/copy/content").status_code, 200)
        self.client.delete(f"/api/reading/{article['id']}")
        self.assertFalse(path.parent.exists())
        self.assertEqual(self.client.get(f"/api/reading/{article['id']}/copy").status_code, 404)

    def test_delete_during_capture_cannot_resurrect_article_or_leave_file(self):
        article = self.add()
        def remove_then_return(url):
            self.client.delete(f"/api/reading/{article['id']}")
            return document()
        with patch('worker.extract_article_text', side_effect=remove_then_return):
            self.assertTrue(worker.process_next())
        self.assertFalse((copies.copy_root() / article['id']).exists())
        self.assertEqual(self.client.get('/api/reading').json(), [])

    def test_stale_job_cannot_publish_over_a_newer_generation(self):
        article = self.add()
        def supersede(url):
            with main.database() as db:
                db.execute("UPDATE articles SET article_job='newer',copy_job='newer' WHERE id=?", (article['id'],))
            return document()
        with patch('worker.extract_article_text', side_effect=supersede):
            self.assertTrue(worker.process_next())
        self.assertFalse((copies.copy_root() / article['id']).exists())
        self.assertFalse(self.article(article['id'])['copy_available'])

    def test_copy_refresh_requested_during_reindexing_remains_queued(self):
        article = self.add()
        self.process()
        original = self.row(article['id'])
        with main.database() as db:
            db.execute("UPDATE articles SET index_status='pending' WHERE id=?", (article['id'],))
        def queue_refresh(row):
            self.client.post(f"/api/reading/{article['id']}/copy/refresh")
            return ExtractedDocument(TEXT)
        with patch('worker.text_from_saved_copy', side_effect=queue_refresh):
            self.assertTrue(worker.process_next())
        current = self.row(article['id'])
        self.assertEqual(current['index_status'], 'ready')
        self.assertEqual(current['copy_status'], 'pending')
        self.assertEqual(current['copy_path'], original['copy_path'])
        self.assertTrue(self.process(at=NOW + timedelta(days=1)))
        self.assertNotEqual(self.row(article['id'])['copy_path'], original['copy_path'])

    def test_missing_files_have_clear_status_and_unsafe_paths_are_rejected(self):
        article = self.add()
        self.process()
        row = self.row(article['id'])
        copies.resolve_copy_path(row['copy_path']).unlink()
        self.assertFalse(self.client.get(f"/api/reading/{article['id']}/copy").json()['copy_available'])
        self.assertEqual(self.client.get(f"/api/reading/{article['id']}/copy/content").status_code, 404)
        for relative in ('../outside.html', '/etc/passwd', 'id/../../outside.html'):
            with self.assertRaises(ValueError): copies.resolve_copy_path(relative)
        self.assertEqual(self.client.post('/api/reading/missing/copy/refresh').status_code, 404)


class SavedHtmlTests(unittest.TestCase):
    def test_html_is_sanitized_and_images_are_embedded_once(self):
        source = '<h2 onclick="bad()">Heading</h2><p style="background:url(https://bad.example)">Readable text.</p><script>bad()</script><iframe src="https://bad.example"></iframe><a href="javascript:bad()">Bad link</a><a href="../other">Good link</a><img src="image.png" onerror="bad()"><img src="image.png">'
        body = copies.clean_article_html(source, 'https://example.com/final/')
        with patch('saved_copies.fetch_image', return_value=(PNG, 'image/png')) as fetch:
            self.assertIsNone(copies.embed_images(body))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.args[1], 'https://example.com/final/image.png')
        text = str(body)
        self.assertNotIn('bad()', text)
        self.assertNotIn('iframe', text)
        self.assertNotIn('style=', text)
        self.assertEqual(len(body.find_all('img')), 2)
        self.assertTrue(all(tag['src'].startswith('data:image/png;base64,') for tag in body.find_all('img')))
        self.assertEqual(body.find_all('a')[1]['href'], 'https://example.com/other')

    def test_missing_images_have_placeholders_and_no_remote_dependencies(self):
        body = copies.clean_article_html('<p>Text</p><img src="/bad.jpg" alt="A chart">', 'https://example.com/')
        with patch('saved_copies.fetch_image', side_effect=httpx.ConnectError('offline')):
            warning = copies.embed_images(body)
        self.assertIn('1 image', warning)
        self.assertFalse(body.find_all('img'))
        self.assertIn('A chart', body.get_text())

    def test_existing_inline_raster_images_are_kept_without_network(self):
        source = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
        body = copies.clean_article_html(f'<img src="{source}">', 'https://example.com/')
        with patch('saved_copies.fetch_image', side_effect=AssertionError('must not fetch')):
            self.assertIsNone(copies.embed_images(body))
        self.assertEqual(body.img['src'], source)

    def test_image_redirects_validate_destinations_and_size_is_bounded(self):
        requests = []
        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
        with httpx.Client(transport=httpx.MockTransport(redirect)) as client, patch('saved_copies.is_public_url', side_effect=lambda url: '127.0.0.1' not in url):
            with self.assertRaises(ValueError): copies.fetch_image(client, 'https://example.com/img', copies.time.monotonic())
        self.assertEqual(len(requests), 1)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=PNG))) as client, patch('saved_copies.is_public_url', return_value=True), patch('saved_copies.MAX_IMAGE_BYTES', 2):
            with self.assertRaises(ValueError): copies.fetch_image(client, 'https://example.com/img', copies.time.monotonic())

    def test_extractor_preserves_a_valid_image_only_pdf(self):
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        stream = BytesIO(); writer.write(stream); pdf = stream.getvalue()
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=pdf, headers={'content-type': 'application/pdf'}))
        with patch('extractor.httpx.Client', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)), patch('extractor.is_public_url', return_value=True):
            doc = extractor.extract_article_text('https://example.com/scanned.pdf')
        self.assertEqual(doc.pdf, pdf)
        self.assertEqual(doc.text, '')
        self.assertIn('insufficient extractable text', doc.text_error)

    def test_pdf_signature_uses_pdf_limit_even_with_generic_url_and_wrong_mime(self):
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        writer.add_metadata({'/Padding': 'x' * extractor.MAX_HTML_BYTES})
        stream = BytesIO(); writer.write(stream); pdf = stream.getvalue()
        self.assertGreater(len(pdf), extractor.MAX_HTML_BYTES)
        real_client = httpx.Client
        def download(request):
            if 'application/pdf' not in request.headers.get('accept', ''):
                return httpx.Response(406)
            return httpx.Response(200, content=pdf, headers={'content-type': 'application/octet-stream', 'content-length': str(len(pdf))})
        transport = httpx.MockTransport(download)
        with patch('extractor.httpx.Client', side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)), patch('extractor.is_public_url', return_value=True):
            doc = extractor.extract_article_text('https://example.com/download?id=1')
        self.assertEqual(doc.pdf, pdf)


if __name__ == '__main__': unittest.main()
