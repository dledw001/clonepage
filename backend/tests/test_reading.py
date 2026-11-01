import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import main
import worker
from extractor import ExtractedDocument


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, "DB_PATH", Path(directory) / "reading.sqlite3"))
        self.stack.enter_context(patch("main.fetch_title", return_value="Detected article title"))
        self.client = self.stack.enter_context(TestClient(main.app))

    def save(self, title="Example article", url="https://example.com/article"):
        response = self.client.post("/api/reading", json={"url": url, "title": title})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def article(self, article_id):
        return next(a for a in self.client.get("/api/reading").json() if a["id"] == article_id)

    def process_at(self, now, document=None, error=None):
        with patch("worker.datetime") as clock, patch("worker.extract_article_text") as extract:
            clock.now.return_value = now
            if error:
                extract.side_effect = error
            else:
                extract.return_value = document or ExtractedDocument("searchable recovered content")
            return worker.process_next()

    def test_extension_pdf_titles_save_and_manual_title_survives_indexing(self):
        article = self.save("My research title", "https://example.com/report.pdf")
        self.assertTrue(self.process_at(NOW, ExtractedDocument("research body", "PDF metadata title")))
        self.assertEqual(self.article(article["id"])["title"], "My research title")

    def test_generic_pdf_filename_is_replaced_with_detected_title(self):
        article = self.save("report name.pdf", "https://example.com/report%20name.pdf")
        self.process_at(NOW, ExtractedDocument("research body", "Detected PDF title"))
        self.assertEqual(self.article(article["id"])["title"], "Detected PDF title")

    def test_failure_is_visible_then_retries_only_after_delay(self):
        article = self.save()
        self.assertTrue(self.process_at(NOW, error=TimeoutError("temporary timeout")))
        failed = self.article(article["id"])
        self.assertEqual(failed["index_status"], "failed")
        self.assertEqual(failed["index_error"], "temporary timeout")
        self.assertEqual(failed["index_attempts"], 1)
        self.assertEqual(failed["index_retry_at"], (NOW + timedelta(seconds=60)).isoformat())
        self.assertFalse(self.process_at(NOW + timedelta(seconds=59)))
        self.assertTrue(self.process_at(NOW + timedelta(seconds=60)))
        ready = self.article(article["id"])
        self.assertEqual(ready["index_status"], "ready")
        self.assertIsNone(ready["index_error"])
        self.assertIsNone(ready["index_retry_at"])
        matches = self.client.get("/api/reading/search", params={"q": "recovered"}).json()
        self.assertEqual([a["id"] for a in matches], [article["id"]])
        self.assertIn("recovered", matches[0]["snippet"])

    def test_retries_are_bounded_and_manual_retry_recovers(self):
        article = self.save()
        for seconds in (0, 60, 180):
            self.assertTrue(self.process_at(NOW + timedelta(seconds=seconds), error=ValueError("unsupported document")))
        failed = self.article(article["id"])
        self.assertEqual(failed["index_attempts"], 3)
        self.assertIsNone(failed["index_retry_at"])
        self.assertFalse(self.process_at(NOW + timedelta(days=1)))
        retry = self.client.post(f"/api/reading/{article['id']}/retry")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["index_status"], "pending")
        self.assertEqual(retry.json()["index_attempts"], 0)
        self.assertIsNone(retry.json()["index_error"])
        self.assertTrue(self.process_at(NOW + timedelta(days=1)))
        self.assertEqual(self.article(article["id"])["index_status"], "ready")

    def test_existing_failed_articles_are_eligible_for_recovery(self):
        article = self.save()
        with main.database() as db:
            db.execute("UPDATE articles SET index_status='failed', index_error='old failure' WHERE id=?", (article["id"],))
        self.assertTrue(self.process_at(NOW))
        self.assertEqual(self.article(article["id"])["index_status"], "ready")

    def test_retry_rejects_missing_and_active_articles(self):
        self.assertEqual(self.client.post("/api/reading/missing/retry").status_code, 404)
        article = self.save()
        for status in ("pending", "processing", "ready"):
            with main.database() as db:
                db.execute("UPDATE articles SET index_status=? WHERE id=?", (status, article["id"]))
            self.assertEqual(self.client.post(f"/api/reading/{article['id']}/retry").status_code, 409)
            self.assertEqual(self.article(article["id"])["index_status"], status)

    def test_new_articles_are_not_blocked_by_a_delayed_retry(self):
        first = self.save()
        self.process_at(NOW, error=TimeoutError())
        second = self.save("New article", "https://example.com/new")
        self.assertTrue(self.process_at(NOW + timedelta(seconds=1)))
        self.assertEqual(self.article(first["id"])["index_status"], "failed")
        self.assertEqual(self.article(second["id"])["index_status"], "ready")

    def test_crud_and_search_filters_still_work(self):
        article = self.save()
        self.assertEqual(self.client.post("/api/reading", json={"url": article["url"]}).status_code, 409)
        self.process_at(NOW)
        changed = self.client.patch(f"/api/reading/{article['id']}", json={"title": "Renamed article", "is_read": True, "archived": True})
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(changed.json()["is_read"])
        self.assertIsNotNone(changed.json()["archived_at"])
        self.assertEqual(self.client.get("/api/reading/search", params={"q": "Renamed", "archived": False}).json(), [])
        self.assertEqual(len(self.client.get("/api/reading/search", params={"q": "Renamed", "archived": True}).json()), 1)
        self.assertEqual(self.client.patch(f"/api/reading/{article['id']}", json={"is_read": None}).status_code, 422)
        self.assertEqual(self.client.delete(f"/api/reading/{article['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/reading").json(), [])
        self.assertEqual(self.client.get("/api/reading/search", params={"q": "recovered"}).json(), [])


class MigrationTests(unittest.TestCase):
    def test_old_database_with_pdfs_migrates_without_losing_articles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE articles (id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL UNIQUE, is_read INTEGER NOT NULL DEFAULT 0, saved_at TEXT NOT NULL)")
                db.execute("INSERT INTO articles VALUES ('old', 'report.pdf', 'https://example.com/report.pdf', 0, '2026-01-01')")
            with patch.object(main, "DB_PATH", path):
                # Starting twice also checks that migrations are idempotent.
                for _ in range(2):
                    with TestClient(main.app) as client:
                        articles = client.get("/api/reading").json()
                        self.assertEqual(len(articles), 1)
                        self.assertEqual(articles[0]["id"], "old")
                        self.assertEqual(articles[0]["index_attempts"], 0)
                        self.assertIsNone(articles[0]["index_retry_at"])
                with main.database() as db:
                    self.assertEqual(db.execute("SELECT title_source FROM articles WHERE id='old'").fetchone()[0], "auto")


if __name__ == "__main__":
    unittest.main()
