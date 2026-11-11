"""Queue boundaries and real POSIX shutdown signals, using temporary databases."""

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


class WorkerShutdownTests(unittest.TestCase):
    def test_shutdown_before_cycle_does_not_claim_a_job(self):
        stop = worker.ShutdownRequest()
        stop.set()
        with patch.object(worker, "process_due_feed") as feed, patch.object(worker, "process_next") as article:
            self.assertFalse(worker.run_cycle(stop))
        feed.assert_not_called()
        article.assert_not_called()

    def test_shutdown_during_feed_finishes_it_without_starting_an_article(self):
        stop = worker.ShutdownRequest()
        def finish_feed():
            stop.set()
            return True
        with patch.object(worker, "process_due_feed", side_effect=finish_feed), patch.object(worker, "process_next") as article:
            self.assertTrue(worker.run_cycle(stop))
        article.assert_not_called()

    def test_shutdown_interrupts_schema_retry(self):
        stop = worker.ShutdownRequest()
        with patch.object(worker, "database", side_effect=sqlite3.OperationalError("schema unavailable")) as connect, \
                patch.object(stop, "wait", side_effect=lambda seconds: stop.set()) as wait:
            self.assertFalse(worker.wait_for_schema(stop))
        connect.assert_called_once()
        wait.assert_called_once_with(3)


# Run the actual main loop in a subprocess: Windows does not deliver POSIX SIGTERM.
CHILD = textwrap.dedent(r"""
    import signal
    import sys
    import time
    from pathlib import Path
    from unittest.mock import patch

    import main
    import worker
    import feeds
    from extractor import ExtractedDocument
    from fastapi.testclient import TestClient

    mode, folder = sys.argv[1], Path(sys.argv[2])
    ready, release = folder / 'ready', folder / 'release'

    class ObservedRequest(worker.ShutdownRequest):
        def set(self):
            super().set()
            (folder / 'signal-received').touch()

    worker.ShutdownRequest = ObservedRequest

    def wait_for_release():
        ready.touch()
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Test did not release the active job')
            time.sleep(0.01)

    if mode == 'schema':
        connect = worker.database
        def missing_schema():
            ready.touch()
            return connect()
        worker.database = missing_schema
    else:
        with TestClient(main.app), patch.object(main, 'fetch_title', return_value='Saved article'):
            if mode in ('article', 'feed'):
                main.save_article(main.ArticleCreate(title='Saved article', url='https://example.com/article'))
            if mode == 'feed':
                with main.database() as db:
                    db.execute("INSERT INTO feed_subscriptions (id,url,title,next_check_at) VALUES (?,?,?,?)",
                               ('feed', 'https://example.com/feed', 'News', worker.timestamp()))
        if mode == 'article':
            worker.process_due_feed = lambda: False
            def extract(url):
                wait_for_release()
                return ExtractedDocument('The saved article text survives a shutdown request.')
            worker.extract_article_text = extract
        elif mode == 'feed':
            def fetch(*args):
                wait_for_release()
                body = b'<rss version="2.0"><channel><title>News</title><link>https://example.com/</link><description>News</description><item><guid>one</guid><title>Story</title><link>https://example.com/story</link></item></channel></rss>'
                return feeds.Document('https://example.com/feed', body, 200, {})
            feeds.fetch_document = fetch
        elif mode == 'idle':
            worker.process_due_feed = lambda: False
            def no_article():
                ready.touch()
                return False
            worker.process_next = no_article
        elif mode == 'error':
            def failed_cycle(stop):
                ready.touch()
                raise RuntimeError('Simulated worker loop failure')
            worker.run_cycle = failed_cycle

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    worker.main()
    assert all(signal.getsignal(sig) == handler for sig, handler in previous.items())
""")


@unittest.skipIf(os.name == "nt", "requires POSIX shutdown signals")
class WorkerSignalTests(unittest.TestCase):
    def wait_for_file(self, process, path):
        deadline = time.monotonic() + 15
        while not path.exists():
            if process.poll() is not None:
                self.fail(process.communicate()[1])
            if time.monotonic() >= deadline:
                self.fail(f"Worker did not reach {path.name}")
            time.sleep(0.01)

    def run_shutdown(self, mode, sig=signal.SIGTERM):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            database = folder / 'reading.sqlite3'
            process = subprocess.Popen(
                [sys.executable, '-u', '-c', CHILD, mode, str(folder)],
                cwd=Path(worker.__file__).parent,
                env={**os.environ, 'READING_DB_PATH': str(database)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.wait_for_file(process, folder / 'ready')
                process.send_signal(sig)
                self.wait_for_file(process, folder / 'signal-received')
                if mode in ('article', 'feed'):
                    self.assertIsNone(process.poll(), 'active job must finish before the worker exits')
                    (folder / 'release').touch()
                _, errors = process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0, errors)
                self.assertIn('worker stopped', errors)
                if mode in ('article', 'feed'):
                    with sqlite3.connect(database) as db:
                        status, copy_status, copy_path = db.execute('SELECT index_status,copy_status,copy_path FROM articles').fetchone()
                        if mode == 'article':
                            self.assertEqual((status, copy_status), ('ready', 'ready'))
                            self.assertTrue((folder / 'copies' / copy_path).is_file(), 'saved copy was published before exit')
                        else:
                            self.assertEqual((status, copy_status), ('pending', 'pending'), 'next queue was left untouched')
                            self.assertEqual(db.execute('SELECT COUNT(*) FROM feed_entries').fetchone()[0], 1)
                            self.assertIsNone(db.execute('SELECT lease_token FROM feed_subscriptions').fetchone()[0])
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_sigterm_wakes_idle_worker(self):
        self.run_shutdown('idle')

    def test_sigint_wakes_idle_worker(self):
        self.run_shutdown('idle', signal.SIGINT)

    def test_sigterm_while_waiting_for_database(self):
        self.run_shutdown('schema')

    def test_sigterm_during_error_backoff(self):
        self.run_shutdown('error')

    def test_sigterm_finishes_and_publishes_active_article(self):
        self.run_shutdown('article')

    def test_sigterm_finishes_feed_without_claiming_next_article(self):
        self.run_shutdown('feed')


if __name__ == '__main__':
    unittest.main()
