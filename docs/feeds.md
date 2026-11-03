# Feed reader

The Home page includes a Feeds widget beneath the reading list. Add an RSS/Atom
URL directly or paste a public website URL to discover its advertised feeds.
Preview the feed and select Subscribe. When several feeds are advertised, choose
which one to preview. Pages without feed links require a direct feed URL; this
version does not scrape website headlines or crawl articles automatically.
Recognized WordPress pages without discovery tags also get a validated check
of their standard `/feed/` address.

## Reading

- Unread/All, a subscription filter, and title/summary search select the stories.
- The widget initially shows three entries. Show more expands the first page,
  then loads older pages using a stable date/sequence cursor.
- Opening a headline marks it read. Read/unread state is stored on the NAS and
  shared across devices. Mark all read applies to the current feed/search scope
  through the loaded snapshot's sequence watermark; later arrivals remain unread.
- Save adds an entry to the existing reading list and article indexing queue.
  Saving an existing URL returns the existing article without changing its title,
  read state, or archive state. Feed read state remains independent.
- Manage feeds shows collection errors, last successful updates, and unsubscribe.
  Unsubscribing removes that subscription's entries, not saved reading-list articles.

## Collection and retention

- The existing worker checks one due feed and one article-indexing job per cycle.
  No new container or monitoring service is required. Feeds update while the page
  is closed, normally every 15 minutes. Manual refresh queues collection in the worker.
- Conditional requests use ETag/Last-Modified. Unchanged feeds retain their entries.
  Failures retain previous data and retry with backoff up to six hours.
- Leases prevent overlapping collection and recover interrupted jobs after two
  minutes. Database transactions remain short; network requests run outside them.
- Entry identity uses publisher IDs and article URLs within each subscription.
  Changed metadata or IDs do not reset read state when the URL still matches.
- Entries expire 90 days after first collection, regardless of read status. Small
  identity hashes survive expiry to prevent old feed items returning as new.
  These hashes are removed when unsubscribing. Saved reading-list articles persist.
- Stored summaries are plain text, limited to 1,000 characters. Responses are
  limited to 3 MiB and 500 entries per feed fetch. Article links must use HTTP(S).
  Fetches use bounded redirects/timeouts and validate public destinations.
- Browser polling reads the local database only. It pauses in hidden tabs and
  while browsing expanded history, unless a requested feed refresh is pending.

## Deployment

Rebuild/recreate API, worker, and web containers using the normal deployment
workflow. New tables are created alongside the existing reading-list tables at
API startup. Keep the existing shared data volume and back it up before deployment.
The worker waits for the new schema. The new Python dependency is feedparser.

## Verification

Run `npm test`, `npm run check`, `npm run build`, and the backend virtual
environment's `python -B -m unittest discover -s backend/tests -q`.
Tests use mocked feed responses and temporary databases, including retention,
pagination, read/save races, discovery, conditional requests, and lease recovery.
