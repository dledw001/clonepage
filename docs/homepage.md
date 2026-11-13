# Homepage and reading continuity

Use **Customize homepage** (the sliders beside the theme button) to reorder or hide Needs attention, Your places, Continue reading, Reading list, Feeds, and Hacker News. Choose 3, 5, or 10 preview items and the Hacker News view and time window. You can hide the clock and greeting while keeping search available. Save layout applies these preferences to all devices through the NAS database. Restore defaults prepares the original layout; Save layout commits it.

Changes made in another session cannot be silently overwritten. Reload the latest layout if a conflict appears. Attention links can temporarily reveal a hidden section without changing your preferences.

## Needs attention

The summary combines current service availability failures, WUD updates and incomplete checks, feed refresh errors, and failed saved copies. Expand it for details and links to the relevant service, container monitor, feed section, or saved article. It refreshes while the homepage is visible and supports a manual refresh.

Unavailable or stale checks are reported separately from confirmed service failures. A failed refresh retains the last available summary and displays an error rather than claiming everything is healthy. WUD results come from its existing monitoring snapshot; opening the homepage does not trigger registry scans or update containers. Snoozed updates retain the existing WUD integration behavior.

## Continue reading

Saved HTML articles now remember an approximate paragraph and offset, so resuming survives different screen widths. Position is stored on the NAS, with a pending local backup if a save fails. The sticky reader toolbar shows progress, returns to the reading list, and offers Start over. Existing PDF positions remain compatible.

Continue reading shows recent unfinished saved articles and PDFs. HTML articles appear after 2% and disappear near the end (98%). PDFs remain eligible on their last page until marked read because reaching that page does not establish completion. Read and archived items are excluded. A new saved-copy version invalidates the old position. Simultaneous sessions cannot silently overwrite each other: choose the saved position or explicitly keep the current one when a conflict appears.

This resumes copies stored on the NAS; downloading a file for disconnected reading does not synchronize progress from an external viewer. Desktop and phone-sized browser layouts are verified locally; real iPhone suspension behavior still needs device testing.

## Storage and deployment

Rebuild API, worker, and web together using the existing deployment process. Startup adds HTML progress columns and a homepage settings table to the existing SQLite database. Existing saved copies, read states, and PDF progress are retained. Back up the whole data directory as described in saved-copies.md.

## API

- GET /api/home/layout returns settings and revision; PUT accepts {revision, settings}. A stale revision returns 409.
- GET /api/home/attention returns grouped counts and linked attention items.
- GET /api/home/continue returns up to 50 recent eligible reading positions.
- GET /api/reading/{id}/article-progress returns copy_version, revision, and position. POST accepts {copy_version, revision, position}; position contains fraction, anchor, and offset, or null to reset. A stale copy or revision returns 409.

Run npm test, npm run check, npm run build, and python -B -m unittest discover -s backend/tests -q. Deployment shell tests require Linux/WSL.
