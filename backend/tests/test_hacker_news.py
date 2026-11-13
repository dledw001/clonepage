import copy
import json
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import hacker_news as hn
import worker
from extractor import ExtractedDocument

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc).timestamp()
REAL_ASYNC_CLIENT = httpx.AsyncClient


def hit(story_id, points=10, comments=5, age=3600, **changes):
    return {'objectID': str(story_id), 'title': f'Story {story_id}', 'url': f'https://example.com/story/{story_id}',
            'author': 'reader', 'points': points, 'num_comments': comments, 'created_at_i': int(NOW-age), **changes}


class HackerNewsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(directory) / 'reading.sqlite3'))
        self.hits = [hit(1, 500, 5), hit(2, 50, 900), hit(3, 300, 20, age=3*86400), hit(4, 600, 30, age=20*86400)]
        self.top_ids = [1, 2, 3, 4]
        self.items = {i: {'id': i, 'type': 'story', 'title': f'Front {i}', 'by': 'reader', 'score': i*10,
                          'descendants': i, 'time': int(NOW-60), 'url': f'https://example.com/story/{i}'} for i in self.top_ids}
        self.requests = []
        self.failure = None
        self.bad_payload = False
        transport = httpx.MockTransport(self.upstream)
        self.stack.enter_context(patch('main.httpx.AsyncClient', side_effect=lambda **kwargs: REAL_ASYNC_CLIENT(transport=transport, **kwargs)))
        self.stack.enter_context(patch('main.fetch_title', return_value='Detected title'))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.gateway = main.app.state.hacker_news
        self.now = NOW
        self.gateway.clock = lambda: self.now

    def upstream(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        if self.bad_payload:
            return httpx.Response(200, json={'unsupported': True})
        if request.url.path.endswith('/topstories.json'):
            return httpx.Response(200, json=self.top_ids)
        if '/item/' in request.url.path:
            story_id = int(request.url.path.rsplit('/', 1)[1].split('.')[0])
            return httpx.Response(200, json=self.items.get(story_id))
        if request.url.path == '/api/v1/search':
            rows = copy.deepcopy(self.hits)
            for expression in request.url.params['numericFilters'].split(','):
                field, operator, raw_value = re.fullmatch(r'(created_at_i|num_comments)(>=|<=|=)(\d+)', expression).groups()
                value = int(raw_value)
                rows = [row for row in rows if row[field] >= value] if operator == '>=' else [row for row in rows if row[field] <= value] if operator == '<=' else [row for row in rows if row[field] == value]
            rows.sort(key=lambda row: (-row['points'], -row['num_comments'], int(row['objectID'])))
            return httpx.Response(200, json={'hits': rows[:int(request.url.params['hitsPerPage'])], 'nbHits': len(rows)})
        raise AssertionError(f'Unexpected upstream: {request.url.host}{request.url.path}')

    def page(self, **params):
        response = self.client.get('/api/hacker-news', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return response.json()

    def test_front_page_keeps_official_order_and_rank_gaps(self):
        self.items[2]['type'] = 'job'
        self.items[3]['dead'] = True
        page = self.page()
        self.assertEqual([story['id'] for story in page['stories']], [1, 4])
        self.assertEqual([story['rank'] for story in page['stories']], [1, 4])
        self.assertFalse(page['stale'])
        self.assertTrue(all('Authorization' not in request.headers for request in self.requests))

    def test_top_points_and_all_three_submission_windows(self):
        for window, expected in [('24h', [1, 2]), ('7d', [1, 3, 2]), ('30d', [4, 1, 3, 2])]:
            with self.subTest(window=window):
                page = self.page(view='top', window=window)
                self.assertEqual([story['id'] for story in page['stories']], expected)
                self.assertEqual(page['stories'][0]['rank'], self.top_ids.index(expected[0])+1)

    def test_most_discussed_sorts_comments_instead_of_points(self):
        page = self.page(view='discussed', window='30d')
        self.assertEqual([story['id'] for story in page['stories']], [2, 4, 3, 1])

    def test_most_discussed_avoids_algolia_pagination_cap_and_handles_large_ties(self):
        self.hits = [hit(index+100, points=index, comments=10) for index in range(1200)] + [hit(9000, points=0, comments=11)]
        page = self.page(view='discussed', window='30d')
        self.assertEqual(len(page['stories']), 100)
        self.assertEqual(page['stories'][0]['id'], 9000)
        self.assertEqual(page['stories'][1]['id'], 1299)
        self.assertEqual(page['stories'][-1]['id'], 1201)
        self.assertTrue(any('num_comments=10' in request.url.params.get('numericFilters', '') for request in self.requests))

    def test_dense_discussions_are_narrowed_without_losing_top_commented_stories(self):
        self.hits = [hit(index+100, points=2000-index, comments=index) for index in range(1400)]
        page = self.page(view='discussed', window='7d')
        self.assertEqual([story['comments'] for story in page['stories']], list(range(1399,1299,-1)))
        self.assertLess(len(self.requests), 15)

    def test_read_state_syncs_independently_of_cached_rankings(self):
        self.page()
        before = len(self.requests)
        self.assertEqual(self.client.patch('/api/hacker-news/1/read', json={'is_read': True}).status_code, 200)
        self.assertTrue(self.page()['stories'][0]['is_read'])
        self.assertEqual(len(self.requests), before)
        self.gateway._ids = None
        self.now += 301
        self.assertTrue(self.page()['stories'][0]['is_read'])
        self.client.patch('/api/hacker-news/1/read', json={'is_read': False})
        self.assertFalse(self.page()['stories'][0]['is_read'])

    def test_force_refresh_is_throttled_and_normal_cache_is_shared(self):
        self.page(); before = len(self.requests)
        self.page(force=True)
        self.assertEqual(len(self.requests), before)
        self.now += 31
        self.page(force=True)
        self.assertGreater(len(self.requests), before)

    def test_persistent_cache_survives_gateway_restart_and_outages_are_marked_stale(self):
        self.page()
        self.gateway._ids = None
        self.failure = httpx.ConnectError('private-upstream-canary')
        self.now += 301
        page = self.page()
        self.assertTrue(page['stale'])
        self.assertEqual(len(page['stories']), 4)
        self.assertNotIn('private-upstream-canary', json.dumps(page))
        before = len(self.requests)
        self.page()
        self.assertEqual(len(self.requests), before, 'failed checks are throttled')
        # A fresh gateway also reads the persistent response rather than inventing an empty list.
        main.app.state.hacker_news = hn.HackerNewsGateway(self.gateway.client, clock=lambda: self.now)
        page = self.page()
        self.assertTrue(page['stale'])
        self.assertEqual(len(page['stories']), 4)

    def test_first_load_failure_is_503_and_retries_recover(self):
        self.failure = httpx.ReadTimeout('private-canary')
        response = self.client.get('/api/hacker-news')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-canary', response.text)
        self.failure = None
        self.now += 31
        self.assertEqual(len(self.page()['stories']), 4)

    def test_invalid_source_payload_is_not_cached_as_a_success(self):
        self.bad_payload = True
        self.assertEqual(self.client.get('/api/hacker-news').status_code, 503)
        with main.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM hn_cache').fetchone()[0], 0)

    def test_save_uses_existing_offline_pipeline_and_retains_discussion_link(self):
        self.page()
        response = self.client.post('/api/hacker-news/1/save')
        self.assertEqual(response.status_code, 200, response.text)
        article = response.json()
        self.assertEqual(article['hn_discussion_url'], 'https://news.ycombinator.com/item?id=1')
        self.assertEqual(article['copy_status'], 'pending')
        self.assertEqual(self.client.post('/api/hacker-news/1/save').json()['id'], article['id'])
        self.assertEqual(len(self.client.get('/api/reading').json()), 1)
        with patch('worker.extract_article_text', return_value=ExtractedDocument('Saved HN story content')):
            self.assertTrue(worker.process_next())
        saved = self.client.get('/api/reading').json()[0]
        self.assertTrue(saved['copy_available'])
        self.assertEqual(saved['hn_discussion_url'], article['hn_discussion_url'])
        self.assertTrue(self.page()['stories'][0]['is_saved'])
        self.client.delete('/api/reading/' + article['id'])
        self.assertFalse(self.page()['stories'][0]['is_saved'])

    def test_existing_reading_article_is_reused_and_self_posts_save_the_discussion(self):
        self.items[1]['url'] = None
        story = self.page()['stories'][0]
        self.assertEqual(story['url'], story['discussion_url'])
        original = self.client.post('/api/reading', json={'url': story['url'], 'title': story['title']}).json()
        self.assertEqual(self.client.post('/api/hacker-news/1/save').json()['id'], original['id'])

    def test_unsafe_urls_and_markup_are_not_rendered_as_links_or_html(self):
        self.items[1].update(url='javascript:alert(1)', title='<script>bad()</script><b>Good &amp; safe</b>')
        story = self.page()['stories'][0]
        self.assertEqual(story['url'], story['discussion_url'])
        self.assertEqual(story['title'], 'Good & safe')

    def test_invalid_filters_ids_and_unknown_story_writes_are_rejected(self):
        for params in [{'view':'other'}, {'window':'365d'}]:
            self.assertEqual(self.client.get('/api/hacker-news', params=params).status_code, 422)
        self.assertEqual(self.client.patch('/api/hacker-news/0/read', json={'is_read':True}).status_code, 422)
        self.assertEqual(self.client.patch('/api/hacker-news/999/read', json={'is_read':True}).status_code, 404)
        self.assertEqual(self.client.post('/api/hacker-news/999/save').status_code, 404)


if __name__ == '__main__':
    unittest.main()
