# PDF reader and saved progress

Saved PDFs now open in a self-hosted PDF.js reader. Uploaded PDFs and PDFs captured from public URLs use the same reader. HTML saved copies keep their existing reader.

The toolbar provides previous/next page, a page-number jump, zoom controls, fit-to-width, and **Start over**. PDF text remains selectable; existing PDF hyperlinks can open in a separate tab. PDF forms, scripting, editing, and annotations are outside this feature. **Download PDF** remains available, including for encrypted PDFs that should be opened in a separate reader.

## Resume behavior

- The reader saves the current page and PDF coordinates within that page, rather than screen pixels or the furthest page reached. Reopening adapts those coordinates to the new screen width.
- The reading-list link shows **Continue · page N of M**. Progress never marks the document read automatically.
- Positions are saved after a pause, periodically while reading, when the app is backgrounded, and when leaving the reader. A small local backup keeps pending positions if the NAS is temporarily unreachable. The UI indicates whether a cross-device save succeeded.
- Progress is stored in SQLite on the NAS and is included in the existing database backup. A newer server revision wins over an older local backup. Concurrent older sessions cannot silently replace it; the reader offers **Resume saved position** or **Keep this position**.
- Returning to a backgrounded app reloads the NAS position. The final save after navigation notifies the reading list even if its reader has already closed.
- **Start over** clears the saved position and returns to the first page. Clearing also advances the revision so an older pending save cannot undo it.
- Replacing a captured PDF invalidates its prior position. Restoring the exact same missing uploaded PDF preserves its progress. Downloaded copies read outside Clonepage do not report progress back to the site.

## Performance and compatibility

PDF.js is pinned in package-lock.json. The browser lazily loads its Safari-compatible legacy build only when opening a PDF. Its worker, fonts, character maps, images, color profiles, and WebAssembly resources are served from Clonepage; no PDF is sent to a third-party reader. Resources are generated into ignored `public/pdfjs/` by `predev` and `prebuild`.

The viewer retains a small rendering cache rather than rendering all pages at once. Each page canvas is capped at approximately two million pixels to bound mobile memory use. Large images or complex documents can still be demanding. Layout restoration waits for actual scaled page geometry so zoom/rotation-related screen-size changes do not alter the reading position. Native browser pinch zoom remains available alongside the toolbar controls.

Desktop and phone-sized layouts are checked locally. Real iPhone Safari/Home Screen behavior still needs device verification, especially app suspension and memory use with large scans.

## Deployment

Rebuild API, worker, and web with the usual Docker Compose command. Startup adds progress metadata columns to existing articles without changing their content or read/archive state.

```sh
sudo docker compose up -d --build --force-recreate
```

## API

`GET /api/reading/{id}/progress` returns the current saved-copy version, revision, and optional position. `POST` to the same endpoint accepts `{copy_version, revision, position}`; a null position clears it. Positions contain `page`, `page_count`, `left`, and `top`. Bounds and finite coordinates are validated. A 409 rejects an outdated saved-copy version or progress revision. Saved-copy content requests also reject a stale `version` query parameter, preventing a reader from associating new file bytes with an old position.
