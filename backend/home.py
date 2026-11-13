"""Shared homepage preferences, continue-reading items, and an attention summary."""
import json
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

router = APIRouter(prefix='/api/home', tags=['Homepage'])
SECTION_IDS = ('attention', 'shortcuts', 'continue', 'reading', 'feeds', 'hacker-news')


def database():
    from main import database as connect
    return connect()


class Section(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Literal['attention', 'shortcuts', 'continue', 'reading', 'feeds', 'hacker-news']
    visible: StrictBool = True


class Previews(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reading: Literal[3, 5, 10] = 3
    feeds: Literal[3, 5, 10] = 3
    hacker_news: Literal[3, 5, 10] = 3
    continue_reading: Literal[3, 5, 10] = 3


class HNPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    view: Literal['front', 'top', 'discussed'] = 'top'
    window: Literal['24h', '7d', '30d'] = '24h'


class HomeSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    show_clock: StrictBool = True
    sections: list[Section] = Field(default_factory=lambda: [Section(id=key) for key in SECTION_IDS])
    previews: Previews = Field(default_factory=Previews)
    hacker_news: HNPreview = Field(default_factory=HNPreview)

    @model_validator(mode='after')
    def complete_sections(self):
        if len(self.sections) != len(SECTION_IDS) or {section.id for section in self.sections} != set(SECTION_IDS):
            raise ValueError('Include every homepage section exactly once.')
        return self


class HomeUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, le=9007199254740991, strict=True)
    settings: HomeSettings


def initialize_home():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS home_settings (
        id INTEGER PRIMARY KEY CHECK(id=1),
        revision INTEGER NOT NULL,
        payload TEXT NOT NULL
    )
    """
    with database() as db:
        db.execute(schema_sql)
        db.execute('INSERT OR IGNORE INTO home_settings(id,revision,payload) VALUES (1,0,?)', (HomeSettings().model_dump_json(),))


def layout_snapshot(db):
    row = db.execute('SELECT revision,payload FROM home_settings WHERE id=1').fetchone()
    return {'revision': row['revision'], 'settings': HomeSettings.model_validate_json(row['payload']).model_dump()}


@router.get('/layout')
def get_layout(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    with database() as db:
        return layout_snapshot(db)


@router.put('/layout')
def save_layout(update: HomeUpdate, response: Response):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT revision FROM home_settings WHERE id=1').fetchone()
        if current['revision'] != update.revision:
            raise HTTPException(409, 'Your homepage layout changed in another session. Reload the latest layout before saving.')
        db.execute('UPDATE home_settings SET revision=revision+1,payload=? WHERE id=1', (update.settings.model_dump_json(),))
        result = layout_snapshot(db)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/continue')
def continue_reading(response: Response):
    from main import ARTICLE_COLUMNS, serialize
    response.headers['Cache-Control'] = 'no-store'
    with database() as db:
        rows = db.execute(f'SELECT {ARTICLE_COLUMNS} FROM articles WHERE archived_at IS NULL AND is_read=0 AND copy_saved_at IS NOT NULL').fetchall()
    items = []
    for row in rows:
        article = serialize(row)
        pdf, html = article.get('reading_progress'), article.get('html_reading_progress')
        if pdf and (pdf['page'] > 1 or abs(pdf.get('top', 0)) > 0.01):
            items.append({'id': article['id'], 'title': article['title'], 'format': 'pdf', 'label': f"Page {pdf['page']} of {pdf['page_count']}", 'updated_at': pdf['updated_at']})
        elif html and 0.02 < html['fraction'] < 0.98:
            items.append({'id': article['id'], 'title': article['title'], 'format': 'html', 'label': f"{round(html['fraction']*100)}% through", 'updated_at': html['updated_at']})
    items.sort(key=lambda item: item['updated_at'] or '', reverse=True)
    return {'items': items[:50]}


def monitor_href(item):
    target = {'kind': 'container', 'watcher': item.watcher, 'project': item.project,
              'service': item.service if item.project and item.service else None,
              'container': None if item.project and item.service else item.container_name}
    return '/infrastructure?monitor=' + quote(json.dumps(target, separators=(',', ':')), safe='') + '#container-updates'


@router.get('/attention')
async def needs_attention(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    items = []
    def add(key, kind, title, detail, href, count=1):
        items.append({'id': key, 'kind': kind, 'title': title, 'detail': detail, 'href': href, 'count': count})

    availability = request.app.state.availability.snapshot()
    with database() as db:
        names = {row['id']: row['name'] for row in db.execute('SELECT id,name FROM shortcuts')}
        feeds = [dict(row) for row in db.execute("SELECT id,title,error,last_success_at FROM feed_subscriptions WHERE error IS NOT NULL AND error!='' ORDER BY title")]
        copies = [dict(row) for row in db.execute("SELECT id,title,copy_error,copy_saved_at FROM articles WHERE copy_status='failed' AND archived_at IS NULL ORDER BY saved_at DESC")]
    waiting = 0
    for reading in availability['readings']:
        if reading['stale'] or reading['state'] == 'unknown':
            waiting += 1
        elif reading['state'] in ('http_error', 'unreachable'):
            name = names.get(reading['id'], 'Service')
            suffix = 'unreachable' if reading['state'] == 'unreachable' else f"HTTP {reading['status_code'] or 'error'}"
            add('service:'+reading['id'], 'service', f'{name}: {suffix}', reading['detail'], reading['value'])
    if waiting:
        add('service-checks', 'check', 'Service checks are incomplete', 'Some checks are pending or too old to report a current status.', '/?section=shortcuts#home-shortcuts', waiting)

    wud = await request.app.state.wud.snapshot()
    if wud.configured:
        if wud.stale or wud.error:
            add('wud-unavailable', 'check', 'Container monitoring is unavailable', wud.error or 'Waiting for fresh results from WUD.', '/infrastructure#container-updates')
        else:
            for container in wud.containers:
                if container.state == 'update':
                    add('update:'+container.watcher+':'+container.id, 'update', f'{container.name}: update available', 'Reported by WUD; review compatibility before updating.', monitor_href(container))
                elif container.state == 'unknown':
                    add('check:'+container.watcher+':'+container.id, 'check', f'{container.name}: check incomplete', container.reason, monitor_href(container))
    for feed in feeds:
        add('feed:'+feed['id'], 'feed', f"{feed['title']}: feed refresh failed", feed['error'], '/?section=feeds#home-feeds')
    for article in copies:
        title = 'Saved-copy refresh failed' if article['copy_saved_at'] else 'Article could not be saved offline'
        add('copy:'+article['id'], 'copy', article['title'], f"{title}. {article['copy_error'] or ''}".strip(), '/reading/'+article['id'])
    counts = {kind: sum(item['count'] for item in items if item['kind'] == kind) for kind in ('service','update','check','feed','copy')}
    return {'items': items[:100], 'counts': counts, 'total': sum(counts.values()), 'checked_at': datetime.now(timezone.utc).isoformat()}
