"""Personal RSS/Atom subscriptions, collection, and same-origin reader API."""

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import feedparser
import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, HttpUrl

from metadata import is_public_url

POLL_SECONDS = 15 * 60
RETENTION_DAYS = 90
MAX_BYTES = 3 * 1024 * 1024
MAX_ENTRIES = 500
router = APIRouter(prefix="/api/feeds", tags=["Feeds"])


def database():
    # Resolve lazily so the API and worker use the same configured database.
    from main import database as connect
    return connect()


def utcnow():
    return datetime.now(timezone.utc)


def initialize_feeds():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS feed_subscriptions (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        website TEXT,
        last_checked_at TEXT,
        last_success_at TEXT,
        next_check_at TEXT NOT NULL,
        etag TEXT,
        last_modified TEXT,
        error TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        lease_token TEXT,
        lease_until TEXT
    );
    CREATE TABLE IF NOT EXISTS feed_entries (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        feed_id TEXT NOT NULL REFERENCES feed_subscriptions(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        summary TEXT NOT NULL,
        published_at TEXT,
        first_seen_at TEXT NOT NULL,
        sort_at TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS feed_entries_sort ON feed_entries(sort_at DESC, seq DESC);
    CREATE INDEX IF NOT EXISTS feed_entries_feed ON feed_entries(feed_id);
    CREATE INDEX IF NOT EXISTS feed_entries_retention ON feed_entries(first_seen_at);
    CREATE TABLE IF NOT EXISTS feed_seen (
        feed_id TEXT NOT NULL REFERENCES feed_subscriptions(id) ON DELETE CASCADE,
        identity TEXT NOT NULL,
        entry_id TEXT REFERENCES feed_entries(id) ON DELETE SET NULL,
        PRIMARY KEY (feed_id, identity)
    );
    """
    with database() as db:
        db.executescript(schema_sql)


def clean_text(value, limit):
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def http_url(value, base=""):
    if not value:
        return None
    try:
        url = urldefrag(urljoin(base, str(value or "")))[0]
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return None
        # Preserve query parameters: they may be part of an article's identity.
        return str(HttpUrl(urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))))
    except ValueError:
        return None


@dataclass
class Document:
    url: str
    body: bytes
    status: int
    headers: dict


def fetch_document(url, etag=None, modified=None):
    headers = {"User-Agent": "Clonepage/0.3 (personal feed reader)",
               "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html;q=0.8"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    started = time.monotonic()
    with httpx.Client(headers=headers, timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
        current = http_url(url)
        for _ in range(4):
            if not current or not is_public_url(current):
                raise ValueError("Use a public HTTP or HTTPS feed URL.")
            with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    current = http_url(response.headers.get("location"), current) if response.headers.get("location") else None
                    if time.monotonic() - started > 20:
                        raise ValueError("The feed took too long to respond.")
                    continue
                if response.status_code == 304:
                    return Document(current, b"", 304, dict(response.headers))
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("The feed is too large (maximum 3 MiB).")
                    if time.monotonic() - started > 20:
                        raise ValueError("The feed took too long to respond.")
                return Document(current, bytes(body), response.status_code, dict(response.headers))
    raise ValueError("The feed redirected too many times.")


def parse_feed(document):
    parsed = feedparser.parse(document.body, response_headers={"content-location": document.url,
                              "content-type": document.headers.get("content-type", "")})
    if not parsed.version or (parsed.get("bozo") and not parsed.entries):
        raise ValueError("This URL did not return a readable RSS or Atom feed.")
    entries = []
    now = utcnow()
    for item in parsed.entries[:MAX_ENTRIES]:
        url = http_url(item.get("link"), document.url)
        if not url:
            continue
        date = item.get("published_parsed") or item.get("updated_parsed")
        published = None
        if date:
            try:
                value = datetime(*date[:6], tzinfo=timezone.utc)
                if value <= now + timedelta(days=1):
                    published = value.isoformat()
            except (ValueError, OverflowError, TypeError):
                pass
        identities = ["url:" + url]
        if item.get("id"):
            identities.append("id:" + str(item.id))
        entries.append({"title": clean_text(item.get("title"), 200) or urlsplit(url).hostname,
                        "url": url, "summary": clean_text(item.get("summary", ""), 1000),
                        "published_at": published,
                        "identities": [hashlib.sha256(identity.encode()).hexdigest() for identity in identities]})
    return {"title": clean_text(parsed.feed.get("title"), 200) or urlsplit(document.url).hostname,
            "url": document.url, "website": http_url(parsed.feed.get("link"), document.url), "entries": entries}


def prune_entries(db, now):
    # Seen fingerprints survive expiry; an old item still in a publisher's feed
    # must not reappear as unread. They contain no article text or URLs.
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    db.execute("DELETE FROM feed_entries WHERE first_seen_at < ?", (cutoff,))


def store_entries(db, feed_id, entries, now):
    prune_entries(db, now)
    for entry in entries:
        identities = entry["identities"]
        placeholders = ",".join("?" for _ in identities)
        matches = db.execute(f"SELECT entry_id FROM feed_seen WHERE feed_id=? AND identity IN ({placeholders})", (feed_id, *identities)).fetchall()
        if matches and any(row["entry_id"] is None for row in matches):
            continue
        entry_id = matches[0]["entry_id"] if matches else str(uuid4())
        if matches:
            # language=SQL
            update_entry_sql = """
            UPDATE feed_entries SET title=?, url=?, summary=?, published_at=?,
                sort_at=COALESCE(?, first_seen_at) WHERE id=?
            """
            db.execute(update_entry_sql, (entry["title"], entry["url"], entry["summary"], entry["published_at"], entry["published_at"], entry_id))
        else:
            # language=SQL
            insert_entry_sql = """
            INSERT INTO feed_entries(id,feed_id,title,url,summary,published_at,first_seen_at,sort_at)
            VALUES(?,?,?,?,?,?,?,?)
            """
            db.execute(insert_entry_sql, (entry_id, feed_id, entry["title"], entry["url"], entry["summary"],
                                         entry["published_at"], now.isoformat(), entry["published_at"] or now.isoformat()))
        for identity in identities:
            db.execute("INSERT OR IGNORE INTO feed_seen(feed_id,identity,entry_id) VALUES(?,?,?)", (feed_id, identity, entry_id))


def process_due_feed():
    now = utcnow()
    token = str(uuid4())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        # language=SQL
        due_feed_sql = """
        SELECT * FROM feed_subscriptions
        WHERE next_check_at<=? AND (lease_until IS NULL OR lease_until<=?)
        ORDER BY next_check_at,id LIMIT 1
        """
        row = db.execute(due_feed_sql, (now.isoformat(), now.isoformat())).fetchone()
        if row is None:
            prune_entries(db, now)
            return False
        subscription = dict(row)
        db.execute("UPDATE feed_subscriptions SET lease_token=?,lease_until=? WHERE id=?",
                   (token, (now + timedelta(minutes=2)).isoformat(), subscription["id"]))
    try:
        document = fetch_document(subscription["url"], subscription["etag"], subscription["last_modified"])
        parsed = parse_feed(document) if document.status != 304 else None
        checked = utcnow()
        with database() as db:
            current = db.execute("SELECT lease_token FROM feed_subscriptions WHERE id=?", (subscription["id"],)).fetchone()
            if not current or current["lease_token"] != token:
                return True
            if parsed:
                store_entries(db, subscription["id"], parsed["entries"], checked)
            # Retain a permanent subscription URL; redirects are followed on each
            # request without introducing collisions between existing sources.
            # language=SQL
            complete_poll_sql = """
            UPDATE feed_subscriptions SET title=?,website=?,last_checked_at=?,last_success_at=?,
                next_check_at=?,etag=?,last_modified=?,error=NULL,failures=0,lease_token=NULL,lease_until=NULL
            WHERE id=? AND lease_token=?
            """
            db.execute(complete_poll_sql, (parsed["title"] if parsed else subscription["title"],
                       parsed["website"] if parsed else subscription["website"], checked.isoformat(), checked.isoformat(),
                       (checked + timedelta(seconds=POLL_SECONDS)).isoformat(),
                       document.headers.get("etag", subscription["etag"] if document.status == 304 else None),
                       document.headers.get("last-modified", subscription["last_modified"] if document.status == 304 else None), subscription["id"], token))
    except Exception as exc:
        checked = utcnow()
        failures = subscription["failures"] + 1
        message = friendly_error(exc)
        logging.warning("Feed refresh failed (%s): %s", subscription["id"], message)
        # language=SQL
        fail_poll_sql = """
        UPDATE feed_subscriptions SET last_checked_at=?,next_check_at=?,error=?,failures=?,lease_token=NULL,lease_until=NULL
        WHERE id=? AND lease_token=?
        """
        with database() as db:
            db.execute(fail_poll_sql, (checked.isoformat(), (checked + timedelta(seconds=min(6 * 3600, POLL_SECONDS * 2 ** min(failures - 1, 5)))).isoformat(),
                                      message, failures, subscription["id"], token))
    return True


def friendly_error(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "The feed took too long to respond."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"The feed returned HTTP {exc.response.status_code}."
    if isinstance(exc, httpx.RequestError):
        return "The feed could not be reached."
    return str(exc)[:200] if isinstance(exc, ValueError) else "The feed could not be refreshed."


class UrlInput(BaseModel):
    url: HttpUrl


@router.post("/discover")
def discover(body: UrlInput):
    try:
        document = fetch_document(str(body.url))
        try:
            parsed = parse_feed(document)
            return {"preview": {**{key: parsed[key] for key in ("title", "url", "website")},
                                "entry_count": len(parsed["entries"]), "samples": [entry["title"] for entry in parsed["entries"][:3]]}, "candidates": []}
        except ValueError:
            soup = BeautifulSoup(document.body, "html.parser")
            candidates = {}
            base_tag = soup.find("base", href=True)
            base_url = http_url(base_tag["href"], document.url) if base_tag else document.url
            for link in soup.find_all("link", href=True):
                if "alternate" not in [rel.lower() for rel in link.get("rel", [])] or link.get("type", "").split(";")[0].lower().strip() not in ("application/rss+xml", "application/atom+xml", "application/rdf+xml"):
                    continue
                url = http_url(link["href"], base_url or document.url)
                if url:
                    candidates[url] = {"url": url, "title": clean_text(link.get("title"), 200) or url}
                if len(candidates) >= 5:
                    break
            if not candidates:
                # Some WordPress themes remove discovery tags while their
                # standard feed remains available (including ARLnow).
                wordpress = b"/wp-content/" in document.body or b"/wp-includes/" in document.body
                fallback = http_url("/feed/", document.url)
                if wordpress and fallback and fallback != document.url:
                    try:
                        native = parse_feed(fetch_document(fallback))
                        return {"preview": {**{key: native[key] for key in ("title", "url", "website")},
                                            "entry_count": len(native["entries"]), "samples": [entry["title"] for entry in native["entries"][:3]]}, "candidates": []}
                    except (ValueError, httpx.HTTPError):
                        pass
                raise ValueError("No RSS or Atom feed was advertised on this page. Try pasting a direct feed URL.")
            return {"preview": None, "candidates": list(candidates.values())}
    except Exception as exc:
        raise HTTPException(400, friendly_error(exc)) from exc


@router.post("/subscriptions", status_code=201)
def subscribe(body: UrlInput):
    try:
        document = fetch_document(str(body.url))
        parsed = parse_feed(document)
    except Exception as exc:
        raise HTTPException(400, friendly_error(exc)) from exc
    now = utcnow()
    feed_id = str(uuid4())
    with database() as db:
        try:
            # language=SQL
            insert_feed_sql = """
            INSERT INTO feed_subscriptions(id,url,title,website,last_checked_at,last_success_at,next_check_at,etag,last_modified)
            VALUES(?,?,?,?,?,?,?,?,?)
            """
            db.execute(insert_feed_sql, (feed_id, document.url, parsed["title"], parsed["website"], now.isoformat(), now.isoformat(),
                                        (now + timedelta(seconds=POLL_SECONDS)).isoformat(), document.headers.get("etag"), document.headers.get("last-modified")))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "You already subscribe to this feed.") from exc
        store_entries(db, feed_id, parsed["entries"], now)
    return {"id": feed_id, "title": parsed["title"]}


@router.delete("/subscriptions/{feed_id}", status_code=204)
def unsubscribe(feed_id: str):
    with database() as db:
        if not db.execute("DELETE FROM feed_subscriptions WHERE id=?", (feed_id,)).rowcount:
            raise HTTPException(404, "Feed not found.")
    return Response(status_code=204)


def entry_scope(feed_id, q, unread):
    conditions, params = [], []
    if feed_id:
        conditions.append("e.feed_id=?")
        params.append(feed_id)
    if unread:
        conditions.append("e.is_read=0")
    if q.strip():
        conditions.append("(instr(lower(e.title),lower(?))>0 OR instr(lower(e.summary),lower(?))>0)")
        params.extend([q.strip(), q.strip()])
    return " AND ".join(conditions) or "1=1", params


@router.get("")
def list_feeds(feed_id: str | None = None, q: str = Query("", max_length=200), unread: bool = True,
               cursor: str | None = None, limit: int = Query(25, ge=1, le=100)):
    now = utcnow()
    where, params = entry_scope(feed_id, q, unread)
    with database() as db:
        prune_entries(db, now)
        # Capture one consistent snapshot for list, counts, and mark-all watermark.
        db.execute("SELECT 1 FROM feed_subscriptions LIMIT 1")
        # language=SQL
        subscriptions_sql = """
        SELECT f.id,f.url,f.title,f.website,f.last_success_at,f.last_checked_at,f.error,f.next_check_at,f.lease_until,
            (SELECT COUNT(*) FROM feed_entries e WHERE e.feed_id=f.id AND e.is_read=0) AS unread
        FROM feed_subscriptions f ORDER BY f.title COLLATE NOCASE,f.id
        """
        subscriptions = [dict(row) for row in db.execute(subscriptions_sql)]
        for subscription in subscriptions:
            subscription["refresh_pending"] = subscription["next_check_at"] <= now.isoformat() or bool(subscription.pop("lease_until"))
        unread_count = db.execute("SELECT COUNT(*) FROM feed_entries WHERE is_read=0").fetchone()[0]
        through = db.execute("SELECT COALESCE(MAX(seq),0) FROM feed_entries").fetchone()[0]
        total = db.execute(f"SELECT COUNT(*) FROM feed_entries e WHERE {where}", params).fetchone()[0]
        if cursor:
            try:
                sort_at, seq = cursor.rsplit("|", 1)
                datetime.fromisoformat(sort_at)
                seq = int(seq)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, "Invalid page cursor.") from exc
            where += " AND (e.sort_at<? OR (e.sort_at=? AND e.seq<?))"
            params.extend([sort_at, sort_at, seq])
        # language=SQL
        entries_sql = f"""
        SELECT e.*,f.title AS feed_title,
            EXISTS(SELECT 1 FROM articles a WHERE a.url=e.url) AS is_saved
        FROM feed_entries e JOIN feed_subscriptions f ON f.id=e.feed_id
        WHERE {where} ORDER BY e.sort_at DESC,e.seq DESC LIMIT ?
        """
        rows = [dict(row) for row in db.execute(entries_sql, (*params, limit + 1))]
        more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            row["is_read"], row["is_saved"] = bool(row["is_read"]), bool(row["is_saved"])
        next_cursor = f"{rows[-1]['sort_at']}|{rows[-1]['seq']}" if more else None
    return {"subscriptions": subscriptions, "entries": rows, "unread": unread_count, "total": total,
            "through_seq": through, "next_cursor": next_cursor}


class ReadInput(BaseModel):
    is_read: bool


@router.patch("/entries/{entry_id}")
def mark_read(entry_id: str, body: ReadInput):
    with database() as db:
        if not db.execute("UPDATE feed_entries SET is_read=? WHERE id=?", (int(body.is_read), entry_id)).rowcount:
            raise HTTPException(404, "Feed entry not found.")
    return {"id": entry_id, "is_read": body.is_read}


class MarkAllInput(BaseModel):
    through_seq: int = Field(ge=0)
    feed_id: str | None = None
    q: str = Field(default="", max_length=200)


@router.post("/read")
def mark_all_read(body: MarkAllInput):
    where, params = entry_scope(body.feed_id, body.q, True)
    with database() as db:
        count = db.execute(f"UPDATE feed_entries AS e SET is_read=1 WHERE {where} AND e.seq<=?", (*params, body.through_seq)).rowcount
    return {"updated": count}


class RefreshInput(BaseModel):
    feed_id: str | None = None


@router.post("/refresh", status_code=202)
def request_refresh(body: RefreshInput):
    with database() as db:
        if body.feed_id:
            count = db.execute("UPDATE feed_subscriptions SET next_check_at=? WHERE id=?", (utcnow().isoformat(), body.feed_id)).rowcount
            if not count:
                raise HTTPException(404, "Feed not found.")
        else:
            count = db.execute("UPDATE feed_subscriptions SET next_check_at=?", (utcnow().isoformat(),)).rowcount
    return {"queued": count}


@router.post("/entries/{entry_id}/save")
def save_to_reading(entry_id: str):
    from main import ArticleCreate, save_article
    with database() as db:
        entry = db.execute("SELECT title,url FROM feed_entries WHERE id=?", (entry_id,)).fetchone()
    if entry is None:
        raise HTTPException(404, "Feed entry not found.")
    return save_article(ArticleCreate(title=entry["title"], url=entry["url"]), return_existing=True)
