"""FRED observations, deterministic economic transformations, and persistent daily cache."""
import asyncio
import calendar
import csv
import io
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix="/api/economy", tags=["Economics"])
DAY = 86400
RETRY_SECONDS = 3600
MAX_BYTES = 2 * 1024 * 1024
FRED = "https://fred.stlouisfed.org"


@dataclass(frozen=True)
class Indicator:
    id: str
    title: str
    group: str
    frequency: str
    source: str
    adjustment: str
    transform: str
    unit: str
    basis: str
    interpretation: str
    methodology: str
    history_limit: int | None = None


INDICATORS = (
    Indicator("UNRATE", "Unemployment rate", "Labor market", "Monthly", "U.S. Bureau of Labor Statistics", "Seasonally adjusted", "unemployment", "%", "Unemployment rate", "A rising rate generally signals a weakening labor market.", "Trend: the latest three-month average minus the preceding three-month average, in percentage points."),
    Indicator("ICSA", "Initial jobless claims", "Labor market", "Weekly", "U.S. Employment and Training Administration", "Seasonally adjusted", "claims", "claims", "Four-week moving average", "Rising claims generally indicate more layoffs and labor-market stress.", "Mean of four consecutive weekly observations. Change compares with the prior week's four-week average."),
    Indicator("PCEPILFE", "Core PCE inflation", "Inflation & growth", "Monthly", "U.S. Bureau of Economic Analysis", "Seasonally adjusted", "yoy", "%", "Year-over-year inflation", "A rising inflation rate means faster price growth; its significance depends on the broader context.", "YoY = 100 × (index / index 12 months earlier − 1). Three-month annualized = 100 × ((index / index 3 months earlier)^4 − 1). Change is YoY inflation versus the prior month."),
    Indicator("GDPC1", "Real GDP growth", "Inflation & growth", "Quarterly", "U.S. Bureau of Economic Analysis", "Seasonally adjusted annual rate", "gdp", "%", "Quarter-over-quarter, annualized", "Rising growth generally indicates stronger economic activity.", "100 × ((real GDP / real GDP one quarter earlier)^4 − 1). The underlying level is already at an annual rate; growth is not multiplied by four again. Change compares growth with the prior quarter."),
    Indicator("IPMAN", "Manufacturing production", "Inflation & growth", "Monthly", "Board of Governors of the Federal Reserve System", "Seasonally adjusted", "yoy", "%", "Year-over-year growth", "Rising growth generally indicates strengthening manufacturing activity.", "100 × (manufacturing industrial production index / index 12 months earlier − 1). Change compares YoY growth with the prior month."),
    Indicator("DGS10", "10-year Treasury yield", "Interest rates & financial conditions", "Daily", "Board of Governors of the Federal Reserve System", "Not seasonally adjusted", "rate30", "%", "Treasury yield", "A rising yield means higher long-term borrowing costs; the economic implications depend on its drivers.", "Current yield minus the closest available yield on or before 30 calendar days earlier, in percentage points."),
    Indicator("MORTGAGE30US", "30-year mortgage rate", "Interest rates & financial conditions", "Weekly", "Freddie Mac", "Not seasonally adjusted", "mortgage", "%", "30-year fixed mortgage average", "A rising rate generally worsens financing affordability.", "Current rate minus the closest available rate on or before three calendar months earlier, in percentage points."),
    Indicator("T10Y2Y", "10Y–2Y Treasury spread", "Interest rates & financial conditions", "Daily", "Federal Reserve Bank of St. Louis", "Not seasonally adjusted", "spread", "bps", "10-year minus 2-year yield", "A rising spread means a steepening yield curve; interpretation depends on what is moving the two yields.", "Published percentage value × 100 gives basis points. Change compares with the closest available observation on or before 30 calendar days earlier."),
    Indicator("SP500", "S&P 500", "Market conditions", "Daily", "S&P Dow Jones Indices LLC", "Not seasonally adjusted", "equity", "index", "Price index · daily close", "The index tracks large-cap U.S. stock prices. Rising prices describe market performance, not the condition of the whole economy.", "30-day price change = 100 × (latest close / last valid close on or before 30 calendar days earlier − 1). Drawdown = 100 × (latest close / highest daily close in the trailing 52 weeks − 1). Dividends are excluded; the high is based on closing values, not intraday prices.", 10),
    Indicator("VIXCLS", "VIX", "Market conditions", "Daily", "Chicago Board Options Exchange", "Not seasonally adjusted", "volatility", "index", "Expected volatility · index level", "VIX reflects options-implied expectations of near-term S&P 500 volatility. A rising level signals greater expected uncertainty; it does not predict market direction.", "The published VIX index level is shown without converting it to a percentage or basis points. The 30-day change subtracts the last valid level on or before 30 calendar days earlier and is expressed in index points."),
    Indicator("BAMLH0A0HYM2", "High-yield credit spread", "Market conditions", "Daily", "ICE Data Indices, LLC", "Not seasonally adjusted", "spread", "bps", "Option-adjusted spread", "A rising spread generally indicates greater perceived credit risk.", "Published percentage value × 100 gives basis points. Change compares with the closest available observation on or before 30 calendar days earlier.", 3),
    Indicator("DSPIC96", "Real disposable income", "Household finances", "Monthly", "U.S. Bureau of Economic Analysis", "Seasonally adjusted annual rate", "yoy", "%", "Year-over-year growth", "Rising real income growth generally indicates improving household purchasing power.", "100 × (real disposable personal income / income 12 months earlier − 1). Change compares YoY growth with the prior month."),
    Indicator("DRCCLACBS", "Credit card delinquency", "Household finances", "Quarterly", "Board of Governors of the Federal Reserve System", "Seasonally adjusted", "delinquency", "%", "Delinquency rate", "A rising rate generally indicates increasing household financial stress.", "Current delinquency rate minus the rate four quarters earlier, in percentage points."),
)


