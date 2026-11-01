import logging
import time
from datetime import datetime, timedelta, timezone

from main import database
from extractor import extract_article_text

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
            return
        except Exception:
            logging.info("Waiting for database initialization...")
            time.sleep(3)


def process_next() -> bool:
    with database() as db:
        # language=SQL
        select_pending_article_sql = """
        SELECT id, url, index_attempts
        FROM articles
        WHERE index_status = 'pending'
           OR (
               index_status = 'failed'
               AND index_attempts < ?
               AND (index_retry_at IS NULL OR index_retry_at <= ?)
           )
        ORDER BY (index_status = 'pending') DESC, saved_at ASC
        LIMIT 1
        """
        row = db.execute(
            select_pending_article_sql, (MAX_INDEX_ATTEMPTS, timestamp())
        ).fetchone()

        if row is None:
            return False

        article_id = row["id"]
        url = row["url"]
        attempt = row["index_attempts"] + 1

        # language=SQL
        mark_processing_sql = """
        UPDATE articles
        SET index_status = 'processing',
            index_attempts = index_attempts + 1,
            index_retry_at = NULL
        WHERE id = ?
        """
        db.execute(
            mark_processing_sql,
            (article_id,),
        )

    logging.info("Indexing %s", url)

    try:
        document = extract_article_text(url)

        with database() as db:
            # language=SQL
            save_indexed_article_sql = """
            UPDATE articles
            SET content = ?,
                index_status = 'ready',
                index_error = NULL,
                indexed_at = ?,
                title = CASE
                    WHEN title_source = 'auto'
                     AND ? IS NOT NULL THEN ? ELSE title
                END
            WHERE id = ?
            """
            db.execute(
                save_indexed_article_sql,
                (
                    document.text,
                    timestamp(),
                    document.title,
                    document.title,
                    article_id,
                ),
            )

        logging.info(
            "Indexed %s (%s characters), detected title: %s",
            url,
            len(document.text),
            document.title,
        )

    except Exception as exc:
        logging.warning("Could not index %s: %s", url, exc)
        retry_at = (
            (
                datetime.now(timezone.utc)
                + timedelta(seconds=RETRY_DELAY_SECONDS * 2 ** (attempt - 1))
            ).isoformat()
            if attempt < MAX_INDEX_ATTEMPTS
            else None
        )

        with database() as db:
            # language=SQL
            mark_failed_sql = """
            UPDATE articles
            SET index_status = 'failed',
                index_error = ?,
                index_retry_at = ?
            WHERE id = ?
            """
            db.execute(
                mark_failed_sql,
                (str(exc)[:300], retry_at, article_id),
            )

    return True


def main():
    wait_for_schema()

    # Recover interrupted jobs after container restarts.
    with database() as db:
        # language=SQL
        recover_interrupted_jobs_sql = """
        UPDATE articles
        SET index_status = 'pending'
        WHERE index_status = 'processing'
        """
        db.execute(recover_interrupted_jobs_sql)

    logging.info("Clonepage article worker started")

    while True:
        try:
            if not process_next():
                time.sleep(3)
        except Exception:
            logging.exception("Unexpected worker error")
            time.sleep(5)


if __name__ == "__main__":
    main()
