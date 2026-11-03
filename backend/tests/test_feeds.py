import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import feeds
import worker

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
FETCH = feeds.fetch_document


def rss(items=None, title="Example News"):
    if items is None:
        items = [("one", "First story", "https://example.com/one", "<p>Hello <b>world</b></p>")]
    body = ''.join(f'<item><guid isPermaLink="false">{escape(guid)}</guid><title>{escape(headline)}</title>'
                   f'<link>{escape(url)}</link><description>{escape(summary)}</description></item>' for guid, headline, url, summary in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{escape(title)}</title><link>https://example.com/</link><description>News</description>{body}</channel></rss>'.encode()


def document(body=None, url="https://example.com/feed/", status=200, headers=None):
    return feeds.Document(url, rss() if body is None else body, status, headers or {"content-type": "application/rss+xml", "etag": '"v1"'})


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, "DB_PATH", Path(directory) / "reading.sqlite3"))
        self.clock = self.stack.enter_context(patch("feeds.utcnow", return_value=NOW))
        self.fetch = self.stack.enter_context(patch("feeds.fetch_document", return_value=document()))
        self.client = self.stack.enter_context(TestClient(main.app))

    def subscribe(self, body=None, url="https://example.com/feed/"):
        self.fetch.return_value = document(body, url)
        response = self.client.post("/api/feeds/subscriptions", json={"url": url})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def page(self, **params):
        response = self.client.get("/api/feeds", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def poll(self, feed_id, value=None):
        self.fetch.return_value = value or document()
        self.client.post("/api/feeds/refresh", json={"feed_id": feed_id})
        self.assertTrue(feeds.process_due_feed())

    def test_subscribe_plain_text_and_duplicate_subscription(self):
        self.subscribe()
        page = self.page()
        self.assertEqual(page["unread"], 1)
        self.assertEqual(page["entries"][0]["summary"], "Hello world")
        self.assertIsNone(page["entries"][0]["published_at"])
        self.assertEqual(self.client.post("/api/feeds/subscriptions", json={"url": "https://example.com/feed/"}).status_code, 409)

    def test_repeated_polls_updates_and_changed_guid_preserve_read_state(self):
        feed_id = self.subscribe()
        entry = self.page()["entries"][0]
        self.client.patch(f"/api/feeds/entries/{entry['id']}", json={"is_read": True})
        self.poll(feed_id, document(rss([("changed-guid", "Updated story", entry["url"], "New summary")])) )
        page = self.page(unread=False)
        self.assertEqual(len(page["entries"]), 1)
        self.assertEqual(page["entries"][0]["id"], entry["id"])
        self.assertTrue(page["entries"][0]["is_read"])
        self.assertEqual(page["entries"][0]["title"], "Updated story")
        self.assertEqual(self.page()["total"], 0)

    def test_stable_guid_with_changed_url_does_not_duplicate(self):
        feed_id = self.subscribe()
        self.poll(feed_id, document(rss([("one", "Moved", "https://example.com/moved", "")])) )
        self.assertEqual(self.page()["total"], 1)
        self.assertTrue(self.page()["entries"][0]["url"].endswith("/moved"))

    def test_atom_dates_relative_links_and_html_are_normalized(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom News</title><link href="https://example.com/"/>
        <entry><id>tag:one</id><title>Atom title</title><link href="/atom-story"/><updated>2025-12-31T18:30:00-05:00</updated>
        <summary type="html">&lt;script&gt;bad()&lt;/script&gt;&lt;p&gt;Useful text&lt;/p&gt;</summary></entry></feed>'''
        self.subscribe(atom)
        entry = self.page()["entries"][0]
        self.assertEqual(entry["url"], "https://example.com/atom-story")
        self.assertEqual(entry["summary"], "Useful text")
        self.assertEqual(entry["published_at"], "2025-12-31T23:30:00+00:00")

    def test_missing_and_unsafe_article_links_are_not_ingested(self):
        self.subscribe(rss([("a", "Bad", "javascript:alert(1)", ""), ("b", "Missing", "", "")]))
        self.assertEqual(self.page()["total"], 0)
        self.assertIsNone(feeds.http_url(None, "https://example.com/feed/"))

    def test_discovery_returns_feed_preview_or_advertised_choices(self):
        result = self.client.post("/api/feeds/discover", json={"url": "https://example.com/feed/"}).json()
        self.assertEqual(result["preview"]["entry_count"], 1)
        html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed/" title="News"><link rel="alternate" type="application/atom+xml" href="/atom" title="Atom"></head></html>'
        self.fetch.return_value = document(html, "https://example.com/", headers={"content-type": "text/html"})
        result = self.client.post("/api/feeds/discover", json={"url": "https://example.com/"}).json()
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["url"], "https://example.com/feed/")
        self.assertIsNone(result["preview"])

    def test_unreadable_pages_and_malformed_feeds_have_clear_errors(self):
        for body in (b'<html><title>No feed</title></html>', b'not a feed', b'<rss><broken>'):
            self.fetch.return_value = document(body)
            self.assertEqual(self.client.post("/api/feeds/discover", json={"url": "https://example.com/"}).status_code, 400)
            self.assertEqual(self.client.post("/api/feeds/subscriptions", json={"url": "https://example.com/"}).status_code, 400)
        self.assertEqual(self.page()["subscriptions"], [])

    def test_wordpress_discovery_validates_standard_feed_when_tags_are_missing(self):
        self.fetch.side_effect = [document(b'<html><script src="/wp-content/theme.js"></script></html>', 'https://example.com/'), document()]
        response = self.client.post("/api/feeds/discover", json={"url": "https://example.com/"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preview"]["url"], 'https://example.com/feed/')
        self.assertEqual(self.fetch.call_args.args, ('https://example.com/feed/',))

    def test_discovery_resolves_html_base_and_rejects_non_http_candidates(self):
        html = b'<html><base href="https://example.com/news/"><link rel="alternate" type="application/rss+xml" href="feed.xml"><link rel="alternate" type="application/rss+xml" href="javascript:alert(1)"></html>'
        self.fetch.return_value = document(html, 'https://example.com/')
        result = self.client.post('/api/feeds/discover', json={'url': 'https://example.com/'}).json()
        self.assertEqual(result['candidates'], [{'url': 'https://example.com/news/feed.xml', 'title': 'https://example.com/news/feed.xml'}])

    def test_conditional_304_retains_entries_and_advances_success(self):
        feed_id = self.subscribe()
        entry = self.page()["entries"][0]
        self.clock.return_value = NOW + timedelta(minutes=16)
        self.fetch.return_value = document(b"", status=304, headers={})
        self.assertTrue(feeds.process_due_feed())
        self.fetch.assert_called_with("https://example.com/feed/", '"v1"', None)
        self.assertEqual(self.page()["entries"][0]["id"], entry["id"])
        self.assertEqual(self.page()["subscriptions"][0]["last_success_at"], self.clock.return_value.isoformat())
        self.assertFalse(feeds.process_due_feed())

    def test_failure_preserves_stories_and_retries_with_backoff(self):
        feed_id = self.subscribe()
        self.client.post("/api/feeds/refresh", json={"feed_id": feed_id})
        self.fetch.side_effect = httpx.ConnectError("offline")
        self.assertTrue(feeds.process_due_feed())
        self.assertEqual(self.page()["total"], 1)
        self.assertIn("could not be reached", self.page()["subscriptions"][0]["error"])
        self.assertFalse(feeds.process_due_feed())
        self.clock.return_value = NOW + timedelta(minutes=15)
        self.assertTrue(feeds.process_due_feed())
        with main.database() as db:
            retry = db.execute("SELECT next_check_at FROM feed_subscriptions").fetchone()[0]
        self.assertEqual(retry, (NOW + timedelta(minutes=45)).isoformat())
        self.fetch.side_effect = None
        self.poll(feed_id)
        self.assertIsNone(self.page()["subscriptions"][0]["error"])

    def test_mark_all_uses_snapshot_watermark_and_current_filter(self):
        first = self.subscribe()
        second = self.subscribe(rss([("two", "Other feed", "https://other.example/story", "")]), "https://other.example/feed/")
        page = self.page(feed_id=first)
        self.poll(first, document(rss([("one", "First story", "https://example.com/one", ""), ("new", "Arrived later", "https://example.com/new", "")])) )
        result = self.client.post("/api/feeds/read", json={"through_seq": page["through_seq"], "feed_id": first})
        self.assertEqual(result.json()["updated"], 1)
        unread = self.page()
        self.assertEqual({entry["title"] for entry in unread["entries"]}, {"Arrived later", "Other feed"})
        self.assertEqual(self.page(feed_id=second)["total"], 1)

    def test_search_is_literal_and_mark_all_respects_search(self):
        self.subscribe(rss([("one", "100% Python", "https://example.com/one", "body"), ("two", "SQL", "https://example.com/two", "summaryword")]))
        self.assertEqual(self.page(q="% ")["total"], 1)
        self.assertEqual(self.page(q="summaryword")["total"], 1)
        page = self.page(q="Python")
        self.client.post("/api/feeds/read", json={"through_seq": page["through_seq"], "q": "Python"})
        self.assertEqual(self.page()["entries"][0]["title"], "SQL")

    def test_keyset_pagination_has_no_duplicates_when_new_entries_arrive(self):
        feed_id = self.subscribe(rss([(str(i), str(i), f"https://example.com/{i}", "") for i in range(6)]))
        first = self.page(limit=2)
        self.poll(feed_id, document(rss([("new", "new", "https://example.com/new", "")])) )
        second = self.page(limit=2, cursor=first["next_cursor"])
        third = self.page(limit=2, cursor=second["next_cursor"])
        ids = [entry["id"] for page in (first, second, third) for entry in page["entries"]]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertIsNone(third["next_cursor"])
        self.assertEqual(self.client.get("/api/feeds", params={"cursor": "bad"}).status_code, 400)

    def test_retention_keeps_tombstones_and_saved_articles_independent(self):
        feed_id = self.subscribe()
        entry = self.page()["entries"][0]
        saved = self.client.post(f"/api/feeds/entries/{entry['id']}/save")
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(self.page()["entries"][0]["is_saved"])
        self.clock.return_value = NOW + timedelta(days=91)
        self.assertEqual(self.page()["total"], 0)
        self.poll(feed_id)
        self.assertEqual(self.page()["total"], 0)
        self.assertEqual(len(self.client.get("/api/reading").json()), 1)
        self.assertEqual(self.client.delete(f"/api/feeds/subscriptions/{feed_id}").status_code, 204)
        with main.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM feed_seen").fetchone()[0], 0)
        self.assertEqual(len(self.client.get("/api/reading").json()), 1)

    def test_save_is_idempotent_does_not_change_feed_read_status_or_manual_article_title(self):
        self.subscribe()
        entry = self.page()["entries"][0]
        article = self.client.post("/api/reading", json={"url": entry["url"], "title": "My title"}).json()
        for _ in range(2):
            saved = self.client.post(f"/api/feeds/entries/{entry['id']}/save")
            self.assertEqual(saved.json()["id"], article["id"])
            self.assertEqual(saved.json()["title"], "My title")
        self.assertFalse(self.page()["entries"][0]["is_read"])
        self.assertEqual(len(self.client.get("/api/reading").json()), 1)

    def test_saved_detection_uses_the_same_url_normalization_as_the_reading_list(self):
        self.subscribe(rss([('one', 'Unicode link', 'https://example.com/caf\u00e9 story?q=a%20b', '')]))
        entry = self.page()['entries'][0]
        self.assertIn('caf%C3%A9%20story', entry['url'])
        self.assertEqual(self.client.post(f"/api/feeds/entries/{entry['id']}/save").status_code, 200)
        self.assertTrue(self.page()['entries'][0]['is_saved'])

    def test_deleted_subscription_is_not_resurrected_by_inflight_poll(self):
        feed_id = self.subscribe()
        def fetch_and_delete(*args):
            self.client.delete(f"/api/feeds/subscriptions/{feed_id}")
            return document()
        self.fetch.side_effect = fetch_and_delete
        self.client.post("/api/feeds/refresh", json={"feed_id": feed_id})
        self.assertTrue(feeds.process_due_feed())
        self.assertEqual(self.page()["subscriptions"], [])

    def test_expired_worker_lease_recovers_after_restart(self):
        feed_id = self.subscribe()
        with main.database() as db:
            db.execute("UPDATE feed_subscriptions SET lease_token='interrupted',lease_until=?,next_check_at=?", ((NOW + timedelta(minutes=2)).isoformat(), NOW.isoformat()))
        self.assertFalse(feeds.process_due_feed())
        self.clock.return_value = NOW + timedelta(minutes=3)
        self.assertTrue(feeds.process_due_feed())
        self.assertFalse(self.page()["subscriptions"][0]["refresh_pending"])

    def test_worker_services_both_queues_even_if_one_raises(self):
        with patch("worker.process_due_feed", side_effect=ValueError("bad feed")), patch("worker.process_next", return_value=True) as index:
            self.assertTrue(worker.run_cycle())
            index.assert_called_once()

    def test_missing_entries_subscriptions_and_invalid_inputs(self):
        self.assertEqual(self.client.patch("/api/feeds/entries/missing", json={"is_read": True}).status_code, 404)
        self.assertEqual(self.client.post("/api/feeds/entries/missing/save").status_code, 404)
        self.assertEqual(self.client.delete("/api/feeds/subscriptions/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/feeds/refresh", json={"feed_id": "missing"}).status_code, 404)
        self.assertEqual(self.client.post("/api/feeds/discover", json={"url": "javascript:alert(1)"}).status_code, 422)


class FeedNetworkTests(unittest.TestCase):
    def test_redirects_validate_each_destination(self):
        requests = []
        def upstream(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        client = httpx.Client(transport=httpx.MockTransport(upstream))
        with patch("feeds.httpx.Client", return_value=client), patch("feeds.is_public_url", side_effect=lambda url: '127.0.0.1' not in url):
            with self.assertRaisesRegex(ValueError, "public"):
                FETCH("https://example.com/feed/")
        self.assertEqual(len(requests), 1)

    def test_body_limits_and_conditional_headers(self):
        requests = []
        def upstream(request):
            requests.append(request)
            return httpx.Response(200, content=b'x' * 100)
        real_client = httpx.Client
        with patch("feeds.httpx.Client", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)), patch("feeds.is_public_url", return_value=True), patch("feeds.MAX_BYTES", 50):
            with self.assertRaisesRegex(ValueError, "too large"):
                FETCH("https://example.com/feed/", '"etag"', "yesterday")
        self.assertEqual(requests[0].headers["if-none-match"], '"etag"')
        self.assertEqual(requests[0].headers["if-modified-since"], "yesterday")


if __name__ == "__main__":
    unittest.main()
