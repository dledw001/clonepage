"""Shared, editable homepage shortcuts backed by the existing SQLite database."""

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictBool, field_validator, model_validator

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
        columns = {row["name"] for row in db.execute("PRAGMA table_info(shortcuts)")}
        if "group_name" not in columns:
            db.execute("ALTER TABLE shortcuts ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
        if "monitor" not in columns:
            db.execute("ALTER TABLE shortcuts ADD COLUMN monitor TEXT DEFAULT NULL")
        if "availability" not in columns:
            db.execute("ALTER TABLE shortcuts ADD COLUMN availability INTEGER NOT NULL DEFAULT 0")
        if db.execute("SELECT 1 FROM shortcut_settings WHERE id = 1").fetchone():
            return
        defaults = json.loads(Path(__file__).with_name("shortcut_defaults.json").read_text(encoding="utf-8"))
        for position, item in enumerate(defaults):
            validated = ShortcutFields.model_validate({key: value for key, value in item.items() if key != "id"})
            db.execute(insert_sql, (item["id"], *values(validated), position))
        # This marker remains even if every shortcut is deleted.
        db.execute("INSERT INTO shortcut_settings (id, revision) VALUES (1, 0)")


def clean_group(value):
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Group names cannot contain control characters.")
    return " ".join(value.split())


class MonitorTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["container", "project"]
    watcher: str = Field(min_length=1, max_length=128)
    project: str | None = Field(default=None, min_length=1, max_length=256)
    service: str | None = Field(default=None, min_length=1, max_length=256)
    container: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("watcher", "project", "service", "container")
    @classmethod
    def valid_identifier(cls, value):
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Monitoring names cannot contain control characters.")
        return value

    @model_validator(mode="after")
    def valid_binding(self):
        if self.kind == "project":
            if self.service is not None or self.container is not None:
                raise ValueError("A project association cannot specify a container or service.")
        elif self.service is not None:
            if self.project is None or self.container is not None:
                raise ValueError("A service association needs a project and no container name.")
        elif self.container is None:
            raise ValueError("Choose a container or a Compose service to monitor.")
        return self


class ShortcutFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    icon: str
    color: str
    action: Literal["open", "copy"]
    value: str = Field(min_length=1, max_length=4000)
    group: str = Field(default="", max_length=48)
    monitor: MonitorTarget | None = None
    availability: StrictBool = False

    @field_validator("group")
    @classmethod
    def valid_group(cls, value):
        return clean_group(value)

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
        if self.action == "copy" and self.availability:
            raise ValueError("Availability checks are available for website shortcuts.")
        if self.action == "copy" and self.monitor is not None:
            raise ValueError("Container monitoring is available for website shortcuts.")
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
        item["availability"] = bool(item["availability"])
        item["group"] = item.pop("group_name")
        item["monitor"] = json.loads(item["monitor"]) if item["monitor"] else None
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


def canonical_group(db, name):
    rows = db.execute("SELECT group_name FROM shortcuts ORDER BY position, id").fetchall()
    return next((row[0] for row in rows if row[0].casefold() == name.casefold()), name)


def organization_values(db, item):
    return canonical_group(db, item.group), json.dumps(item.monitor.model_dump()) if item.monitor else None


class GroupRename(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=48)
    new_name: str = Field(default="", max_length=48)

    @field_validator("name", "new_name")
    @classmethod
    def valid_name(cls, value):
        return clean_group(value)


@router.put("/groups")
def rename_group(item: GroupRename):
    with database() as db:
        check_revision(db, item.revision)
        names = {row[0] for row in db.execute("SELECT DISTINCT group_name FROM shortcuts")}
        if item.name not in names:
            raise HTTPException(404, "This group no longer exists.")
        if item.new_name and any(name != item.name and name.casefold() == item.new_name.casefold() for name in names):
            raise HTTPException(409, "Another group already uses that name. Choose a different name.")
        db.execute("UPDATE shortcuts SET group_name = ? WHERE group_name = ?", (item.new_name, item.name))
        return changed(db)


@router.get("")
def list_shortcuts():
    with database() as db:
        db.execute("BEGIN")
        return snapshot(db)


@router.post("", status_code=201)
def create_shortcut(item: ShortcutWrite):
    # language=SQL
    insert_sql = """
    INSERT INTO shortcuts (id, name, description, aliases, icon, color, action, value, position, group_name, monitor, availability)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    with database() as db:
        check_revision(db, item.revision)
        if db.execute("SELECT COUNT(*) FROM shortcuts").fetchone()[0] >= MAX_SHORTCUTS:
            raise HTTPException(422, "You can save up to 100 shortcuts.")
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shortcuts").fetchone()[0]
        db.execute(insert_sql, (uuid4().hex, *values(item), position, *organization_values(db, item), int(item.availability)))
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
    UPDATE shortcuts SET name = ?, description = ?, aliases = ?, icon = ?, color = ?, action = ?, value = ?, group_name = ?, monitor = ?, availability = ?
    WHERE id = ?
    """
    with database() as db:
        check_revision(db, item.revision)
        existing = db.execute("SELECT group_name, monitor, availability FROM shortcuts WHERE id = ?", (shortcut_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "This shortcut no longer exists.")
        group, monitor = organization_values(db, item)
        # Older cached clients can still edit a shortcut without erasing new fields.
        if "group" not in item.model_fields_set:
            group = existing["group_name"]
        if "monitor" not in item.model_fields_set and item.action == "open":
            monitor = existing["monitor"]
        availability = item.availability
        if "availability" not in item.model_fields_set and item.action == "open":
            availability = bool(existing["availability"])
        db.execute(update_sql, (*values(item), group, monitor, int(availability), shortcut_id))
        return changed(db)


@router.delete("/{shortcut_id}")
def delete_shortcut(shortcut_id: str, revision: int = Query(ge=0)):
    with database() as db:
        check_revision(db, revision)
        if db.execute("DELETE FROM shortcuts WHERE id = ?", (shortcut_id,)).rowcount == 0:
            raise HTTPException(404, "This shortcut no longer exists.")
        return changed(db)
