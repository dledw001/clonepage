
import logging
import time
from datetime import datetime, timezone

from main import database
from extractor import extract_article_text


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait_for_schema():
    while True:
        try:
            with database() as db:
                db.execute("SELECT index_status FROM articles LIMIT 0")
                db.execute("SELECT rowid FROM article_fts LIMIT 0")
            return
        except Exception:
            logging.info("Waiting for database initialization...")
            time.sleep(3)


def process_next() -> bool:
    with database() as db:
        row = db.execute("""
                         SELECT id, url
                         FROM articles
                         WHERE index_status = 'pending'
                         ORDER BY saved_at ASC
                             LIMIT 1
                         """).fetchone()

        if row is None:
            return False

        article_id = row["id"]
        url = row["url"]

        db.execute("""
                   UPDATE articles
                   SET index_status = 'processing'
                   WHERE id = ?
                   """, (article_id,))

    logging.info("Indexing %s", url)

    try:
        document = extract_article_text(url)

        with database() as db:
            db.execute("""
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
                       """, (document.text, timestamp(), document.title, document.title, article_id))

        logging.info("Indexed %s (%s characters), detected title: %s", url, len(document.text), document.title)

    except Exception as exc:
        logging.warning("Could not index %s: %s", url, exc)

        with database() as db:
            db.execute("""
                       UPDATE articles
                       SET index_status = 'failed',
                           index_error = ?
                       WHERE id = ?
                       """, (str(exc)[:300], article_id))

    return True


def main():
    wait_for_schema()

    # Recover interrupted jobs after container restarts.
    with database() as db:
        db.execute("""
                   UPDATE articles
                   SET index_status = 'pending'
                   WHERE index_status = 'processing'
                   """)

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
