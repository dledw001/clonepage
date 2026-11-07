"""Versioned PDF reading positions shared across the personal dashboard's devices."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(prefix="/api/reading", tags=["Reading progress"])
PROGRESS_NAMES = ("pdf_progress_version", "pdf_progress_page", "pdf_progress_pages", "pdf_progress_left", "pdf_progress_top", "pdf_progress_updated_at", "pdf_progress_revision")
PROGRESS_COLUMNS = ", ".join(PROGRESS_NAMES)


def database():
    from main import database as connect
    return connect()


def initialize_pdf_progress():
    with database() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(articles)")}
        definitions = ["TEXT", "INTEGER", "INTEGER", "REAL", "REAL", "TEXT", "INTEGER NOT NULL DEFAULT 0"]
        for name, definition in zip(PROGRESS_NAMES, definitions):
            if name not in columns:
                db.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")


def progress_for(row):
    if row.get("copy_format") != "pdf" or not row.get("pdf_progress_page") or row.get("pdf_progress_version") != row.get("copy_saved_at"):
        return None
    return {"page": row["pdf_progress_page"], "page_count": row["pdf_progress_pages"],
            "left": row["pdf_progress_left"], "top": row["pdf_progress_top"], "updated_at": row["pdf_progress_updated_at"]}


def state(row):
    return {"copy_version": row["copy_saved_at"], "revision": row["pdf_progress_revision"], "position": progress_for(row)}


def require_pdf(row):
    if row is None:
        raise HTTPException(404, "Article not found")
    if row["copy_format"] != "pdf" or not row["copy_saved_at"]:
        raise HTTPException(409, "Reading progress is available for saved PDFs.")


class PDFPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    page: int = Field(ge=1, le=50000, strict=True)
    page_count: int = Field(ge=1, le=50000, strict=True)
    left: float = Field(ge=-10000000, le=10000000)
    top: float = Field(ge=-10000000, le=10000000)

    @model_validator(mode="after")
    def page_within_document(self):
        if self.page > self.page_count:
            raise ValueError("Page must be within the document")
        return self


class ProgressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    copy_version: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=0, le=9007199254740991, strict=True)
    position: PDFPosition | None


@router.get("/{article_id}/progress")
def get_progress(article_id: str, response: Response):
    with database() as db:
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    require_pdf(row)
    response.headers["Cache-Control"] = "no-store"
    return state(dict(row))


@router.post("/{article_id}/progress")
def save_progress(article_id: str, update: ProgressUpdate, response: Response):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
        require_pdf(row)
        if row["copy_saved_at"] != update.copy_version:
            raise HTTPException(409, {"reason": "copy_changed", "message": "The saved PDF changed. Reopen the reader to continue."})
        if row["pdf_progress_revision"] != update.revision:
            raise HTTPException(409, {"reason": "progress_changed", "message": "Reading progress changed in another session."})
        position = update.position
        # Clearing progress also advances the revision, invalidating writes from older tabs.
        # language=SQL
        save_progress_sql = """
        UPDATE articles SET pdf_progress_version=?, pdf_progress_page=?, pdf_progress_pages=?, pdf_progress_left=?,
            pdf_progress_top=?, pdf_progress_updated_at=?, pdf_progress_revision=pdf_progress_revision+1
        WHERE id=?
        """
        db.execute(save_progress_sql, (update.copy_version, position.page if position else None,
                   position.page_count if position else None, position.left if position else None,
                   position.top if position else None, datetime.now(timezone.utc).isoformat(), article_id))
        row = db.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    response.headers["Cache-Control"] = "no-store"
    return state(dict(row))
