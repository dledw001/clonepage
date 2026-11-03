import logging
import time
import errno
import httpx
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from main import database
from extractor import extract_article_text
from feeds import process_due_feed
from extractor import ExtractedDocument
from saved_copies import store_copy, remove_copy, text_from_saved_copy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

MAX_INDEX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 60


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait_for_schema():
    # language=SQL
    check_index_schema_sql = """
    SELECT index_status, index_attempts, index_retry_at
    FROM articles LIMIT 0
    """
    while True:
        try:
            with database() as db:
                db.execute(check_index_schema_sql)
                db.execute("SELECT rowid FROM article_fts LIMIT 0")
                db.execute("SELECT id FROM feed_subscriptions LIMIT 0")
                db.execute("SELECT copy_status,article_job FROM articles LIMIT 0")
            return
        except Exception:
            logging.info("Waiting for database initialization...")
            time.sleep(3)


def next_retry(attempt):
    return ((datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS * 2 ** (attempt - 1))).isoformat()
            if attempt < MAX_INDEX_ATTEMPTS else None)


def capture_error(exc, storage=False):
    if storage and isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "The NAS is out of storage space."
        if exc.errno in (errno.EACCES, errno.EROFS):
            return "The saved-copy folder is not writable."
        return "The saved file could not be written to NAS storage."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (404, 410):
            return f"The original page is no longer available (HTTP {status})."
        if status in (401, 403):
            return f"The original website denied access (HTTP {status})."
        return f"The original website returned HTTP {status}."
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "The original website took too long to respond."
    if isinstance(exc, httpx.RequestError):
        return "The original website could not be reached."
    if isinstance(exc, FileNotFoundError):
        return "The saved file is missing. Refresh the saved copy to retrieve it again."
    return str(exc)[:300] or "The article could not be retrieved."


