
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, HttpUrl

from metadata import fetch_title

DB_PATH = Path(
    os.getenv(
        "READING_DB_PATH",
        Path(__file__).parent / "data" / "reading.sqlite3"
    )
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
                                                           saved_at TEXT NOT NULL
                   )
                   """)
    yield


app = FastAPI(title="Clonepage API", lifespan=lifespan)


class ArticleCreate(BaseModel):
    url: HttpUrl
    title: str = Field(default="", max_length=200)


class ArticleUpdate(BaseModel):
    is_read: bool


def serialize(row):
    article = dict(row)
    article["is_read"] = bool(article["is_read"])
    return article


@app.get("/api/reading")
def list_articles():
    with database() as db:
        rows = db.execute("""
                          SELECT * FROM articles
                          ORDER BY saved_at DESC
                          """).fetchall()
        return [serialize(row) for row in rows]


@app.post("/api/reading", status_code=201)
def create_article(article: ArticleCreate):
    url = str(article.url)
    title = article.title.strip() or fetch_title(url) or urlsplit(url).hostname or url
    article_id = str(uuid4())
    saved_at = datetime.now(timezone.utc).isoformat()

    with database() as db:
        try:
            db.execute("""
                       INSERT INTO articles
                           (id, title, url, is_read, saved_at)
                       VALUES (?, ?, ?, 0, ?)
                       """, (article_id, title, url, saved_at))
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Article already saved"
            )

        row = db.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()

        return serialize(row)


@app.patch("/api/reading/{article_id}")
def update_article(article_id: str, update: ArticleUpdate):
    with database() as db:
        result = db.execute("""
                            UPDATE articles
                            SET is_read = ?
                            WHERE id = ?
                            """, (int(update.is_read), article_id))

        if result.rowcount == 0:
            raise HTTPException(404, "Article not found")

        row = db.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()

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
