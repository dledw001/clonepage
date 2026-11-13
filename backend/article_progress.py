"""Versioned positions for saved HTML articles, independent of PDF positions."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/reading", tags=["Article progress"])
HTML_PROGRESS_NAMES = ("html_progress_version", "html_progress_fraction", "html_progress_anchor", "html_progress_offset", "html_progress_updated_at", "html_progress_revision")
HTML_PROGRESS_COLUMNS = ", ".join(HTML_PROGRESS_NAMES)


def database():
    from main import database as connect
    return connect()


def initialize_article_progress():
    with database() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(articles)')}
        for name, definition in zip(HTML_PROGRESS_NAMES, ['TEXT', 'REAL', 'INTEGER', 'REAL', 'TEXT', 'INTEGER NOT NULL DEFAULT 0']):
            if name not in columns:
                db.execute(f'ALTER TABLE articles ADD COLUMN {name} {definition}')


def progress_for(row):
    if row.get('copy_format') != 'html' or row.get('html_progress_fraction') is None or row.get('html_progress_version') != row.get('copy_saved_at'):
        return None
    return {'fraction': row['html_progress_fraction'], 'anchor': row['html_progress_anchor'],
            'offset': row['html_progress_offset'], 'updated_at': row['html_progress_updated_at']}


def require_article(row):
    if row is None:
        raise HTTPException(404, 'Article not found')
    if row['copy_format'] != 'html' or not row['copy_saved_at']:
        raise HTTPException(409, 'Article positions are available for saved HTML copies.')


def snapshot(row):
    return {'copy_version': row['copy_saved_at'], 'revision': row['html_progress_revision'], 'position': progress_for(dict(row))}


class ArticlePosition(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    fraction: float = Field(ge=0, le=1)
    anchor: int = Field(ge=0, le=100000, strict=True)
    offset: float = Field(ge=0, le=1)


class ArticleProgressUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    copy_version: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0, le=9007199254740991, strict=True)
    position: ArticlePosition | None


@router.get('/{article_id}/article-progress')
def get_progress(article_id: str, response: Response):
    with database() as db:
        row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
    require_article(row)
    response.headers['Cache-Control'] = 'no-store'
    return snapshot(row)


@router.post('/{article_id}/article-progress')
def save_progress(article_id: str, update: ArticleProgressUpdate, response: Response):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
        require_article(row)
        if row['copy_saved_at'] != update.copy_version:
            raise HTTPException(409, {'reason': 'copy_changed', 'message': 'The saved article changed. Reopen it to continue.'})
        if row['html_progress_revision'] != update.revision:
            raise HTTPException(409, {'reason': 'progress_changed', 'message': 'Reading progress changed in another session.'})
        position = update.position
        # language=SQL
        save_sql = """
        UPDATE articles SET html_progress_version=?, html_progress_fraction=?, html_progress_anchor=?,
            html_progress_offset=?, html_progress_updated_at=?, html_progress_revision=html_progress_revision+1
        WHERE id=?
        """
        db.execute(save_sql, (update.copy_version, position.fraction if position else None, position.anchor if position else None,
                             position.offset if position else None, datetime.now(timezone.utc).isoformat(), article_id))
        row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
    response.headers['Cache-Control'] = 'no-store'
    return snapshot(row)