def database():
    from main import database as connect
    return connect()


def initialize_economy():
    with database() as db:
        # language=SQL
        schema_sql = """
        CREATE TABLE IF NOT EXISTS economic_series (
            id TEXT PRIMARY KEY, observations TEXT, fetched_at TEXT,
            fred_updated_at TEXT, checked_at TEXT, next_check_at TEXT,
            error TEXT, metadata_warning TEXT
        )
        """
        db.execute(schema_sql)
        db.executemany("INSERT OR IGNORE INTO economic_series(id) VALUES (?)", [(item.id,) for item in INDICATORS])


def months_before(day, months):
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def growth(current, previous, power=1):
    if current is None or previous is None or current <= 0 or previous <= 0:
        return None
    try:
        result = 100 * ((current / previous) ** power - 1)
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def parse_csv(content, series_id, today):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if reader.fieldnames not in (["observation_date", series_id], ["DATE", series_id]):
        raise ValueError("Unexpected FRED CSV columns")
    rows, seen = [], set()
    for row in reader:
        day = date.fromisoformat(row[reader.fieldnames[0]])
        if day in seen:
            raise ValueError("Duplicate FRED observation date")
        seen.add(day)
        raw = row[series_id]
        value = None if raw in ("", ".") else float(raw)
        if value is not None and not math.isfinite(value):
            raise ValueError("Non-finite FRED observation")
        if day <= today:
            rows.append({"date": day.isoformat(), "value": value})
    if not any(row["value"] is not None for row in rows):
        raise ValueError("No valid FRED observations")
    return sorted(rows, key=lambda row: row["date"])


def fred_timestamp(content):
    soup = BeautifulSoup(content, "html.parser")
    day = soup.select_one(".series-meta-updated-date")
    clock = soup.select_one(".series-meta-updated-time")
    if not day or not clock:
        return None
    text = clock.get_text(" ", strip=True)
    zone = text.rsplit(" ", 1)[-1]
    offsets = {"CST": -6, "CDT": -5}
    if zone not in offsets:
        return None
    try:
        stamp = datetime.strptime(day.get_text(" ", strip=True) + " " + text.rsplit(" ", 1)[0], "%b %d, %Y %I:%M %p")
        return stamp.replace(tzinfo=timezone(timedelta(hours=offsets[zone]))).isoformat()
    except ValueError:
        return None