def process_next() -> bool:
    now = timestamp()
    token = str(uuid4())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        # language=SQL
        select_job_sql = """
        SELECT * FROM articles
        WHERE index_status!='processing' AND copy_status!='processing' AND (
            index_status='pending' OR (index_status='failed' AND index_attempts<? AND (index_retry_at IS NULL OR index_retry_at<=?))
            OR copy_status='pending' OR (copy_status='failed' AND copy_attempts<? AND (copy_retry_at IS NULL OR copy_retry_at<=?))
        )
        ORDER BY (index_status='pending') DESC,(copy_status='pending') DESC,saved_at ASC,id ASC LIMIT 1
        """
        found = db.execute(select_job_sql, (MAX_INDEX_ATTEMPTS, now, MAX_INDEX_ATTEMPTS, now)).fetchone()
        if found is None:
            return False
        row = dict(found)
        article_id = row["id"]
        index_due = row["index_status"] == "pending" or (row["index_status"] == "failed" and row["index_attempts"] < MAX_INDEX_ATTEMPTS
                    and (not row["index_retry_at"] or row["index_retry_at"] <= now))
        copy_due = row["copy_status"] == "pending" or (row["copy_status"] == "failed" and row["copy_attempts"] < MAX_INDEX_ATTEMPTS
                   and (not row["copy_retry_at"] or row["copy_retry_at"] <= now)) or (index_due and not row["copy_path"])
        index_attempt = row["index_attempts"] + int(index_due)
        copy_attempt = row["copy_attempts"] + int(copy_due)
        # Index status acts as the article-job lock. Generation tokens prevent
        # interrupted workers from publishing over a newer capture.
        # language=SQL
        claim_job_sql = """
        UPDATE articles SET index_status='processing',index_attempts=?,index_retry_at=NULL,article_job=?,
            copy_status=CASE WHEN ? THEN 'processing' ELSE copy_status END,
            copy_attempts=?,copy_job=CASE WHEN ? THEN ? ELSE copy_job END
        WHERE id=?
        """
        db.execute(claim_job_sql, (index_attempt, token, copy_due, copy_attempt, copy_due, token, article_id))

    logging.info("Preparing article %s", article_id)
    document, extraction_error, capture_failure, copy_error, candidate = None, None, None, None, None
    captured_at = timestamp()
    try:
        # Re-index the stored snapshot without silently recapturing the source.
        document = extract_article_text(row["url"]) if copy_due or not row["copy_path"] else text_from_saved_copy(row)
    except Exception as exc:
        # Preserve the existing indexing diagnostic contract while presenting
        # concise capture errors in the saved-copy UI.
        extraction_error = str(exc)[:300] or "The article could not be retrieved."
        capture_failure = capture_error(exc)
        logging.warning("Article capture failed (%s): %s", article_id, extraction_error)

    if copy_due:
        if document is not None:
            try:
                title = document.title if row["title_source"] == "auto" and document.title else row["title"]
                captured_at = timestamp()
                candidate = store_copy(article_id, document, title, row["url"], captured_at)
            except Exception as exc:
                copy_error = capture_error(exc, storage=True)
                logging.warning("Saved-file write failed (%s): %s", article_id, exc)
        else:
            copy_error = capture_failure or extraction_error
            if not row["copy_path"] and row["content"]:
                try:
                    # Preserve old search text when the original has disappeared.
                    captured_at = row["indexed_at"] or timestamp()
                    candidate = store_copy(article_id, ExtractedDocument(row["content"]), row["title"], row["url"], captured_at, legacy=True)
                except Exception:
                    logging.exception("Could not recover indexed text as a saved copy")

    published = False
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT article_job FROM articles WHERE id=?", (article_id,)).fetchone()
            if current is None or current["article_job"] != token:
                return True
            # A failed refresh must retain both the prior file and its search text.
            retain_index = (document is None or (copy_due and copy_error and row["copy_path"])) and not index_due
            if retain_index:
                db.execute("UPDATE articles SET index_status=?,index_error=?,index_retry_at=?,article_job=NULL WHERE id=? AND article_job=?",
                           (row["index_status"], row["index_error"], row["index_retry_at"], article_id, token))
            elif document is not None and document.text.strip():
                # language=SQL
                save_index_sql = """
                UPDATE articles SET content=?,index_status='ready',index_error=NULL,indexed_at=?,index_retry_at=NULL,article_job=NULL,
                    title=CASE WHEN title_source='auto' AND ? IS NOT NULL THEN ? ELSE title END
                WHERE id=? AND article_job=?
                """
                db.execute(save_index_sql, (document.text, timestamp(), document.title, document.title, article_id, token))
            else:
                # A valid image-only PDF remains downloadable even without OCR.
                terminal = document is not None
                error = extraction_error or (document.text_error if document else None) or "No searchable text was found."
                # language=SQL
                fail_index_sql = """
                UPDATE articles SET index_status='failed',index_error=?,index_attempts=?,index_retry_at=?,article_job=NULL WHERE id=? AND article_job=?
                """
                db.execute(fail_index_sql, (error, MAX_INDEX_ATTEMPTS if terminal else index_attempt,
                                           None if terminal else next_retry(index_attempt), article_id, token))

            if copy_due:
                succeeded = candidate is not None and not candidate.is_legacy and not copy_error
                # COALESCE keeps the previous immutable file on every failure.
                # language=SQL
                save_copy_sql = """
                UPDATE articles SET copy_status=?,copy_error=?,copy_retry_at=?,copy_job=NULL,
                    copy_path=COALESCE(?,copy_path),copy_saved_at=COALESCE(?,copy_saved_at),copy_format=COALESCE(?,copy_format),
                    copy_size=COALESCE(?,copy_size),copy_warning=CASE WHEN ? THEN ? ELSE copy_warning END,
                    copy_is_legacy=CASE WHEN ? THEN ? ELSE copy_is_legacy END,
                    copy_source_url=CASE WHEN ? THEN ? ELSE copy_source_url END
                WHERE id=? AND copy_job=?
                """
                result = db.execute(save_copy_sql, ('ready' if succeeded else 'failed', None if succeeded else copy_error,
                    None if succeeded else next_retry(copy_attempt), candidate.path if candidate else None,
                    captured_at if candidate else None, candidate.format if candidate else None, candidate.size if candidate else None,
                    candidate is not None, candidate.warning if candidate else None, candidate is not None, int(candidate.is_legacy) if candidate else 0,
                    candidate is not None, document.source_url if document and document.source_url else row["url"], article_id, token))
                published = candidate is not None and result.rowcount == 1
    finally:
        if candidate and not published:
            remove_copy(candidate.path)
    if published and row["copy_path"]:
        remove_copy(row["copy_path"])
    return True

def run_cycle() -> bool:
    worked = False
    # One task from each queue per cycle: a busy or failing feed cannot starve
    # saved-article indexing, and a long article queue cannot starve feeds.
    for task in (process_due_feed, process_next):
        try:
            worked = task() or worked
        except Exception:
            logging.exception("Unexpected %s error", getattr(task, "__name__", "worker task"))
    return worked


def main():
    wait_for_schema()

    # Recover interrupted jobs after container restarts.
    with database() as db:
        # language=SQL
        recover_interrupted_jobs_sql = """
        UPDATE articles SET index_status='pending',article_job=NULL,
            copy_status=CASE WHEN copy_status='processing' THEN 'pending' ELSE copy_status END,copy_job=NULL
        WHERE index_status='processing' OR copy_status='processing'
        """
        db.execute(recover_interrupted_jobs_sql)

    logging.info("Clonepage article and feed worker started")

    while True:
        try:
            if not run_cycle():
                time.sleep(3)
        except Exception:
            logging.exception("Unexpected worker error")
            time.sleep(5)


if __name__ == "__main__":
    main()
