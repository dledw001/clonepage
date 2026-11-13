"""Small, cached same-origin adapter for wttr.in's JSON forecast."""
import asyncio
import math
import json
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter(prefix='/api/home', tags=['Weather'])
CACHE_SECONDS = 900
RETRY_SECONDS = 60


def text(values):
    if isinstance(values, list) and values and isinstance(values[0], dict):
        value = values[0].get('value')
        if isinstance(value, str):
            return value.strip()[:160] or None
    return None


def number(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and low <= result <= high else None
    except (ValueError, TypeError, OverflowError):
        return None


def temperature(celsius, fahrenheit):
    c = number(celsius, -100, 100)
    f = number(fahrenheit, -148, 212)
    if c is None and f is not None:
        c = (f-32)*5/9
    if f is None and c is not None:
        f = c*9/5+32
    return {'c': c, 'f': f}


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError('Invalid weather payload')
    readings = payload.get('current_condition')
    if not isinstance(readings, list) or not readings or not isinstance(readings[0], dict):
        raise ValueError('Missing current conditions')
    current = readings[0]
    temp = temperature(current.get('temp_C'), current.get('temp_F'))
    if temp['c'] is None:
        raise ValueError('Missing temperature')
    areas = payload.get('nearest_area')
    area = areas[0] if isinstance(areas, list) and areas and isinstance(areas[0], dict) else {}
    city, region, country = text(area.get('areaName')), text(area.get('region')), text(area.get('country'))
    forecast = []
    days = payload.get('weather', [])
    for day in days if isinstance(days, list) else []:
        if not isinstance(day, dict):
            continue
        try:
            stamp = date.fromisoformat(day.get('date', '')).isoformat()
        except (TypeError, ValueError):
            continue
        if any(item['date'] == stamp for item in forecast):
            continue
        hours = [hour for hour in day.get('hourly', []) if isinstance(hour, dict)] if isinstance(day.get('hourly'), list) else []
        rains = [number(hour.get('chanceofrain'), 0, 100) for hour in hours]
        timed = [(number(hour.get('time'), 0, 2300), hour) for hour in hours]
        timed = [(stamp, hour) for stamp, hour in timed if stamp is not None]
        noon = min(timed, key=lambda pair: abs(pair[0]-1200))[1] if timed else {}
        forecast.append({'date': stamp, 'high': temperature(day.get('maxtempC'), day.get('maxtempF')),
                         'low': temperature(day.get('mintempC'), day.get('mintempF')),
                         'description': text(noon.get('weatherDesc')), 'code': str(noon.get('weatherCode', ''))[:8],
                         'rain_chance': max((rain for rain in rains if rain is not None), default=None)})
    forecast.sort(key=lambda day: day['date'])
    return {'place': ', '.join(part for part in (city, region) if part), 'country': country,
            'current': {'temperature': temp, 'feels_like': temperature(current.get('FeelsLikeC'), current.get('FeelsLikeF')),
                        'description': text(current.get('weatherDesc')) or 'Conditions unavailable',
                        'code': str(current.get('weatherCode', ''))[:8],
                        'wind_kph': number(current.get('windspeedKmph'), 0, 500),
                        'wind_mph': number(current.get('windspeedMiles'), 0, 310),
                        'wind_direction': str(current.get('winddir16Point', ''))[:4],
                        'humidity': number(current.get('humidity'), 0, 100)},
            'forecast': forecast[:3]}


def location_query(location):
    # Bare five-digit codes are ambiguous internationally (22206 resolves to
    # South Korea without qualification). The location field labels these US ZIPs.
    return f'{location}, United States' if re.fullmatch(r'\d{5}(?:-\d{4})?', location) else location


class WeatherGateway:
    def __init__(self, client, clock=time.monotonic):
        self.client, self.clock = client, clock
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()

    async def snapshot(self, location):
        async with self.lock:
            cached = self.cache.get(location)
            if cached and self.clock() < cached[0]:
                self.cache.move_to_end(location)
                return dict(cached[1])
            previous = cached[1] if cached else {'location': location, 'current': None, 'forecast': [], 'place': '', 'country': None, 'fetched_at': None}
            try:
                url = 'https://wttr.in/' + quote(location_query(location), safe='')
                async with self.client.stream('GET', url, params={'format': 'j1', 'lang': 'en'}) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2 * 1024 * 1024:
                            raise ValueError('Oversized weather response')
                    normalized = normalize(json.loads(content))
                result = {**normalized, 'location': location, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'stale': False, 'error': None}
                ttl = CACHE_SECONDS
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                result = {**previous, 'stale': True, 'error': 'Weather could not be refreshed from wttr.in. Try again shortly.'}
                ttl = RETRY_SECONDS
            self.cache[location] = (self.clock()+ttl, result)
            self.cache.move_to_end(location)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return dict(result)


@router.get('/weather')
async def weather(request: Request, response: Response):
    from home import database, layout_snapshot
    with database() as db:
        settings = layout_snapshot(db)['settings']['weather']
    response.headers['Cache-Control'] = 'no-store'
    return {**await request.app.state.weather.snapshot(settings['location']), 'units': settings['units']}
