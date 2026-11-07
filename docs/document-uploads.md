# Reading-list PDF uploads

Choose **Upload PDF** next to **Add article**, select a PDF on your device, optionally enter a title, and upload it. The original file is kept on the NAS and is immediately readable/downloadable through the usual saved-copy reader. The worker extracts text for the existing full-text search and detects the PDF title when no custom title was supplied.

- PDF files only, up to 25 MiB (the same limit as downloaded PDFs). Extension, signature, basic PDF structure, and actual streamed size are checked; uploads do not depend on a public URL.
- Scanned/image-only and encrypted PDFs remain available as originals. They cannot be indexed without readable text; OCR and password entry are outside this feature.
- Identical file bytes are detected across queue and archive, even under a different filename. An existing document with a missing stored file can be restored by uploading the same PDF again, preserving its title and read/archive state.
- Uploaded PDFs have no **Open original** or website-refresh action. **Retry now** retries indexing from NAS storage. Deleting the reading-list item removes its saved file and search entry.
- Files use generated immutable paths under `data/copies/`; user filenames are display metadata only. Back up the SQLite database and the entire `data/copies/` folder together.

## Deployment

Rebuild/recreate API, worker, and web together after pulling the change. The API adds upload metadata columns and a unique file-hash index without changing existing URL articles. The worker waits for the new schema before processing jobs.

```sh
sudo docker compose up -d --build --force-recreate
```

## API

`POST /api/reading/upload` accepts an `application/pdf` request body. `X-Filename` (required) and `X-Document-Title` (optional) carry percent-encoded UTF-8 metadata. The browser sends the selected File directly, so there is no multipart dependency or base64 overhead. Success returns the usual article record with `source_kind: "upload"` and `upload_filename`; `url` is its internal `/reading/{id}` reader address. No public source URL is invented.

Uploads are capped both by declared Content-Length and actual bytes read. Rejections use 413 for oversized files, 415 for non-PDF request types, 422 for invalid files/metadata, 409 for duplicates, and 507 for NAS storage failures.
