"""Portable saved articles, original PDFs, and reader endpoints."""

import base64
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Literal

from extractor import ExtractedDocument, extract_pdf_document
from metadata import is_public_url

router = APIRouter(prefix="/api/reading", tags=["Saved copies"])
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_TOTAL = 12 * 1024 * 1024
MAX_IMAGES = 20
IMAGE_BUDGET_SECONDS = 30
MAX_COPY_BYTES = 24 * 1024 * 1024
COPY_COLUMNS = ("copy_status, copy_saved_at, copy_format, copy_error, copy_warning, "
                "copy_attempts, copy_retry_at, copy_size, copy_is_legacy")


def database():
    from main import database as connect
    return connect()


def copy_root():
    from main import DB_PATH
    return (DB_PATH.parent / "copies").resolve()


def initialize_saved_copies():
    migrations = {
        "copy_status": "TEXT NOT NULL DEFAULT 'pending'", "copy_path": "TEXT",
        "copy_saved_at": "TEXT", "copy_format": "TEXT", "copy_error": "TEXT", "copy_warning": "TEXT",
        "copy_attempts": "INTEGER NOT NULL DEFAULT 0", "copy_retry_at": "TEXT", "copy_job": "TEXT",
        "copy_size": "INTEGER", "copy_source_url": "TEXT", "copy_is_legacy": "INTEGER NOT NULL DEFAULT 0",
        "article_job": "TEXT",
    }
    with database() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
        for name, definition in migrations.items():
            if name not in columns:
                db.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")


def safe_http_url(value, base=""):
    if not value:
        return None
    try:
        url = urljoin(base, str(value))
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        return url
    except ValueError:
        return None


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def fetch_image(client, url, started):
    current = url
    for _ in range(4):
        if time.monotonic() - started >= IMAGE_BUDGET_SECONDS:
            raise ValueError("Image collection time limit reached")
        if not current or not is_public_url(current):
            raise ValueError("Image address is unavailable")
        with client.stream("GET", current) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                current = safe_http_url(response.headers.get("location"), current)
                continue
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=8192):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES or time.monotonic() - started >= IMAGE_BUDGET_SECONDS:
                    raise ValueError("Image collection limit reached")
            mime = image_type(bytes(data))
            if not mime:
                raise ValueError("Unsupported image format")
            return bytes(data), mime
    raise ValueError("Too many image redirects")


def clean_article_html(source, base_url):
    soup = BeautifulSoup(source, "html.parser")
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "svg", "math", "link", "meta", "base"]):
        tag.decompose()
    allowed = {"article", "section", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "strong", "b", "em", "i", "u", "s", "del", "blockquote", "pre", "code",
               "ul", "ol", "li", "dl", "dt", "dd", "a", "img", "figure", "figcaption", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "sup", "sub", "span"}
    for tag in list(body.find_all(True)):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        attrs = dict(tag.attrs)
        tag.attrs = {}
        if tag.name == "a":
            target = safe_http_url(attrs.get("href"), base_url)
            if target:
                tag["href"] = target
                tag["target"] = "_blank"
                tag["rel"] = "noopener noreferrer"
        elif tag.name == "img":
            tag["alt"] = str(attrs.get("alt", ""))[:500]
            source = str(attrs.get("src", ""))
            tag["src"] = source if source.startswith("data:image/") else safe_http_url(source, base_url) or ""
        elif tag.name in ("th", "td"):
            for name in ("colspan", "rowspan"):
                if str(attrs.get(name, "")).isdigit():
                    tag[name] = str(max(1, min(100, int(attrs[name]))))
    return body


def embed_images(body):
    images = body.find_all("img")
    failures, total, cache = 0, 0, {}
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(3, connect=2), follow_redirects=False, trust_env=False,
                      headers={"User-Agent": "Clonepage/0.5 (saved article images)", "Accept": "image/*"}) as client:
        for index, tag in enumerate(images):
            src = tag.get("src", "")
            try:
                if not src or index >= MAX_IMAGES or total >= MAX_IMAGE_TOTAL:
                    raise ValueError("Image collection limit reached")
                if src not in cache:
                    if src.startswith("data:"):
                        prefix, encoded = src.split(",", 1)
                        if not prefix.endswith(";base64") or len(encoded) > MAX_IMAGE_BYTES * 4 / 3 + 8:
                            raise ValueError("Unsupported inline image")
                        data = base64.b64decode(encoded, validate=True)
                        mime = image_type(data)
                        if not mime or len(data) > MAX_IMAGE_BYTES:
                            raise ValueError("Unsupported inline image")
                    else:
                        data, mime = fetch_image(client, src, started)
                    if total + len(data) > MAX_IMAGE_TOTAL:
                        raise ValueError("Image collection size limit reached")
                    total += len(data)
                    cache[src] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
                tag["src"] = cache[src]
            except Exception:
                failures += 1
                replacement = body.new_tag("span") if isinstance(body, BeautifulSoup) else BeautifulSoup("", "html.parser").new_tag("span")
                replacement["class"] = "missing-image"
                replacement.string = f"[Image unavailable: {tag.get('alt') or 'no description'}]"
                tag.replace_with(replacement)
    return f"{failures} image{'s' if failures != 1 else ''} could not be saved; the article text is available." if failures else None


