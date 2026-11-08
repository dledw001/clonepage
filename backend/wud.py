"""Read-only access to WUD's stored container update results."""

import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/infrastructure", tags=["Infrastructure"])


def text(value):
    return value.strip()[:1000] if isinstance(value, str) else ""


def web_url(value):
    value = text(value)
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment) and not any(char.isspace() for char in value):
            _ = parts.port
            return value.rstrip("/")
    except ValueError:
        pass
    return None


def checked_time(value):
    try:
        moment = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            return moment.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    return None


class ContainerReading(BaseModel):
    id: str
    name: str
    container_name: str
    watcher: str
    project: str | None
    service: str | None
    image: str
    current_tag: str | None
    available_tag: str | None
    current_version: str | None
    available_version: str | None
    current_digest: str | None
    available_digest: str | None
    runtime_status: str
    state: str
    reason: str
    checked_at: str | None
    update_kind: str
    change: str | None


class ContainerSnapshot(BaseModel):
    configured: bool
    containers: list[ContainerReading] = Field(default_factory=list)
    fetched_at: str | None = None
    stale: bool = False
    error: str = ""
    url: str | None = None


def normalize_container(item):
    if not isinstance(item, dict) or not text(item.get("id")) or not text(item.get("name")):
        raise ValueError("Invalid container")
    image = item.get("image")
    if not isinstance(image, dict) or not text(image.get("name")):
        raise ValueError("Invalid image")
    tag = image.get("tag") if isinstance(image.get("tag"), dict) else {}
    digest = image.get("digest") if isinstance(image.get("digest"), dict) else {}
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    kind = item.get("updateKind") if isinstance(item.get("updateKind"), dict) else {}
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    checked_at = checked_time(item.get("checkedAt"))
    state, reason = "unknown", "Waiting for WUD to check this image."
    if item.get("error"):
        # Upstream errors can contain registry details; keep them out of public responses.
        reason = "WUD could not check this image. Open WUD for details."
    elif tag.get("semver") is not True and digest.get("watch") is not True:
        reason = "Version and digest monitoring are disabled for this image in WUD."
    elif tag.get("semver") is not True and not text(digest.get("repo")):
        reason = "No registry digest is available for comparison. The image may be built locally."
    elif not (text(result.get("tag")) if tag.get("semver") is True else text(result.get("digest"))):
        reason = "Waiting for WUD to finish comparing this image with its registry."
    elif item.get("updateAvailable") is True:
        if text(result.get("tag")) or text(result.get("digest")) or text(kind.get("remoteValue")):
            state, reason = "update", "A newer image is available according to WUD's watch settings."
        else:
            reason = "WUD reported an update without candidate image details."
    elif item.get("updateAvailable") is False:
        state, reason = "current", "Current according to WUD's watch settings at the last check."
    project = text(labels.get("com.docker.compose.project")) or text(item.get("stack")) or None
    return ContainerReading(
        id=text(item["id"]), name=text(item.get("displayName")) or text(item["name"]),
        container_name=text(item["name"]), watcher=text(item.get("watcher")) or "default", project=project,
        service=text(labels.get("com.docker.compose.service")) or None, image=text(image["name"]),
        current_tag=text(tag.get("value")) or None, available_tag=text(result.get("tag")) or None,
        current_version=text(image.get("version")) or None, available_version=text(result.get("version")) or None,
        current_digest=text(digest.get("value")) or text(digest.get("repo")) or None,
        available_digest=text(result.get("digest")) or None, runtime_status=text(item.get("status")) or "unknown",
        state=state, reason=reason, checked_at=checked_at,
        update_kind=text(kind.get("kind")) or "unknown", change=text(kind.get("semverDiff")) or None,
    )


class WudGateway:
    def __init__(self, client, url="", token="", public_url="", clock=time.monotonic):
        self.client = client
        self.url = web_url(url)
        self._token = token.strip()
        self.clock = clock
        self._expires = 0.0
        self._lock = asyncio.Lock()
        self._snapshot = ContainerSnapshot(configured=bool(self._token), url=web_url(public_url) or self.url)

    async def snapshot(self):
        if not self._token:
            return self._snapshot
        async with self._lock:
            if self.clock() < self._expires:
                return self._snapshot
            error = ""
            containers = None
            if not self.url:
                error = "The WUD connection address is invalid."
            else:
                try:
                    response = await self.client.get(self.url + "/api/containers", headers={"Authorization": f"Bearer {self._token}"})
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, list):
                        raise ValueError("Expected a list")
                    containers = [normalize_container(item) for item in data]
                    identities = [(item.watcher, item.id) for item in containers]
                    if len(identities) != len(set(identities)):
                        raise ValueError("Duplicate containers")
                except httpx.HTTPStatusError as exc:
                    error = "WUD rejected the connection token. Check its access and expiration." if exc.response.status_code in (401, 403) else "WUD returned an unsuccessful response."
                except httpx.TimeoutException:
                    error = "WUD did not respond in time."
                except httpx.RequestError:
                    error = "Could not reach WUD."
                except (ValueError, TypeError):
                    error = "WUD returned an unsupported response."
            if error:
                self._snapshot = self._snapshot.model_copy(update={"stale": True, "error": error})
            else:
                self._snapshot = ContainerSnapshot(configured=True, containers=containers,
                    fetched_at=datetime.now(timezone.utc).isoformat(), url=self._snapshot.url)
            self._expires = self.clock() + 15
            return self._snapshot


@router.get("/containers", response_model=ContainerSnapshot)
async def containers(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.wud.snapshot()
