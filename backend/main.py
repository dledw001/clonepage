
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Response, Query
from pydantic import BaseModel, Field, HttpUrl

from metadata import fetch_title

import re

DB_PATH = Path(
    os.getenv(
        "READING_DB_PATH",
        Path(__file__).parent / "data" / "reading.sqlite3"
    )
)

ARTICLE_COLUMNS = (
    "id, title, url, is_read, saved_at, archived_at, "
    "index_status, indexed_at"
)

@contextmanager
def database():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_generic_pdf_title(title: str, url: str) -> bool:
    parsed = urlsplit(url)

    if not parsed.path.lower().endswith(".pdf"):
        return False

    filename = unquote(parsed.path.rsplit("/", 1)[-1])
    stem = filename[:-4]
    hostname = parsed.hostname or ""

    candidates = {
        hostname.casefold(),
        filename.casefold(),
        stem.casefold(),
        stem.replace("_", " ").casefold(),
    }

    return title.strip().casefold() in candidates


def initialize_search():
    with database() as db:
        db.execute("PRAGMA journal_mode=WAL")

        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(articles)")
        }

        migrations = {
            "content": "TEXT",
            "index_status": "TEXT NOT NULL DEFAULT 'pending'",
            "index_error": "TEXT",
            "indexed_at": "TEXT",
            "title_source": "TEXT NOT NULL DEFAULT 'manual'",
        }

        had_title_source = "title_source" in columns

        for name, definition in migrations.items():
            if name not in columns:
                db.execute(
                    f"ALTER TABLE articles ADD COLUMN {name} {definition}"
                )


        if not had_title_source:
            rows = db.execute("""
                          SELECT id, title, url FROM articles
                          """).fetchall()

            for row in rows:
                if is_generic_pdf_title(row["title"], row["url"]):
                    db.execute("""
                               UPDATE articles
                               SET title_source = 'auto'
                               WHERE id = ?
                               """, (row["id"],))


        # The FTS table indexes text without storing
        # another full copy of the articles.
        exists = db.execute("""
                            SELECT 1 FROM sqlite_master
                            WHERE type = 'table' AND name = 'article_fts'
                            """).fetchone()

        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS article_fts
            USING fts5(
                title,
                url,
                content,
                content='articles',
                content_rowid='rowid'
            )
        """)

        db.executescript("""
                         CREATE TRIGGER IF NOT EXISTS articles_fts_insert
            AFTER INSERT ON articles BEGIN
                INSERT INTO article_fts(rowid, title, url, content)
                VALUES(new.rowid, new.title, new.url, new.content);
                         END;

                         CREATE TRIGGER IF NOT EXISTS articles_fts_delete
            AFTER DELETE ON articles BEGIN
                INSERT INTO article_fts(
                    article_fts, rowid, title, url, content
                )
                VALUES(
                    'delete', old.rowid,
                    old.title, old.url, old.content
                );
                         END;

                         CREATE TRIGGER IF NOT EXISTS articles_fts_update
            AFTER UPDATE OF title, url, content ON articles BEGIN
                  INSERT INTO article_fts(
                             article_fts, rowid, title, url, content
                         )
                  VALUES(
                                                                 'delete', old.rowid,
                                                                 old.title, old.url, old.content
                                                                 );

                         INSERT INTO article_fts(rowid, title, url, content)
                         VALUES(
                                   new.rowid, new.title, new.url, new.content
                               );
                         END;
                         """)

        if not exists:
            db.execute("""
                       INSERT INTO article_fts(article_fts)
                       VALUES('rebuild')
                       """)

@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with database() as db:
        db.execute("""
                   CREATE TABLE IF NOT EXISTS articles (
                                                           id TEXT PRIMARY KEY,
                                                           title TEXT NOT NULL,
                                                           url TEXT NOT NULL UNIQUE,
                                                           is_read INTEGER NOT NULL DEFAULT 0,
                                                           saved_at TEXT NOT NULL,
                                                           archived_at TEXT DEFAULT NULL
                   )
                   """)

        # Migrate existing databases without deleting articles.
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(articles)")
        }

        if "archived_at" not in columns:
            db.execute("""
                       ALTER TABLE articles
                           ADD COLUMN archived_at TEXT DEFAULT NULL
                       """)

    initialize_search()

    yield



app = FastAPI(
    title="Clonepage API",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


class ArticleCreate(BaseModel):
    url: HttpUrl
    title: str = Field(default="", max_length=200)


class ArticleUpdate(BaseModel):
    is_read: bool | None = None
    archived: bool | None = None
    title: str | None = Field(default=None, max_length=200)


def serialize(row):
    article = dict(row)
    article["is_read"] = bool(article["is_read"])
    return article


@app.get("/api/reading")
def list_articles():
    with database() as db:
        rows = db.execute(f"""
                          SELECT {ARTICLE_COLUMNS} FROM articles
                          ORDER BY saved_at DESC
                          """).fetchall()
        return [serialize(row) for row in rows]


@app.post("/api/reading", status_code=201)
def create_article(article: ArticleCreate):
    url = str(article.url)
    provided_title = article.title.strip()
    title = (
            provided_title
            or (
                None
                if urlsplit(url).path.lower().endswith(".pdf")
                else fetch_title(url)
            )
            or urlsplit(url).hostname
            or url
    )
    title_source = (
        "auto"
        if not provided_title or is_generic_pdf_title(provided_title, url)
        else "manual"
    )
    article_id = str(uuid4())
    saved_at = datetime.now(timezone.utc).isoformat()

    with database() as db:
        try:
            db.execute("""
                       INSERT INTO articles
                           (id, title, url, is_read, saved_at, title_source)
                       VALUES (?, ?, ?, 0, ?, ?)
            """, (article_id, title, url, saved_at, title_source))
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Article already saved"
            )

        row = db.execute(
            f"SELECT {ARTICLE_COLUMNS} FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()

        return serialize(row)



@app.patch("/api/reading/{article_id}")
def update_article(article_id: str, update: ArticleUpdate):
    changes = update.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(400, "No changes provided")

    assignments = []
    values = []

    if "is_read" in changes:
        if update.is_read is None:
            raise HTTPException(422, "is_read cannot be null")

        assignments.append("is_read = ?")
        values.append(int(update.is_read))

    if "archived" in changes:
        if update.archived is None:
            raise HTTPException(422, "archived cannot be null")

        archived_at = (
            datetime.now(timezone.utc).isoformat()
            if update.archived else None
        )

        assignments.append("archived_at = ?")
        values.append(archived_at)

    if "title" in changes:
        if not update.title or not update.title.strip():
            raise HTTPException(422, "Title cannot be empty")

        assignments.append("title = ?")
        values.append(update.title.strip())
        assignments.append("title_source = 'manual'")

    values.append(article_id)

    with database() as db:
        db.execute(
            f"""
            UPDATE articles
            SET {', '.join(assignments)}
            WHERE id = ?
            """,
            values
        )

        row = db.execute(
            f"SELECT {ARTICLE_COLUMNS} FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()

        if row is None:
            raise HTTPException(404, "Article not found")

        return serialize(row)



@app.delete("/api/reading/{article_id}", status_code=204)
def delete_article(article_id: str):
    with database() as db:
        result = db.execute(
            "DELETE FROM articles WHERE id = ?",
            (article_id,)
        )

        if result.rowcount == 0:
            raise HTTPException(404, "Article not found")

    return Response(status_code=204)


@app.get("/api/reading/search")
def search_articles(
        q: str = Query(..., min_length=1, max_length=160),
        archived: bool | None = None,
        limit: int = Query(50, ge=1, le=100),
):
    # Convert user input into safe FTS5 search terms.
    # Every term must match, regardless of word order.
    terms = re.findall(r"[^\W_]+", q.lower())[:10]

    if not terms:
        return []

    # Quoted prefix terms avoid exposing FTS5 operators
    # directly to the client.
    match_query = " AND ".join(
        f'"{term}"*' for term in terms
    )

    archived_filter = (
        None if archived is None else int(archived)
    )

    columns = ", ".join(
        f"a.{column.strip()}"
        for column in ARTICLE_COLUMNS.split(",")
    )

    with database() as db:
        rows = db.execute(f"""
            SELECT
                {columns},
                snippet(
                    article_fts,
                    2,
                    '',
                    '',
                    ' … ',
                    24
                ) AS snippet
            FROM article_fts
            JOIN articles a
                ON a.rowid = article_fts.rowid
            WHERE article_fts MATCH ?
              AND (
                  ? IS NULL
                  OR (a.archived_at IS NOT NULL) = ?
              )
            ORDER BY bm25(article_fts, 8.0, 3.0, 1.0)
            LIMIT ?
        """, (
            match_query,
            archived_filter,
            archived_filter,
            limit,
        )).fetchall()

        return [serialize(row) for row in rows]