def transformed(indicator, observations):
    values = {row["date"]: row["value"] for row in observations}
    points = []
    for row in observations:
        day, value = date.fromisoformat(row["date"]), row["value"]
        secondary = None
        if indicator.transform == "yoy":
            if indicator.id == "PCEPILFE":
                secondary = growth(value, values.get(months_before(day, 3).isoformat()), 4)
            value = growth(value, values.get(months_before(day, 12).isoformat()))
        elif indicator.transform == "gdp":
            value = growth(value, values.get(months_before(day, 3).isoformat()), 4)
        elif indicator.transform == "claims":
            sample = [values.get((day - timedelta(weeks=week)).isoformat()) for week in range(4)]
            value = sum(sample) / 4 if all(item is not None for item in sample) else None
        elif indicator.transform == "spread" and value is not None:
            value *= 100
        points.append({"date": row["date"], "value": value, "secondary": secondary})
    return points


def change_for(indicator, latest, points, observations):
    day = date.fromisoformat(latest["date"])
    values = {row["date"]: row["value"] for row in points}
    unit, label, comparison_date, comparison_value = "pp", "vs prior month", None, None
    if indicator.transform == "unemployment":
        raw = {row["date"]: row["value"] for row in observations}
        six = [raw.get(months_before(day, offset).isoformat()) for offset in range(6)]
        delta = (sum(six[:3]) - sum(six[3:])) / 3 if all(value is not None for value in six) else None
        return {"value": delta, "unit": unit, "label": "3-month trend", "comparison_date": months_before(day, 3).isoformat(),
                "current_average": sum(six[:3]) / 3 if all(value is not None for value in six[:3]) else None,
                "previous_average": sum(six[3:]) / 3 if all(value is not None for value in six[3:]) else None}
    if indicator.transform in ("spread", "rate30", "mortgage", "equity", "volatility"):
        target = months_before(day, 3) if indicator.transform == "mortgage" else day - timedelta(days=30)
        previous = next((row for row in reversed(points) if row["date"] <= target.isoformat() and row["value"] is not None), None)
        label = "3-month change" if indicator.transform == "mortgage" else "30-day change"
        unit = {"spread": "bps", "equity": "%", "volatility": "points"}.get(indicator.transform, "pp")
        if indicator.transform == "equity":
            label = "30-day price change"
        if previous:
            comparison_date, comparison_value = previous["date"], previous["value"]
    else:
        if indicator.transform == "claims":
            comparison_date, unit, label = (day - timedelta(weeks=1)).isoformat(), "claims", "vs prior week"
        elif indicator.transform == "gdp":
            comparison_date, label = months_before(day, 3).isoformat(), "vs prior quarter"
        elif indicator.transform == "delinquency":
            comparison_date, label = months_before(day, 12).isoformat(), "year-over-year change"
        else:
            comparison_date = months_before(day, 1).isoformat()
        comparison_value = values.get(comparison_date)
    delta = (growth(latest["value"], comparison_value) if indicator.transform == "equity"
             else latest["value"] - comparison_value if comparison_value is not None else None)
    return {"value": delta, "unit": unit, "label": label, "comparison_date": comparison_date}


def equity_drawdown(latest, points):
    """Drawdown from daily closing highs; require coverage of the full window."""
    if latest is None or latest["value"] <= 0:
        return None
    cutoff = (date.fromisoformat(latest["date"]) - timedelta(weeks=52)).isoformat()
    valid = [point for point in points if point["value"] is not None and point["value"] > 0 and point["date"] <= latest["date"]]
    if not any(point["date"] <= cutoff for point in valid):
        return None
    window = [point for point in valid if point["date"] >= cutoff]
    peak = max(window, key=lambda point: (point["value"], point["date"]))
    return {"value": growth(latest["value"], peak["value"]), "high": peak["value"],
            "high_date": peak["date"], "window_start": cutoff}


def present(indicator, row, now):
    today = now.date()
    observations = json.loads(row["observations"]) if row.get("observations") else []
    # Do not accumulate ICE observations beyond FRED's rolling history allowance.
    if indicator.history_limit:
        cutoff = months_before(today, indicator.history_limit * 12).isoformat()
        observations = [point for point in observations if point["date"] >= cutoff]
    points = transformed(indicator, observations)
    latest = next((point for point in reversed(points) if point["value"] is not None), None)
    change = change_for(indicator, latest, points, observations) if latest else None
    cutoff = months_before(today, 60).isoformat()
    history = [point for point in points if point["date"] >= cutoff]
    return {"id": indicator.id, "title": indicator.title, "group": indicator.group, "frequency": indicator.frequency,
            "source": indicator.source, "adjustment": indicator.adjustment, "unit": indicator.unit, "basis": indicator.basis,
            "interpretation": indicator.interpretation, "methodology": indicator.methodology, "history_limit_years": indicator.history_limit,
            "source_url": FRED + "/series/" + indicator.id, "latest": latest, "change": change,
            "drawdown": equity_drawdown(latest, points) if indicator.transform == "equity" else None,
            "points": history, "checked_at": row.get("checked_at"), "fetched_at": row.get("fetched_at"),
            "fred_updated_at": row.get("fred_updated_at"), "next_check_at": row.get("next_check_at"),
            "stale": bool(row.get("error")) or not row.get("fetched_at") or (now - datetime.fromisoformat(row["fetched_at"])).total_seconds() >= DAY,
            "error": row.get("error"), "metadata_warning": row.get("metadata_warning")}


