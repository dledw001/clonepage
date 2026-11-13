# Hacker News reader

Open **Hacker News** in the main navigation, or use the homepage card's **Open reader** link. No API token or extra service is needed.

## Views

- **Front page** follows the official HN API's first 30 ranked item IDs. Jobs, deleted items, and dead stories are excluded without renumbering the HN rank badges.
- **Top stories** lists up to 100 stories submitted during the rolling past **24 hours**, **7 days**, or **30 days**, sorted by their current points, then comment count.
- **Most discussed** uses the same submission windows, sorted by current comment count, then points. This measures total comments on stories submitted in the window, not comments added during the window.
- Historical views use HN Search (Algolia), whose index can lag behind HN. HN rank badges indicate a story's front-page position at the last refresh; the numbered rows indicate order in the selected list. Historical rank movement and peak ranks are not collected.

Algolia's normal search order is primarily points-based, and its pagination is capped. The most-discussed collector narrows by comment-count thresholds until all candidates fit in a response. Large cutoff ties are filled by points. It does not simply re-sort the first page of popular stories and present it as a complete comment ranking.

## Reading and saving

Opening an article or its discussion marks it read. The circle button toggles read status without opening anything; **Hide read** filters the currently ranked list. Read state is stored in SQLite and shared across devices.

**Save** adds the original article to the existing reading list and queues the existing offline-copy/indexing worker. A story without an external URL saves its HN discussion page. Duplicate article URLs reuse the existing reading-list entry. Saved articles retain an **HN discussion** link in the reading list. HN's own discussion remains on HN; there is no built-in comment-thread viewer.

Saving a story does not mark it read. Deleting an article from the reading list makes it saveable again after the reader refreshes.

## Refresh and storage

- Visible HN components poll every three minutes and refresh when returning to the page. They stop polling when hidden and cancel outstanding requests on disposal.
- The backend shares a five-minute cache across devices. The Refresh button requests an earlier recheck, limited to once per 30 seconds per view. Upstream failures also have a 30-second retry cooldown.
- Rankings and story metadata are cached in SQLite (`hn_cache`, `hn_stories`). Read flags live separately in `hn_read_state`, so a refresh never overwrites them. The existing `articles` table gains a nullable `hn_story_id` column.
- Cached rankings survive app restarts. If an upstream refresh fails, prior results are explicitly marked stale. A first-load failure returns an error and a retry control rather than an empty successful list.
- Requests have a 30-second backend collection deadline and a 35-second browser deadline. Outbound HN requests have bounded concurrency; the browser talks only to Clonepage's API.
- These tables use the existing `/data/reading.sqlite3` database and its normal backup process. No Compose environment changes are required.

## API and verification

- `GET /api/hacker-news?view=front|top|discussed&window=24h|7d|30d&force=false`
- `PATCH /api/hacker-news/{story_id}/read` with `{"is_read":true}`
- `POST /api/hacker-news/{story_id}/save`

Tests:

```sh
python -m unittest discover -s backend/tests -p test_hacker_news.py -v
node --test tests/hacker-news.test.mjs
npm run check
npm run build
```

References: [Official HN API](https://github.com/HackerNews/API), [HN Search API](https://hn.algolia.com/api), [HN Search indexing configuration](https://github.com/algolia/hn-search#Indexing-Configuration).
