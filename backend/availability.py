"""Bounded, credential-free HTTP availability checks for opted-in saved shortcuts."""

import asyncio
import logging
import socket
import ssl
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/api/shortcuts", tags=["Shortcuts"])
logger = logging.getLogger(__name__)
CHECK_INTERVAL = 60
STALE_AFTER = 150


def database():
    from main import database as connect
    return connect()


def initialize_availability():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS shortcut_availability (
        shortcut_id TEXT PRIMARY KEY REFERENCES shortcuts(id) ON DELETE CASCADE,
        target TEXT NOT NULL,
        state TEXT NOT NULL,
        detail TEXT NOT NULL,
        status_code INTEGER,
        checked_at TEXT NOT NULL,
        last_success_at TEXT
    )
    """
    with database() as db:
        db.execute(schema_sql)


def timestamp():
    return datetime.now(timezone.utc)


def age(value, now):
    if not value:
        return float("inf")
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            return float("inf")
        return max(0, (now - moment).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def failure_detail(error):
    seen = set()
    cause = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "The HTTPS certificate could not be verified."
        if isinstance(cause, socket.gaierror):
            return "The service address could not be resolved."
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "The service did not respond in time."
    return "Could not establish a connection from the Clonepage server."


class AvailabilityGateway:
    def __init__(self, client, service_domain="", proxy_ip=None, now=timestamp):
        self.client = client
        self.domain = service_domain.lower().strip(".")
        self.proxy_ip = proxy_ip
        self.now = now
        self._slots = asyncio.Semaphore(4)
        self._task = None
        self._closed = False

    def load(self):
        # language=SQL
        select_sql = """
        SELECT s.id, s.value, a.state, a.detail, a.status_code, a.checked_at, a.last_success_at
        FROM shortcuts s LEFT JOIN shortcut_availability a
        ON a.shortcut_id = s.id AND a.target = s.value
        WHERE s.action = 'open' AND s.availability = 1
        ORDER BY s.position, s.id
        """
        with database() as db:
            return [dict(row) for row in db.execute(select_sql)]

    def schedule(self):
        if not self._closed and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self.refresh_due())
            self._task.add_done_callback(self._finished)

    @staticmethod
    def _finished(task):
        if not task.cancelled() and task.exception() is not None:
            # Never log request URLs, exception text, or response bodies.
            logger.warning("Shortcut availability refresh failed (%s)", type(task.exception()).__name__)

    def snapshot(self):
        rows = self.load()
        if any(age(row["checked_at"], self.now()) >= CHECK_INTERVAL for row in rows):
            self.schedule()
        return {"readings": [dict(row, state=row["state"] or "unknown", detail=row["detail"] or "Waiting for the first check.",
                                  stale=age(row["checked_at"], self.now()) > STALE_AFTER) for row in rows],
                "checking": self._task is not None and not self._task.done()}

    async def run(self):
        try:
            while True:
                self.schedule()
                await asyncio.sleep(CHECK_INTERVAL)
        finally:
            await self.close()

    async def close(self):
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def refresh_due(self):
        rows = [row for row in self.load() if age(row["checked_at"], self.now()) >= CHECK_INTERVAL]
        await asyncio.gather(*(self.check(row) for row in rows))

    def request(self, value, method):
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("Unsupported saved URL")
        # Query strings can contain signed URLs or commands; check the path only.
        url = httpx.URL(value).copy_with(query=None, fragment=None)
        hostname = url.host.lower()
        headers = {"User-Agent": "Clonepage-Availability/1.0", "Accept": "*/*"}
        extensions = {}
        if (self.proxy_ip and self.domain and hostname.endswith("." + self.domain)):
            headers["Host"] = url.netloc.decode("ascii")
            url = url.copy_with(host=self.proxy_ip)
            if parts.scheme == "https":
                extensions["sni_hostname"] = hostname
        # Construct fresh requests directly: no browser/DSM/WUD auth or shared cookies.
        return httpx.Request(method, url, headers=headers, extensions=extensions)

    async def probe(self, value):
        # A redirect is an HTTP response from this address; don't follow it to SSO or another host.
        async with asyncio.timeout(5):
            for method in ("HEAD", "GET"):
                response = await self.client.send(self.request(value, method), stream=True, follow_redirects=False)
                try:
                    status = response.status_code
                finally:
                    await response.aclose()
                if method == "HEAD" and status in (405, 501):
                    continue
                if 200 <= status < 400 or status in (401, 403):
                    detail = f"Responded with HTTP {status}."
                    if status in (401, 403):
                        detail += " Access requires authentication or is restricted."
                    elif 300 <= status < 400:
                        detail += " Redirects are not followed."
                    return "reachable", detail, status
                return "http_error", f"The service responded with HTTP {status}.", status

    async def check(self, row):
        async with self._slots:
            try:
                state, detail, status = await self.probe(row["value"])
            except (TimeoutError, httpx.RequestError) as error:
                state, detail, status = "unreachable", failure_detail(error), None
            except ValueError:
                state, detail, status = "unknown", "The saved website address is not supported.", None
            checked = self.now().isoformat()
            success = checked if state == "reachable" else row["last_success_at"]
            # A URL edited, disabled, or deleted while probing must not receive an old result.
            # language=SQL
            save_sql = """
            INSERT INTO shortcut_availability
                (shortcut_id, target, state, detail, status_code, checked_at, last_success_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(shortcut_id) DO UPDATE SET target = excluded.target,
                state = excluded.state, detail = excluded.detail, status_code = excluded.status_code,
                checked_at = excluded.checked_at, last_success_at = excluded.last_success_at
            """
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT value, action, availability FROM shortcuts WHERE id = ?", (row["id"],)).fetchone()
                if current and current["value"] == row["value"] and current["action"] == "open" and current["availability"]:
                    db.execute(save_sql, (row["id"], row["value"], state, detail, status, checked, success))


@router.get("/availability")
async def availability(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.availability.snapshot()
