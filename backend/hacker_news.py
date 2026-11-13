"""Personal Hacker News rankings, a persistent response cache, and read state."""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel

from feeds import clean_text, http_url

router = APIRouter(prefix="/api/hacker-news", tags=["Hacker News"])
WINDOWS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
LIMIT = 100
CACHE_SECONDS = 300
HN_API = "https://hacker-news.firebaseio.com/v0"
SEARCH_API = "https://hn.algolia.com/api/v1/search"
MAX_ID = 9007199254740991
StoryId = Path(ge=1, le=MAX_ID)


def database():
    from main import database as connect
    return connect()


def initialize_hacker_news():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS hn_cache (
        key TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        fetched_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS hn_stories (
        id INTEGER PRIMARY KEY,
        payload TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS hn_read_state (
        story_id INTEGER PRIMARY KEY REFERENCES hn_stories(id) ON DELETE CASCADE,
        read_at TEXT NOT NULL
    );
    """
    with database() as db:
        db.executescript(schema_sql)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
        if "hn_story_id" not in columns:
            db.execute("ALTER TABLE articles ADD COLUMN hn_story_id INTEGER")


def integer(value, default=None):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def normalize_story(item, *, algolia=False):
    if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
        return None
    if not algolia and item.get("type") != "story":
        return None
    raw_id = item.get("objectID") if algolia else item.get("id")
    try:
        story_id = int(raw_id)
    except (ValueError, TypeError, OverflowError):
        return None
    if not 0 < story_id <= MAX_ID or isinstance(raw_id, bool):
        return None
    created = integer(item.get("created_at_i") if algolia else item.get("time"))
    title = clean_text(item.get("title"), 500)
    if not created or created > 253402300799 or not title:
        return None
    discussion = f"https://news.ycombinator.com/item?id={story_id}"
    return {
        "id": story_id, "title": title, "url": http_url(item.get("url")) or discussion,
        "discussion_url": discussion,
        "author": clean_text(item.get("author") if algolia else item.get("by"), 100),
        "points": integer(item.get("points") if algolia else item.get("score"), 0),
        "comments": integer(item.get("num_comments") if algolia else item.get("descendants"), 0),
        "created_at": datetime.fromtimestamp(created, timezone.utc).isoformat(), "rank": None,
    }


def cache_entry(key):
    with database() as db:
        row = db.execute("SELECT payload,fetched_at FROM hn_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload"]), row["fetched_at"]
    except (ValueError, TypeError):
        return None


def decorate(stories):
    if not stories:
        return []
    placeholders = ','.join('?' for _ in stories)
    with database() as db:
        read = {row["story_id"] for row in db.execute(
            f"SELECT story_id FROM hn_read_state WHERE story_id IN ({placeholders})", [story["id"] for story in stories])}
        saved = {row["url"]: row["id"] for row in db.execute(
            f"SELECT id,url FROM articles WHERE url IN ({placeholders})", [story["url"] for story in stories])}
    return [{**story, "is_read": story["id"] in read, "is_saved": story["url"] in saved,
             "article_id": saved.get(story["url"])} for story in stories]


class HackerNewsGateway:
    def __init__(self, client, clock=time.time):
        self.client = client
        self.clock = clock
        self._locks = {}
        self._ids_lock = asyncio.Lock()
        self._ids = None
        self._ids_at = 0
        self._failed_at = {}
        self._slots = asyncio.Semaphore(6)

    async def get(self, url, **params):
        async with self._slots:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def top_ids(self, force=False):
        async with self._ids_lock:
            if self._ids is not None and self.clock() - self._ids_at < (30 if force else CACHE_SECONDS):
                return self._ids
            data = await self.get(HN_API + "/topstories.json")
            if not isinstance(data, list) or any(integer(value) is None or not 0 < value <= MAX_ID for value in data):
                raise ValueError("Invalid HN ranking")
            self._ids, self._ids_at = data[:30], self.clock()
            return self._ids

    async def search(self, since, until, *, comments=None, exact=False, count=1000):
        filters = f"created_at_i>={since},created_at_i<={until}"
        if comments is not None:
            filters += f",num_comments{'=' if exact else '>='}{comments}"
        data = await self.get(SEARCH_API, tags="story", numericFilters=filters, hitsPerPage=count,
                              attributesToRetrieve="objectID,title,url,author,points,num_comments,created_at_i")
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list) or integer(data.get("nbHits")) is None:
            raise ValueError("Invalid HN search results")
        return data

    async def discussed(self, since, until):
        # Algolia sorts primarily by points, not comments, and caps pagination.
        # Narrow by comment count until every candidate fits on one page. If a
        # large tie crosses the cap, fill the boundary using its top-point hits.
        lower, upper, threshold, upper_hits = 0, None, 64, []
        for _ in range(24):
            data = await self.search(since, until, comments=threshold)
            total, hits = data["nbHits"], data["hits"]
            if total <= 1000 and (total >= LIMIT or threshold == 0):
                if len(hits) != total:
                    raise ValueError("Incomplete HN search results")
                return hits
            if total > 1000:
                lower = threshold
            else:
                upper, upper_hits = threshold, hits
            if upper is not None and upper - lower <= 1:
                boundary = await self.search(since, until, comments=lower, exact=True, count=LIMIT)
                return upper_hits + boundary["hits"]
            threshold = max(1, threshold * 2) if upper is None else (lower + upper) // 2
        raise ValueError("HN comment ranking could not be completed")

    async def collect(self, view, window, force):
        if view == "front":
            ids = await self.top_ids(force)
            raw = await asyncio.gather(*(self.get(f"{HN_API}/item/{story_id}.json") for story_id in ids))
            stories = []
            for position, item in enumerate(raw, 1):
                story = normalize_story(item)
                if story:
                    stories.append({**story, "rank": position})
            return {"stories": stories, "notice": ""}
        until = int(self.clock())
        since = until - WINDOWS[window]
        raw = (await self.search(since, until, count=LIMIT))["hits"] if view == "top" else await self.discussed(since, until)
        stories = {}
        for item in raw:
            story = normalize_story(item, algolia=True)
            if story and since <= item["created_at_i"] <= until:
                stories[story["id"]] = story
        order = (lambda story: (-story["comments"], -story["points"], story["id"])) if view == "discussed" else (lambda story: (-story["points"], -story["comments"], story["id"]))
        ranked = sorted(stories.values(), key=order)[:LIMIT]
        notice = ""
        try:
            ids = await self.top_ids(force)
            ranks = {story_id: index + 1 for index, story_id in enumerate(ids)}
            ranked = [{**story, "rank": ranks.get(story["id"])} for story in ranked]
        except (httpx.HTTPError, ValueError, TypeError):
            notice = "Current HN front-page ranks could not be refreshed."
        return {"stories": ranked, "notice": notice}

    async def snapshot(self, view, window, force=False):
        key = "front" if view == "front" else f"{view}:{window}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        error, stale = "", False
        async with lock:
            cached = cache_entry(key)
            cooling_down = key in self._failed_at and self.clock() - self._failed_at[key] < 30
            if cooling_down:
                if not cached:
                    raise HTTPException(503, "Hacker News could not be reached. Try again shortly.")
                error, stale = "Hacker News could not be refreshed. Showing the last available stories.", True
            if not cooling_down and (not cached or self.clock() - cached[1] >= (30 if force else CACHE_SECONDS)):
                try:
                    async with asyncio.timeout(30):
                        payload = await self.collect(view, window, force)
                    fetched = self.clock()
                    with database() as db:
                        # Publish metadata and the ranking atomically. Personal read
                        # state is stored separately and never overwritten by refresh.
                        db.executemany("INSERT INTO hn_stories(id,payload,updated_at) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                                       [(story["id"], json.dumps(story), fetched) for story in payload["stories"]])
                        db.execute("INSERT INTO hn_cache(key,payload,fetched_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at",
                                   (key, json.dumps(payload), fetched))
                    self._failed_at.pop(key, None)
                    cached = payload, fetched
                except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
                    self._failed_at[key] = self.clock()
                    if not cached:
                        raise HTTPException(503, "Hacker News could not be reached. Try again shortly.")
                    error, stale = "Hacker News could not be refreshed. Showing the last available stories.", True
        payload, fetched = cached
        return {"view": view, "window": window, "stories": decorate(payload["stories"]),
                "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                "stale": stale, "error": error, "notice": payload.get("notice", ""), "limit": LIMIT}


@router.get("")
async def list_stories(request: Request, response: Response, view: Literal["front", "top", "discussed"] = "front",
                       window: Literal["24h", "7d", "30d"] = "24h", force: bool = False):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.hacker_news.snapshot(view, window, force)


class ReadInput(BaseModel):
    is_read: bool


@router.patch("/{story_id}/read")
def set_read(body: ReadInput, story_id: int = StoryId):
    with database() as db:
        if not db.execute("SELECT 1 FROM hn_stories WHERE id=?", (story_id,)).fetchone():
            raise HTTPException(404, "HN story not found. Refresh the reader first.")
        if body.is_read:
            db.execute("INSERT INTO hn_read_state(story_id,read_at) VALUES (?,?) ON CONFLICT(story_id) DO UPDATE SET read_at=excluded.read_at",
                       (story_id, datetime.now(timezone.utc).isoformat()))
        else:
            db.execute("DELETE FROM hn_read_state WHERE story_id=?", (story_id,))
    return {"id": story_id, "is_read": body.is_read}


@router.post("/{story_id}/save")
def save_story(story_id: int = StoryId):
    from main import ARTICLE_COLUMNS, ArticleCreate, save_article, serialize
    with database() as db:
        row = db.execute("SELECT payload FROM hn_stories WHERE id=?", (story_id,)).fetchone()
    if not row:
        raise HTTPException(404, "HN story not found. Refresh the reader first.")
    story = json.loads(row["payload"])
    article = save_article(ArticleCreate(title=story["title"], url=story["url"]), return_existing=True)
    with database() as db:
        db.execute("UPDATE articles SET hn_story_id=COALESCE(hn_story_id,?) WHERE id=?", (story_id, article["id"]))
        row = db.execute(f"SELECT {ARTICLE_COLUMNS} FROM articles WHERE id=?", (article["id"],)).fetchone()
    return serialize(row)
