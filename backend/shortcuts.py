"""Shared, editable homepage shortcuts backed by the existing SQLite database."""

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

router = APIRouter(prefix="/api/shortcuts", tags=["Shortcuts"])
CATALOG = json.loads(Path(__file__).with_name("shortcut_catalog.json").read_text(encoding="utf-8"))
ICON_KEYS = {item["key"] for item in CATALOG["icons"]}
MAX_SHORTCUTS = 100


def database():
    from main import database as connect
    return connect()


def initialize_shortcuts():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS shortcuts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        aliases TEXT NOT NULL,
        icon TEXT NOT NULL,
        color TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('open', 'copy')),
        value TEXT NOT NULL,
        position INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS shortcut_settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        revision INTEGER NOT NULL
    );
    """
    # language=SQL
    insert_sql = """
    INSERT INTO shortcuts (id, name, description, aliases, icon, color, action, value, position)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with database() as db:
        db.executescript(schema_sql)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM shortcut_settings WHERE id = 1").fetchone():
            return
        defaults = json.loads(Path(__file__).with_name("shortcut_defaults.json").read_text(encoding="utf-8"))
        for position, item in enumerate(defaults):
            validated = ShortcutFields.model_validate({key: value for key, value in item.items() if key != "id"})
            db.execute(insert_sql, (item["id"], *values(validated), position))
        # This marker remains even if every shortcut is deleted.
        db.execute("INSERT INTO shortcut_settings (id, revision) VALUES (1, 0)")


class ShortcutFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    icon: str
    color: str
    action: Literal["open", "copy"]
    value: str = Field(min_length=1, max_length=4000)

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, aliases):
        result = []
        for alias in aliases:
            alias = alias.strip().lower()
            if not alias:
                continue
            if len(alias) > 80 or any(ord(char) < 32 for char in alias):
                raise ValueError("Search aliases must be at most 80 characters without control characters.")
            if alias not in result:
                result.append(alias)
        return result

    @field_validator("icon")
    @classmethod
    def valid_icon(cls, value):
        if value not in ICON_KEYS:
            raise ValueError("Choose an available icon.")
        return value

    @field_validator("color")
    @classmethod
    def valid_color(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Use a six-digit hex color, such as #8fb4df.")
        return value.lower()

    @model_validator(mode="after")
    def valid_target(self):
        if self.action == "open":
            try:
                HttpUrl(self.value)
                parts = urlsplit(self.value)
                _ = parts.port
                if (parts.scheme.lower() not in ("http", "https") or not parts.hostname
                        or parts.username is not None or parts.password is not None
                        or re.search(r"[\s\\]", self.value)):
                    raise ValueError()
            except ValueError:
                raise ValueError("Enter a complete http:// or https:// website URL without embedded credentials.")
        return self


class ShortcutWrite(ShortcutFields):
    revision: int = Field(ge=0)


class ShortcutOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    ids: list[str] = Field(max_length=MAX_SHORTCUTS)


def values(item):
    return (item.name, item.description, json.dumps(item.aliases), item.icon, item.color, item.action, item.value)


def snapshot(db):
    rows = db.execute("SELECT * FROM shortcuts ORDER BY position, id").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["aliases"] = json.loads(item["aliases"])
        item.pop("position")
        items.append(item)
    return {"items": items, "revision": db.execute("SELECT revision FROM shortcut_settings WHERE id = 1").fetchone()[0]}


def check_revision(db, revision):
    db.execute("BEGIN IMMEDIATE")
    current = db.execute("SELECT revision FROM shortcut_settings WHERE id = 1").fetchone()[0]
    if current != revision:
        raise HTTPException(409, "Shortcuts changed on another device. Your draft is kept; review the latest shortcuts and save again.")


def changed(db):
    db.execute("UPDATE shortcut_settings SET revision = revision + 1 WHERE id = 1")
    return snapshot(db)


@router.get("")
def list_shortcuts():
    with database() as db:
        db.execute("BEGIN")
        return snapshot(db)


@router.post("", status_code=201)
def create_shortcut(item: ShortcutWrite):
    # language=SQL
    insert_sql = """
    INSERT INTO shortcuts (id, name, description, aliases, icon, color, action, value, position)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with database() as db:
        check_revision(db, item.revision)
        if db.execute("SELECT COUNT(*) FROM shortcuts").fetchone()[0] >= MAX_SHORTCUTS:
            raise HTTPException(422, "You can save up to 100 shortcuts.")
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shortcuts").fetchone()[0]
        db.execute(insert_sql, (uuid4().hex, *values(item), position))
        return changed(db)


@router.put("/order")
def reorder_shortcuts(order: ShortcutOrder):
    with database() as db:
        check_revision(db, order.revision)
        current = {row[0] for row in db.execute("SELECT id FROM shortcuts")}
        if len(order.ids) != len(set(order.ids)) or set(order.ids) != current:
            raise HTTPException(422, "The order must include every current shortcut exactly once.")
        db.executemany("UPDATE shortcuts SET position = ? WHERE id = ?", enumerate(order.ids))
        return changed(db)


@router.put("/{shortcut_id}")
def update_shortcut(shortcut_id: str, item: ShortcutWrite):
    # language=SQL
    update_sql = """
    UPDATE shortcuts SET name = ?, description = ?, aliases = ?, icon = ?, color = ?, action = ?, value = ?
    WHERE id = ?
    """
    with database() as db:
        check_revision(db, item.revision)
        if db.execute(update_sql, (*values(item), shortcut_id)).rowcount == 0:
            raise HTTPException(404, "This shortcut no longer exists.")
        return changed(db)


@router.delete("/{shortcut_id}")
def delete_shortcut(shortcut_id: str, revision: int = Query(ge=0)):
    with database() as db:
        check_revision(db, revision)
        if db.execute("DELETE FROM shortcuts WHERE id = ?", (shortcut_id,)).rowcount == 0:
            raise HTTPException(404, "This shortcut no longer exists.")
        return changed(db)
