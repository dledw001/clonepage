"""Read-only, same-origin access to the NAS Temps collector."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, FiniteFloat, TypeAdapter, ValidationError


class Metric(BaseModel):
    key: str
    label: str
    unit: str


class Reading(BaseModel):
    metric: str
    ts: int = Field(ge=0)
    value: FiniteFloat
    unit: str


class Device(BaseModel):
    label: str
    device: str
    present: bool


class CollectorHealth(BaseModel):
    ok: bool
    smartctl_available: bool | None = None
    smart_devices: list[Device] = Field(default_factory=list)
    last_collection_ts: int | None = None
    last_smart_poll_ts: int | None = None


class Snapshot(BaseModel):
    metrics: list[Metric] = Field(default_factory=list)
    readings: list[Reading] = Field(default_factory=list)
    health: CollectorHealth | None = None
    errors: dict[str, str] = Field(default_factory=dict)


class Point(BaseModel):
    ts: int = Field(ge=0)
    value: FiniteFloat | None
    min: FiniteFloat | None = None
    max: FiniteFloat | None = None
    samples: int | None = None


class Series(BaseModel):
    metric: str
    unit: str
    points: list[Point]


METRICS = TypeAdapter(list[Metric])
READINGS = TypeAdapter(list[Reading])


class NasTempsGateway:
    def __init__(self, client: httpx.AsyncClient, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.clock = clock
        self._snapshot = Snapshot()
        self._snapshot_expires = 0.0
        self._snapshot_lock = asyncio.Lock()
        self._series: OrderedDict[tuple, tuple[float, Series]] = OrderedDict()

    async def _read(self, path, adapter, params=None):
        try:
            response = await self.client.get(path, params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(adapter, TypeAdapter):
                return adapter.validate_python(data)
            return adapter.model_validate(data)
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "NAS Temps did not respond in time.") from exc
        except httpx.RequestError as exc:
            raise HTTPException(502, "Could not reach NAS Temps.") from exc
        except (httpx.HTTPStatusError, ValidationError, ValueError) as exc:
            raise HTTPException(502, "NAS Temps returned an invalid response.") from exc

    async def snapshot(self) -> Snapshot:
        async with self._snapshot_lock:
            if self.clock() < self._snapshot_expires:
                return self._snapshot

            results = await asyncio.gather(
                self._read("api/metrics", METRICS),
                self._read("api/latest", READINGS),
                self._read("api/health", CollectorHealth),
                return_exceptions=True,
            )
            values = self._snapshot.model_dump()
            errors = {}
            for name, result in zip(("metrics", "readings", "health"), results):
                if isinstance(result, Exception):
                    errors[name] = result.detail if isinstance(result, HTTPException) else "Could not read NAS Temps."
                else:
                    values[name] = result
            values["errors"] = errors
            # Keep original reading timestamps when retaining last-known data.
            self._snapshot = Snapshot.model_validate(values)
            self._snapshot_expires = self.clock() + 5
            return self._snapshot

    async def series(self, metric: str, since_seconds: int, max_points: int) -> Series:
        key = (metric, since_seconds, max_points)
        cached = self._series.get(key)
        if cached and self.clock() < cached[0]:
            self._series.move_to_end(key)
            return cached[1]

        result = await self._read("api/series", Series, {
            "metric": metric,
            "since_seconds": since_seconds,
            "max_points": max_points,
        })
        if result.metric != metric:
            raise HTTPException(502, "NAS Temps returned history for a different metric.")
        self._series[key] = (self.clock() + 15, result)
        self._series.move_to_end(key)
        while len(self._series) > 64:
            self._series.popitem(last=False)
        return result


router = APIRouter(prefix="/api/infrastructure", tags=["infrastructure"])


@router.get("/snapshot", response_model=Snapshot)
async def snapshot(request: Request):
    return await request.app.state.nas_temps.snapshot()


@router.get("/series", response_model=Series)
async def series(
    request: Request,
    metric: str = Query(..., min_length=1, max_length=160),
    since_seconds: int = Query(3600, ge=60, le=31536000),
    max_points: int = Query(360, ge=100, le=5000),
):
    return await request.app.state.nas_temps.series(metric, since_seconds, max_points)