READER_CSS = """
:root{color-scheme:light dark;--bg:#faf9f6;--text:#252c36;--muted:#657080;--line:#d7dbe0;--link:#425f95}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1b2029;--text:#f1efe9;--muted:#a2abba;--line:#3b4351;--link:#b7c7ed}}
:root[data-theme=dark]{--bg:#1b2029;--text:#f1efe9;--muted:#a2abba;--line:#3b4351;--link:#b7c7ed}
*{box-sizing:border-box}body{margin:0;padding:40px 28px;background:var(--bg);color:var(--text);font:18px/1.8 Georgia,serif;overflow-wrap:anywhere}
main{max-width:760px;margin:auto}h1,h2,h3,h4,h5,h6{line-height:1.3;margin:1.5em 0 .7em}h1{font-size:2em;margin-top:0}
:root[data-embedded] main>h1,:root[data-embedded] .metadata,:root[data-embedded] main>.notice{display:none}
p{margin:1em 0}a{color:var(--link)}img{display:block;max-width:100%;height:auto;margin:24px auto}figure{margin:24px 0}figcaption,.metadata,.notice{color:var(--muted);font:14px/1.7 system-ui,sans-serif}
.metadata{padding-bottom:22px;margin-bottom:28px;border-bottom:1px solid var(--line)}.metadata span{display:block}.notice,.missing-image{display:block;padding:12px 16px;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;max-width:100%;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:8px 12px}blockquote{border-left:3px solid var(--line);margin-left:0;padding-left:20px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:color-mix(in srgb,var(--text) 5%,transparent);padding:16px;border-radius:8px}code{font-size:.85em}hr{border:0;border-top:1px solid var(--line);margin:28px 0}
@media(max-width:540px){body{padding:24px 18px;font-size:17px}h1{font-size:1.7em}}
"""


def reader_html(title, url, captured_at, body, warning=None):
    source = safe_http_url(url)
    source_link = f'<a href="{html.escape(source, quote=True)}" target="_blank" rel="noopener noreferrer">Open original</a>' if source else "Original address unavailable"
    notice = f'<p class="notice">{html.escape(warning)}</p>' if warning else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title><style>{READER_CSS}</style></head>'
            f'<body><main><h1>{html.escape(title)}</h1><div class="metadata"><span>Saved copy · {html.escape(captured_at)}</span>{source_link}</div>{notice}'
            f'<article>{body}</article></main></body></html>')


def plain_html(text):
    return "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in text.split("\n\n") if part.strip())


@dataclass(frozen=True)
class StoredCopy:
    path: str
    format: str
    size: int
    warning: str | None
    is_legacy: bool = False


def resolve_copy_path(relative):
    root = copy_root()
    # Accept only the immutable filenames created below, never arbitrary paths.
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}/[0-9a-f-]{36}\.(html|pdf)", relative):
        raise ValueError("Invalid saved-copy path")
    target = root.joinpath(*relative.split("/"))
    resolved = target.resolve()
    if resolved.parent.parent != root or target.is_symlink() or target.parent.is_symlink():
        raise ValueError("Saved-copy path leaves its storage directory")
    return target