class EconomyGateway:
    def __init__(self, client, now=lambda: datetime.now(timezone.utc)):
        self.client, self.now = client, now
        self.task = None
        self.closed = False
        self.limit = asyncio.Semaphore(2)

    async def read(self, url, params=None):
        async with self.client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("FRED redirect or empty response")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("Oversized FRED response")
            return bytes(content)

    async def collect(self, indicator):
        async with self.limit:
            stamp = self.now()
            try:
                content = await self.read(FRED + "/graph/fredgraph.csv", {"id": indicator.id})
                observations = parse_csv(content, indicator.id, stamp.date())
                cutoff = months_before(stamp.date(), 73).isoformat()
                observations = [point for point in observations if point["date"] >= cutoff]
                updated = None
                try:
                    updated = fred_timestamp(await self.read(FRED + "/series/" + indicator.id))
                except (httpx.HTTPError, ValueError):
                    pass
                # Replace the whole retained history: published revisions must win.
                with database() as db:
                    # language=SQL
                    save_sql = """
                    UPDATE economic_series SET observations=?,fetched_at=?,checked_at=?,fred_updated_at=?,
                        next_check_at=?,error=NULL,metadata_warning=? WHERE id=?
                    """
                    db.execute(save_sql, (json.dumps(observations, allow_nan=False), stamp.isoformat(), stamp.isoformat(), updated,
                               (stamp + timedelta(seconds=DAY)).isoformat(), None if updated else "FRED publication timestamp is unavailable.", indicator.id))
            except (httpx.HTTPError, ValueError, TypeError, KeyError, UnicodeError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    error = f"FRED returned HTTP {exc.response.status_code}."
                elif isinstance(exc, httpx.TimeoutException):
                    error = "FRED did not respond in time."
                else:
                    error = "FRED observations could not be refreshed."
                with database() as db:
                    db.execute("UPDATE economic_series SET checked_at=?,next_check_at=?,error=? WHERE id=?",
                               (stamp.isoformat(), (stamp + timedelta(seconds=RETRY_SECONDS)).isoformat(), error, indicator.id))

    async def refresh_due(self):
        with database() as db:
            due = {row["id"] for row in db.execute("SELECT id FROM economic_series WHERE next_check_at IS NULL OR next_check_at<=?", (self.now().isoformat(),))}
        await asyncio.gather(*(self.collect(item) for item in INDICATORS if item.id in due))

    def start_due(self):
        if not self.closed and (self.task is None or self.task.done()):
            with database() as db:
                due = db.execute("SELECT 1 FROM economic_series WHERE next_check_at IS NULL OR next_check_at<=? LIMIT 1", (self.now().isoformat(),)).fetchone()
            if due is None:
                return
            self.task = asyncio.create_task(self.refresh_due())
            self.task.add_done_callback(self.finished)

    @staticmethod
    def finished(task):
        if not task.cancelled() and task.exception():
            logging.error("Economic cache refresh failed", exc_info=task.exception())

    async def snapshot(self):
        self.start_due()
        with database() as db:
            rows = {row["id"]: dict(row) for row in db.execute("SELECT * FROM economic_series")}
        return {"indicators": [present(item, rows.get(item.id, {}), self.now()) for item in INDICATORS],
                "refreshing": self.task is not None and not self.task.done(), "as_of": self.now().isoformat()}

    async def run(self):
        # Lazy requests can start the first refresh immediately. The background
        # loop also checks every minute when nobody has the dashboard open.
        while True:
            await asyncio.sleep(60)
            self.start_due()

    async def close(self):
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


@router.get("")
async def economy(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.economy.snapshot()
