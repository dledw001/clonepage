# Saved article copies

Every article added through the reading list, Firefox extension, or RSS Save
action is queued for a saved copy. The existing worker downloads the article
once for capture and search indexing. No additional service is required.

## Reading and downloads

- Read saved copy opens `/reading/<article-id>` in a new tab with Clonepage's
  reader and theme. Open original remains available separately.
- Web articles become clean HTML with headings, links, tables, and embedded
  raster images. Download HTML produces a self-contained file that opens without
  connecting to the NAS or publisher. Links still lead to their original targets.
- PDFs retain the original downloaded bytes, including scanned or encrypted PDFs
  whose text cannot be extracted. Those remain readable/downloadable even when
  content search reports an indexing failure. OCR is not included.
- Copy status, capture date, image-retrieval notes, and retry actions are shown.
  A copy stored only on the NAS still requires access to the NAS; download the
  file before going fully offline. The app itself is not a browser offline cache.
- Archive moves the article out of the queue and keeps its copy. Permanent delete
  removes the article and its saved files.

## Capture and refresh

- The first successful copy is retained. Later search-index retries read that
  snapshot instead of silently fetching a newer page.
- Refresh saved copy explicitly queues a replacement. The current file stays
  available until a new capture succeeds. Failed source requests or file writes
  preserve the previous file, capture date, and search content.
- Failed captures retry up to three times, with a delay of one then two minutes.
  Manual retry starts another capture sequence. Search indexing has its own status.
- Existing articles are queued automatically after migration. If a source is no
  longer available, previously indexed text is recovered into a clearly labeled
  HTML fallback. That copy lacks original formatting/images; capture failures
  remain visible so it is not presented as a complete original capture.
- Missing/blocked/oversized images become labeled placeholders with a note;
  the captured page never retains remote image URLs as an offline dependency.
- Scripts, forms, publisher styles, embeds, and event handlers are removed.
  Dynamic interactives and video are not preserved. Server-side capture cannot
  use a logged-in browser session. Browser-based capture can be added separately.
- HTML source downloads retain the existing 2 MiB limit; original PDFs are
  limited to 25 MiB. Images are limited to 20 per capture, 2 MiB each, 12 MiB total,
  and a 30-second collection budget. Saved HTML is limited to 24 MiB.

## Storage and deployment

Rebuild/recreate API, worker, and web containers together. New article columns
are migrated automatically. The worker waits for the new schema and recovers
interrupted capture jobs when restarted.

Copies live in `copies/` beside the database: normally `/data/copies` inside the
containers, backed by the existing shared `./data` volume on the NAS. The database
records capture metadata; immutable HTML/PDF files hold the content. Files are
written atomically before their database record is published. Superseded or
abandoned files are removed after the worker finishes its job.

Back up and restore the **whole data directory**, including the database, its
SQLite journal files when present, and `copies/`. For a simple file backup, stop
API and worker first, copy the directory, then restart them. A database-only
backup contains search text and metadata but does not include saved files.

## Tests

Run `npm test`, `npm run check`, `npm run build`, and the backend virtual
environment's `python -B -m unittest discover -s backend/tests -q`.
Tests use temporary databases/files and mocked downloads to cover preservation,
image embedding, scanned PDFs, retries, deletion races, and legacy text recovery.