def store_copy(article_id, document, title, url, captured_at, legacy=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", article_id):
        raise ValueError("Invalid article identifier")
    warning = None
    if document.pdf is not None and not legacy:
        data, extension = document.pdf, "pdf"
    else:
        if document.html and not legacy:
            body = clean_article_html(document.html, document.source_url or url)
            warning = embed_images(body)
            # The wrapper already supplies the article title.
            first_heading = body.find("h1")
            if first_heading and first_heading.get_text(" ", strip=True).casefold() == title.casefold():
                first_heading.decompose()
            content = body.decode_contents()
        else:
            if not document.text.strip():
                raise ValueError("No readable content is available for a saved copy")
            content = plain_html(document.text)
            warning = "Recovered from indexed text; original formatting and images are unavailable." if legacy else "Text-only copy; original formatting and images are unavailable."
        data = reader_html(title, document.source_url or url, captured_at, content, warning).encode("utf-8")
        extension = "html"
    if len(data) > (25 * 1024 * 1024 if extension == "pdf" else MAX_COPY_BYTES):
        raise ValueError("Saved copy exceeds its storage limit")
    relative = f"{article_id}/{uuid4()}.{extension}"
    path = resolve_copy_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return StoredCopy(relative, extension, len(data), warning, legacy)


def remove_copy(relative):
    if relative:
        try:
            path = resolve_copy_path(relative)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
        except (OSError, ValueError):
            logging.exception("Could not remove an old saved-copy file")


def remove_article_copies(article_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", article_id):
        return
    root = copy_root()
    directory = root / article_id
    if directory.resolve().parent != root or directory.is_symlink() or not directory.is_dir():
        return
    # Flat, generated files only. No recursive deletion or following symlinks.
    for path in directory.iterdir():
        if not path.is_symlink() and re.fullmatch(r"[0-9a-f-]{36}\.(html|pdf)(\.tmp)?", path.name):
            path.unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        pass


def text_from_saved_copy(row):
    data = resolve_copy_path(row["copy_path"]).read_bytes()
    if row["copy_format"] == "pdf":
        return extract_pdf_document(data, row.get("upload_filename") or row["url"])
    soup = BeautifulSoup(data, "html.parser")
    article = soup.find("article")
    text = article.get_text("\n", strip=True) if article else ""
    if not text:
        raise ValueError("The saved copy has no searchable text")
    return ExtractedDocument(text=text[:300_000])


def copy_row(article_id):
    with database() as db:
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Article not found")
    return dict(row)


@router.get("/{article_id}/copy")
def get_copy(article_id: str):
    row = copy_row(article_id)
    available = False
    if row["copy_path"]:
        try:
            available = resolve_copy_path(row["copy_path"]).is_file()
        except ValueError:
            pass
    return {"id": row["id"], "title": row["title"], "url": row["url"], "copy_available": available,
            "source_kind": row["source_kind"], "upload_filename": row["upload_filename"],
            **{name.strip(): row[name.strip()] for name in COPY_COLUMNS.split(",")}}


@router.post("/{article_id}/copy/refresh", status_code=202)
def refresh_copy(article_id: str):
    row = copy_row(article_id)
    if row["source_kind"] == "upload":
        raise HTTPException(409, "Uploaded PDFs have no website to refresh. Upload the document again if its file is missing.")
    with database() as db:
        # Refresh is explicit; keep the last successful file during collection.
        # language=SQL
        refresh_copy_sql = """
        UPDATE articles SET copy_status='pending',copy_error=NULL,copy_attempts=0,copy_retry_at=NULL,copy_job=NULL
        WHERE id=? AND copy_status NOT IN ('pending','processing')
        """
        db.execute(refresh_copy_sql, (article_id,))
        if db.execute("SELECT id FROM articles WHERE id=?", (article_id,)).fetchone() is None:
            raise HTTPException(404, "Article not found")
    return get_copy(article_id)


def serve_copy(article_id, download, theme=None, version=None):
    row = copy_row(article_id)
    for attempt in range(2):
        if version is not None and version != row["copy_saved_at"]:
            raise HTTPException(409, "The saved copy changed. Reopen the reader to load the current version.")
        if not row["copy_path"]:
            raise HTTPException(404, "No saved copy is available yet")
        try:
            data = resolve_copy_path(row["copy_path"]).read_bytes()
            break
        except FileNotFoundError:
            if attempt == 1:
                raise HTTPException(404, "The uploaded PDF is missing. Upload it again to restore the file." if row["source_kind"] == "upload" else "The saved-copy file is missing. Refresh the saved copy to retrieve it again.")
            row = copy_row(article_id)
        except ValueError as exc:
            raise HTTPException(404, "The saved-copy file is unavailable") from exc
    if row["copy_format"] == "html" and not download:
        attributes = ' data-embedded="true"' + (f' data-theme="{theme}"' if theme else '')
        data = data.replace(b'<html lang="en">', f'<html lang="en"{attributes}>'.encode(), 1)
    filename = quote(re.sub(r'[\x00-\x1f/\\]', '_', row['title'])[:120] + '.' + row['copy_format'], safe='')
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Content-Disposition": f"{'attachment' if download else 'inline'}; filename=\"saved-article.{row['copy_format']}\"; filename*=UTF-8''{filename}",
               "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"}
    return Response(content=data, media_type="application/pdf" if row["copy_format"] == "pdf" else "text/html", headers=headers)


@router.get("/{article_id}/copy/content")
def view_copy(article_id: str, theme: Literal['light', 'dark'] | None = Query(None), version: str | None = Query(None, max_length=100)):
    return serve_copy(article_id, False, theme, version)


@router.get("/{article_id}/copy/download")
def download_copy(article_id: str):
    return serve_copy(article_id, True)
