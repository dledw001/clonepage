"""Local PDF ingestion into the existing saved-copy and indexing pipeline."""

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from extractor import ExtractedDocument, MAX_PDF_BYTES
from saved_copies import remove_copy, resolve_copy_path, store_copy

router = APIRouter(prefix="/api/reading", tags=["Reading uploads"])
UPLOAD_COLUMNS = "source_kind, upload_filename"


def initialize_uploads():
    from main import database
    with database() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
        for name, definition in {
            "source_kind": "TEXT NOT NULL DEFAULT 'url'",
            "upload_filename": "TEXT",
            "upload_sha256": "TEXT",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS articles_upload_sha256 ON articles(upload_sha256) WHERE source_kind='upload'")


def upload_metadata(filename, title):
    try:
        filename, title = unquote(filename, errors="strict"), unquote(title, errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(422, "The document filename or title could not be read.") from None
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not filename.lower().endswith(".pdf") or not 5 <= len(filename) <= 255 or re.search(r"[\x00-\x1f\x7f]", filename):
        raise HTTPException(422, "Choose a PDF file with a valid filename.")
    title = " ".join(title.split())
    if len(title) > 200:
        raise HTTPException(422, "Document titles must be 200 characters or fewer.")
    return filename, title


def persist_upload(data, filename, title):
    from main import ARTICLE_COLUMNS, database, serialize
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "This file is not a PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if not reader.trailer.get("/Root"):
            raise ValueError("Missing PDF catalog")
    except Exception:
        raise HTTPException(422, "This PDF could not be opened. It may be damaged or incomplete.") from None
    digest = hashlib.sha256(data).hexdigest()
    article_id = str(uuid4())
    saved_at = datetime.now(timezone.utc).isoformat()
    display_title = title or filename[:-4].replace("_", " ").strip()[:200] or "Uploaded PDF"
    candidate = None
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM articles WHERE upload_sha256=? AND source_kind='upload'", (digest,)).fetchone()
            if existing:
                try:
                    available = resolve_copy_path(existing["copy_path"]).is_file()
                except ValueError:
                    available = False
                if available:
                    raise HTTPException(409, "This PDF is already in your reading list, including archived items.")
                # Re-uploading the same bytes repairs a missing file without losing read/archive state.
                article_id, display_title = existing["id"], existing["title"]
            candidate = store_copy(article_id, ExtractedDocument(text="", pdf=data), display_title, "", saved_at)
            if existing:
                # language=SQL
                restore_upload_sql = """
                UPDATE articles SET pdf_progress_version=CASE WHEN pdf_progress_version=copy_saved_at THEN ? ELSE pdf_progress_version END,
                    copy_status='ready', copy_path=?, copy_saved_at=?, copy_format='pdf', copy_size=?,
                    copy_error=NULL, copy_retry_at=NULL, copy_attempts=0, copy_job=NULL,
                    index_status='pending', index_error=NULL, index_retry_at=NULL, index_attempts=0, article_job=NULL
                WHERE id=?
                """
                db.execute(restore_upload_sql, (saved_at, candidate.path, saved_at, candidate.size, article_id))
            else:
                # The local reader route is the canonical address; no public source is invented.
                # language=SQL
                insert_upload_sql = """
                INSERT INTO articles (id, title, url, saved_at, title_source, source_kind, upload_filename, upload_sha256,
                    copy_status, copy_path, copy_saved_at, copy_format, copy_size)
                VALUES (?, ?, ?, ?, ?, 'upload', ?, ?, 'ready', ?, ?, 'pdf', ?)
                """
                db.execute(insert_upload_sql, (article_id, display_title, f"/reading/{article_id}", saved_at,
                           "manual" if title else "auto", filename, digest, candidate.path, saved_at, candidate.size))
            row = db.execute(f"SELECT {ARTICLE_COLUMNS} FROM articles WHERE id=?", (article_id,)).fetchone()
        return serialize(row)
    except sqlite3.IntegrityError:
        if candidate:
            remove_copy(candidate.path)
        raise HTTPException(409, "This PDF is already in your reading list, including archived items.") from None
    except Exception as error:
        if candidate:
            remove_copy(candidate.path)
        if isinstance(error, OSError):
            logging.exception("Could not store uploaded PDF %s", article_id)
            raise HTTPException(507, "The NAS could not store the PDF. Check available storage and folder permissions.") from None
        raise


@router.post("/upload", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload_pdf(request: Request, filename: str = Header(..., alias="X-Filename", max_length=3060),
                     title: str = Header("", alias="X-Document-Title", max_length=2400)):
    """Upload one PDF as the request body; filename/title headers are URL-encoded UTF-8."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise HTTPException(415, "Upload a PDF document.")
    filename, title = upload_metadata(filename, title)
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(400, "Invalid upload size.") from None
        if length > MAX_PDF_BYTES:
            raise HTTPException(413, "PDFs must be 25 MB or smaller.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDFs must be 25 MB or smaller.")
        data.extend(chunk)
    if not data:
        raise HTTPException(422, "The PDF file is empty.")
    return await run_in_threadpool(persist_upload, bytes(data), filename, title)
