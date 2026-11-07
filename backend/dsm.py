"""Short-lived, browser-bound DSM sessions. No credentials are persisted."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

router = APIRouter(prefix="/api/dsm", tags=["DSM services"])
COOKIE = "__Host-clonepage-dsm"
AUTH = "SYNO.API.Auth"
PROXY = "SYNO.Core.AppPortal.ReverseProxy"
CERT = "SYNO.Core.Certificate.CRT"
CERT_SERVICE = "SYNO.Core.Certificate.Service"
IDLE_SECONDS = 300
MAX_SECONDS = 900


@dataclass(frozen=True)
class DSMConfig:
    origin: str
    url: str
    domain: str
    upstream_hosts: frozenset[str]

    @classmethod
    def from_env(cls):
        origin = os.getenv("CLONEPAGE_ORIGIN", "https://clonepage.dledwith.com").rstrip("/")
        url = os.getenv("DSM_URL", "https://dsm.dledwith.com").rstrip("/")
        domain = os.getenv("DSM_SERVICE_DOMAIN", "dledwith.com").lower().strip()
        hosts = frozenset(v.strip().lower() for v in os.getenv("DSM_UPSTREAM_HOSTS", "10.0.0.149,127.0.0.1,localhost").split(",") if v.strip())
        for value in (origin, url):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("DSM_URL and CLONEPAGE_ORIGIN must be HTTPS origins without paths or credentials.")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or ".." in domain or not hosts:
            raise ValueError("Configure a service domain and allowed upstream hosts.")
        return cls(origin, url, domain, hosts)


class DSMError(Exception):
    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class DSMClient:
    def __init__(self, config: DSMConfig, client=None):
        self.config = config
        self.http = client or httpx.AsyncClient(base_url=config.url, timeout=httpx.Timeout(12, connect=4),
                                                follow_redirects=False, trust_env=False)
        self.sid = None
        self.token = None

    async def call(self, api, method, **parameters):
        payload = {"api": api, "version": "6" if api == AUTH else "1", "method": method, **parameters}
        if self.sid:
            payload["_sid"] = self.sid
        headers = {"X-SYNO-TOKEN": self.token} if self.token else {}
        try:
            response = await self.http.post("/webapi/entry.cgi", data=payload, headers=headers)
            if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
                raise DSMError("DSM did not return a usable response.")
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise DSMError("Could not reach DSM over verified HTTPS. Check the configured DSM address.") from None
        if not isinstance(result, dict) or result.get("success") is not True:
            code = result.get("error", {}).get("code") if isinstance(result, dict) and isinstance(result.get("error", {}), dict) else None
            if api == AUTH and method == "login":
                if code in (403, 406):
                    raise DSMError("Enter your password again with an authenticator code. Nothing has been saved.", 401, code)
                if code == 404:
                    raise DSMError("The authenticator code was not accepted. Enter your password and a fresh code.", 401, code)
                raise DSMError("DSM sign-in failed. Check your credentials and account status.", 401, code)
            if code in (106, 107, 119):
                raise DSMError("Your DSM session expired. Connect again to continue.", 401, code)
            if code in (105, 402):
                raise DSMError("This DSM account does not have permission for this operation.", 403, code)
            raise DSMError(f"DSM could not complete this operation (code {code if isinstance(code, int) else 'unknown'}).", 502, code)
        return result.get("data", {})

    async def login(self, username, password, otp):
        info = await self.call("SYNO.API.Info", "query", query=",".join([AUTH, PROXY, CERT, CERT_SERVICE]))
        if not isinstance(info, dict):
            raise DSMError("DSM returned an unsupported API discovery response.")
        for api in (AUTH, PROXY, CERT, CERT_SERVICE):
            entry = info.get(api, {})
            version = 6 if api == AUTH else 1
            if entry.get("path") != "entry.cgi" or not entry.get("minVersion", 99) <= version <= entry.get("maxVersion", 0):
                raise DSMError("DSM does not expose the required service APIs in a supported format.")
        params = dict(account=username, passwd=password, session="Core", format="sid", enable_syno_token="yes")
        if otp:
            params["otp_code"] = otp
        try:
            result = await self.call(AUTH, "login", **params)
        finally:
            params.clear()
        if not isinstance(result, dict):
            raise DSMError("DSM returned an unsupported sign-in response.")
        sid = result.get("sid")
        token = result.get("synotoken")
        self.sid = sid if isinstance(sid, str) and sid else None
        self.token = token if isinstance(token, str) and token else None
        if not self.sid or not self.token:
            raise DSMError("DSM did not provide the session protection required by Clonepage.")

    async def rules(self):
        data = await self.call(PROXY, "list")
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise DSMError("DSM returned an unsupported reverse-proxy list. No rules were changed.")
        return entries

    async def certificates(self):
        data = await self.call(CERT, "list")
        entries = data.get("certificates") if isinstance(data, dict) else None
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise DSMError("DSM returned an unsupported certificate list. No certificates were changed.")
        return entries

    async def close(self):
        try:
            if self.sid:
                await self.call(AUTH, "logout", session="Core")
        except (DSMError, httpx.HTTPError):
            pass
        finally:
            self.sid = None
            self.token = None
            self.http.cookies.clear()
            await self.http.aclose()


@dataclass
class BrowserSession:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    client: DSMClient | None = field(default=None, repr=False)
    preview: dict | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def remaining(self):
        now = time.monotonic()
        return max(0, min(MAX_SECONDS - (now - self.created), IDLE_SECONDS - (now - self.touched)))


class DSMSessions:
    def __init__(self, config=None, client_factory=None):
        self.config = config or DSMConfig.from_env()
        self.client_factory = client_factory or (lambda: DSMClient(self.config))
        self.sessions = {}
        self.attempts = {}
        self.provision_lock = asyncio.Lock()

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    async def remove(self, key):
        session = self.sessions.pop(key, None)
        if session and session.client:
            client = session.client; session.client = None
            await client.close()

    async def cleanup(self):
        for key, session in list(self.sessions.items()):
            if not session.lock.locked() and session.remaining() <= 0:
                await self.remove(key)
        now = time.monotonic()
        self.attempts = {key: [at for at in values if now - at < 300] for key, values in self.attempts.items() if any(now - at < 300 for at in values)}

    async def shutdown(self):
        for key in list(self.sessions):
            await self.remove(key)

    def check_origin(self, request, mutate=False):
        origin = self.config.origin
        if request.headers.get("host", "").lower() != urlsplit(origin).netloc.lower():
            raise HTTPException(403, "Open Clonepage at its configured HTTPS address to connect to DSM.")
        supplied = request.headers.get("origin")
        if supplied and supplied != origin:
            raise HTTPException(403, "This DSM request did not come from Clonepage.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "This DSM request did not come from Clonepage.")
        if mutate and (supplied != origin or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"):
            raise HTTPException(403, "DSM changes require a protected request from Clonepage.")

    async def get(self, request, mutate=False, connected=False):
        self.check_origin(request, mutate)
        token = request.cookies.get(COOKIE, "")
        key = self.key(token)
        session = self.sessions.get(key)
        if not session or session.remaining() <= 0:
            if session and not session.lock.locked():
                await self.remove(key)
            raise HTTPException(401, "Your DSM connection expired. Connect again to continue.")
        if mutate and not secrets.compare_digest(request.headers.get("x-clonepage-csrf", ""), session.csrf):
            raise HTTPException(403, "The DSM request could not be verified. Reopen Add service and try again.")
        if connected and not session.client:
            raise HTTPException(401, "Connect to DSM to continue.")
        return key, session

    def rate_limit(self, key, request):
        now = time.monotonic()
        source = f"ip:{request.client.host if request.client else 'unknown'}"
        for bucket, limit in ((key, 5), (source, 12)):
            attempts = [at for at in self.attempts.get(bucket, []) if now - at < 300]
            if len(attempts) >= limit:
                raise HTTPException(429, "Too many sign-in attempts. Wait five minutes before trying again.")
            self.attempts[bucket] = attempts
        self.attempts[key].append(now); self.attempts[source].append(now)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)
    otp: SecretStr = Field(default=SecretStr(""), max_length=16)


def manager(request):
    return request.app.state.dsm_sessions


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"


@router.get("/connection")
async def connection(request: Request, response: Response):
    sessions = manager(request); sessions.check_origin(request)
    await sessions.cleanup()
    token = request.cookies.get(COOKIE, ""); key = sessions.key(token)
    session = sessions.sessions.get(key)
    if not session:
        if len(sessions.sessions) >= 64:
            raise HTTPException(429, "Too many open setup sessions. Try again shortly.")
        token = secrets.token_urlsafe(32); key = sessions.key(token)
        session = BrowserSession(); sessions.sessions[key] = session
    response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite="strict", path="/", max_age=int(session.remaining()))
    no_store(response)
    return {"connected": bool(session.client), "csrf": session.csrf, "expires_in": int(session.remaining()),
            "dsm_url": sessions.config.url, "domain": sessions.config.domain, "upstream_hosts": sorted(sessions.config.upstream_hosts)}


@router.post("/connection")
async def connect(request: Request, response: Response):
    sessions = manager(request); key, session = await sessions.get(request, mutate=True)
    sessions.rate_limit(key, request)
    # Manually validate this one body: FastAPI's standard validation errors can echo raw passwords.
    if len(await request.body()) > 4096:
        raise HTTPException(400, "The sign-in request is too large.")
    try:
        credentials = LoginInput.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(400, "Enter a username, password, and optional authenticator code.") from None
    if session.lock.locked():
        raise HTTPException(409, "A DSM operation is already in progress.")
    async with session.lock:
        session.preview = None
        if session.client:
            await session.client.close(); session.client = None
        client = sessions.client_factory()
        try:
            await client.login(credentials.username, credentials.password.get_secret_value(), credentials.otp.get_secret_value())
            del credentials
            await client.rules(); await client.certificates()
            session.client = client; session.touched = time.monotonic()
        except DSMError as error:
            await client.close()
            raise HTTPException(error.status, str(error)) from None
        except BaseException:
            await client.close()
            raise
    response.set_cookie(COOKIE, request.cookies[COOKIE], secure=True, httponly=True, samesite="strict", path="/", max_age=int(session.remaining()))
    no_store(response)
    return {"connected": True, "expires_in": int(session.remaining())}


@router.post("/disconnect")
async def disconnect(request: Request, response: Response):
    sessions = manager(request); key, session = await sessions.get(request, mutate=True)
    if session.lock.locked():
        raise HTTPException(409, "Wait for the current DSM operation to finish before disconnecting.")
    await sessions.remove(key)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    no_store(response)
    return {"connected": False}
