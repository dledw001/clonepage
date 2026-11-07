"""Reviewed, resumable creation of NAS service routes and homepage shortcuts."""

import asyncio
import copy
import hashlib
import json
import logging
import re
import secrets
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dsm import CERT_SERVICE, PROXY, DSMError, manager, no_store, router
from shortcuts import MAX_SHORTCUTS, ShortcutFields, changed, snapshot, values


logger = logging.getLogger(__name__)


def database():
    from main import database as connect
    return connect()


def initialize_services():
    # language=SQL
    schema_sql = """
    CREATE TABLE IF NOT EXISTS service_operations (
        id TEXT PRIMARY KEY,
        hostname TEXT NOT NULL UNIQUE,
        settings TEXT NOT NULL,
        rule_id TEXT,
        shortcut_id TEXT,
        stage TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        updated_at TEXT NOT NULL
    )
    """
    with database() as db:
        db.execute(schema_sql)


class ServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    icon: str
    color: str
    subdomain: str = Field(min_length=1, max_length=63)
    upstream: str = Field(min_length=1, max_length=300)
    template_id: str = Field(min_length=1, max_length=128)
    certificate_id: str = Field(min_length=1, max_length=128)


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=20, max_length=100)


def operation(hostname):
    with database() as db:
        row = db.execute("SELECT * FROM service_operations WHERE hostname = ?", (hostname,)).fetchone()
        return dict(row) if row else None


def update_operation(hostname, stage, **fields):
    # Only internally named metadata fields can be updated; no credentials live here.
    assert set(fields) <= {"rule_id", "shortcut_id", "error"}
    fields.update(stage=stage, updated_at=datetime.now(timezone.utc).isoformat())
    sql = "UPDATE service_operations SET " + ", ".join(f"{name} = ?" for name in fields) + " WHERE hostname = ?"
    with database() as db:
        db.execute(sql, (*fields.values(), hostname))


def rule_id(rule):
    return rule.get("UUID") or rule.get("uuid")


def template_entry(rule):
    front = rule.get("frontend", {})
    if not isinstance(front, dict) or not isinstance(front.get("https", {}), dict):
        raise DSMError("DSM returned an unsupported settings template.", 400)
    if not rule_id(rule) or front.get("protocol") != 1 or front.get("port") != 443 or not isinstance(front.get("fqdn"), str):
        raise DSMError("Choose a supported HTTPS reverse-proxy rule as the settings template.", 400)
    entry = {"frontend": {"protocol": 1, "port": 443, "https": {"hsts": bool(front.get("https", {}).get("hsts", False))}, "acl": copy.deepcopy(front.get("acl"))},
             "proxy_connect_timeout": rule.get("proxy_connect_timeout", 60), "proxy_read_timeout": rule.get("proxy_read_timeout", 60),
             "proxy_send_timeout": rule.get("proxy_send_timeout", 60), "proxy_http_version": rule.get("proxy_http_version", 1),
             "proxy_intercept_errors": bool(rule.get("proxy_intercept_errors", False))}
    # Never copy an Authorization/API-key header to a different service.
    allowed = {("upgrade", "$http_upgrade"), ("connection", "$connection_upgrade"), ("host", "$host"),
               ("x-real-ip", "$remote_addr"), ("x-forwarded-for", "$proxy_add_x_forwarded_for"), ("x-forwarded-proto", "$scheme")}
    headers = rule.get("customize_headers", []) or []
    if not isinstance(headers, list) or any(not isinstance(h, dict) or (str(h.get("name", "")).lower(), h.get("value")) not in allowed for h in headers):
        raise DSMError("This template has custom headers that need manual review. Choose another template.", 400)
    if headers:
        entry["customize_headers"] = [{"name": header["name"], "value": header["value"]} for header in headers]
    return entry


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def wildcard_certificates(certs, domain):
    result = []
    for cert in certs:
        subject = cert.get("subject", {})
        if not isinstance(subject, dict):
            continue
        names = subject.get("sub_alt_name", [])
        if not isinstance(names, list):
            continue
        if cert.get("id") and not cert.get("is_broken", False) and f"*.{domain}" in [str(name).lower() for name in names]:
            result.append(cert)
    return result


def source_matches(rule, hostname):
    front = rule.get("frontend", {})
    if front.get("port") != 443:
        return False
    name = str(front.get("fqdn", "")).lower()
    return name == hostname or name == "*" or (name.startswith("*.") and hostname.endswith(name[1:]))


def rule_matches(rule, settings, hostname):
    parts = urlsplit(settings["upstream"])
    backend = rule.get("backend", {})
    front = rule.get("frontend", {})
    return (front.get("fqdn", "").lower() == hostname and front.get("port") == 443 and front.get("protocol") == 1
            and backend.get("fqdn", "").lower() == parts.hostname and backend.get("port") == (parts.port or (443 if parts.scheme == "https" else 80))
            and backend.get("protocol") == (1 if parts.scheme == "https" else 0))


def check_existing(rules, settings, hostname, previous):
    matches = [rule for rule in rules if source_matches(rule, hostname)]
    if not matches:
        if previous and previous["rule_id"]:
            raise DSMError("The previously created rule is missing. Review it in DSM before retrying.", 409)
        return None
    if len(matches) == 1 and previous and rule_matches(matches[0], settings, hostname):
        rule = matches[0]
        if rule_id(rule) == previous["rule_id"] or (not previous["rule_id"] and previous["stage"] == "creating_rule" and rule.get("description") == f"Clonepage: {settings['name']}"):
            return rule
    raise DSMError("A DSM rule already uses this hostname. Existing rules will not be overwritten.", 409)


def validate_settings(item, config):
    subdomain = item.subdomain.lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", subdomain):
        raise DSMError("Enter one subdomain using letters, numbers, and hyphens.", 400)
    hostname = f"{subdomain}.{config.domain}"
    if hostname in {urlsplit(config.url).hostname, urlsplit(config.origin).hostname}:
        raise DSMError("The DSM and Clonepage hostnames cannot be provisioned here.", 400)
    try:
        parts = urlsplit(item.upstream)
        if (parts.scheme not in ("http", "https") or parts.hostname not in config.upstream_hosts or parts.username is not None or parts.password is not None
                or parts.path not in ("", "/") or parts.query or parts.fragment or not 1 <= (parts.port or (443 if parts.scheme == "https" else 80)) <= 65535
                or re.search(r"[\s\\]", item.upstream)):
            raise ValueError()
    except ValueError:
        raise DSMError("Use an HTTP(S) destination on an allowed NAS host, with no path, query, or credentials.", 400) from None
    try:
        shortcut = ShortcutFields.model_validate({"name": item.name, "description": item.description, "aliases": item.aliases,
            "icon": item.icon, "color": item.color, "action": "open", "value": f"https://{hostname}"})
    except ValidationError:
        raise DSMError("Check the shortcut name, icon, color, and aliases.", 400) from None
    settings = {**item.model_dump(), "subdomain": subdomain, "upstream": item.upstream.rstrip("/"), "aliases": shortcut.aliases}
    return settings, hostname


async def prepare(client, item, config):
    settings, hostname = validate_settings(item, config)
    rules = await client.rules(); certs = await client.certificates()
    template = next((rule for rule in rules if rule_id(rule) == item.template_id), None)
    if not template:
        raise DSMError("The selected settings template no longer exists. Reload the connection options.", 409)
    entry = template_entry(template)
    if not any(cert.get("id") == item.certificate_id for cert in wildcard_certificates(certs, config.domain)):
        raise DSMError("Choose an available wildcard certificate for the configured domain.", 400)
    previous = operation(hostname)
    if previous and json.loads(previous["settings"]) != settings:
        raise DSMError("An earlier setup exists for this hostname with different settings. Review it in DSM before continuing.", 409)
    existing = check_existing(rules, settings, hostname, previous)
    if existing and fingerprint(template_entry(existing)) != fingerprint(entry):
        raise DSMError("The existing route no longer matches the reviewed connection settings. Review it in DSM before retrying.", 409)
    with database() as db:
        if db.execute("SELECT COUNT(*) FROM shortcuts").fetchone()[0] >= MAX_SHORTCUTS and not (previous and previous["shortcut_id"]):
            raise DSMError("Your shortcut list is full. Remove a shortcut before creating another service.", 400)
    return settings, hostname, entry, existing, template


@router.post("/options")
async def options(request: Request, response: Response):
    sessions = manager(request); key, session = await sessions.get(request, mutate=True, connected=True)
    if session.lock.locked():
        raise HTTPException(409, "A DSM operation is already in progress.")
    async with session.lock:
        try:
            rules = await session.client.rules(); certs = await session.client.certificates()
            templates = []
            for rule in rules:
                try:
                    template_entry(rule)
                except DSMError:
                    continue
                if str(rule["frontend"]["fqdn"]).lower().endswith("." + sessions.config.domain):
                    templates.append({"id": rule_id(rule), "name": rule.get("description") or rule["frontend"]["fqdn"],
                                      "hostname": rule["frontend"]["fqdn"], "access_control": rule["frontend"].get("acl") is not None})
            certificates = [{"id": cert["id"], "name": cert.get("desc") or f"*.{sessions.config.domain}", "expires": cert.get("valid_till", "")}
                            for cert in wildcard_certificates(certs, sessions.config.domain)]
            session.touched = time.monotonic()
        except DSMError as error:
            if error.status == 401:
                await sessions.remove(key)
            raise HTTPException(error.status, str(error)) from None
    no_store(response)
    return {"templates": templates, "certificates": certificates}


@router.post("/preview")
async def preview(item: ServiceInput, request: Request, response: Response):
    sessions = manager(request); key, session = await sessions.get(request, mutate=True, connected=True)
    if session.lock.locked():
        raise HTTPException(409, "A DSM operation is already in progress.")
    async with session.lock:
        session.preview = None
        try:
            settings, hostname, entry, existing, template = await prepare(session.client, item, sessions.config)
            nonce = secrets.token_urlsafe(24)
            session.preview = {"id": nonce, "settings": settings, "template": fingerprint(entry), "created": time.monotonic()}
            session.touched = time.monotonic()
        except DSMError as error:
            if error.status == 401:
                await sessions.remove(key)
            raise HTTPException(error.status, str(error)) from None
    no_store(response)
    return {"preview_id": nonce, "url": f"https://{hostname}", "upstream": settings["upstream"], "template": template.get("description") or template["frontend"]["fqdn"],
            "resuming": bool(operation(hostname)), "name": settings["name"], "access_control": entry["frontend"]["acl"] is not None}


async def assign_certificate(client, uuid, cert_id):
    for attempt in range(6):
        certs = await client.certificates()
        matches = [(cert["id"], service) for cert in certs for service in cert.get("services", [])
                   if service.get("subscriber", "").lower() == "reverseproxy" and service.get("service") == uuid]
        if len(matches) == 1:
            old_id, service = matches[0]
            if old_id == cert_id:
                return
            await client.call(CERT_SERVICE, "set", settings=json.dumps([{"service": service, "old_id": old_id, "id": cert_id}], separators=(",", ":")))
            certs = await client.certificates()
            if any(cert.get("id") == cert_id and any(s.get("subscriber", "").lower() == "reverseproxy" and s.get("service") == uuid for s in cert.get("services", [])) for cert in certs):
                return
            raise DSMError("DSM did not confirm the new rule's certificate assignment. Reconnect and retry to resume.")
        if attempt < 5:
            await asyncio.sleep(.4)
    raise DSMError("The new rule's certificate service could not be identified safely. Review it in DSM, then reconnect to resume.")


def https_failure_reason(error):
    # Inspect types only: raw errors/responses can contain sensitive data.
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "certificate", "The service's HTTPS certificate could not be verified. Check its certificate assignment and chain."
        if isinstance(cause, socket.gaierror):
            return "dns", "The service's HTTPS address could not be resolved. Check DNS or DSM_PROXY_IP."
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, httpx.TimeoutException):
        return "timeout", "The HTTPS check timed out. Check the NAS connection and reverse-proxy destination."
    if isinstance(error, httpx.ConnectError):
        return "connection", "The HTTPS check could not connect to the NAS. Check the LAN address and HTTPS listener."
    return "http", "The HTTPS check failed while communicating with the service. Check the reverse-proxy destination."


async def verify_https(hostname, proxy_ip=None):
    # Connect to the NAS directly, but route and verify TLS for the service hostname.
    # This is a server-configured address; the browser cannot choose the check target.
    url = httpx.URL(scheme="https", host=proxy_ip or hostname, path="/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=False,
                                     trust_env=False, verify=True) as client:
            async with client.stream("GET", url, headers={"Host": hostname},
                                     extensions={"sni_hostname": hostname}) as response:
                if response.status_code >= 500:
                    logger.warning("Service HTTPS check failed: hostname=%s connect_host=%s reason=upstream status=%s",
                                   hostname, proxy_ip or hostname, response.status_code)
                    raise DSMError("The HTTPS route is present but the service returned a server error. Check the destination, then reconnect to resume.")
    except httpx.HTTPError as error:
        reason, detail = https_failure_reason(error)
        logger.warning("Service HTTPS check failed: hostname=%s connect_host=%s reason=%s error_type=%s",
                       hostname, proxy_ip or hostname, reason, type(error).__name__)
        raise DSMError(f"{detail} Reconnect to resume.") from None


def publish_shortcut(hostname, settings):
    # language=SQL
    insert_sql = """
    INSERT INTO shortcuts (id, name, description, aliases, icon, color, action, value, position)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    item = ShortcutFields.model_validate({key: settings[key] for key in ("name", "description", "aliases", "icon", "color")} | {"action": "open", "value": f"https://{hostname}"})
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM shortcuts WHERE action = 'open' AND value = ?", (item.value,)).fetchone()
        if existing:
            return existing[0], snapshot(db)
        if db.execute("SELECT COUNT(*) FROM shortcuts").fetchone()[0] >= MAX_SHORTCUTS:
            raise DSMError("The route is ready, but the shortcut list is now full. Free a place and reconnect to resume.", 409)
        shortcut_id = uuid4().hex
        position = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shortcuts").fetchone()[0]
        db.execute(insert_sql, (shortcut_id, *values(item), position))
        return shortcut_id, changed(db)


@router.post("/services")
async def provision(item: ApplyInput, request: Request, response: Response):
    sessions = manager(request); key, session = await sessions.get(request, mutate=True, connected=True)
    if session.lock.locked() or sessions.provision_lock.locked():
        raise HTTPException(409, "Another service setup is in progress. Wait for it to finish.")
    plan = session.preview
    if not plan or not secrets.compare_digest(plan["id"], item.preview_id) or time.monotonic() - plan["created"] > IDLE_PREVIEW_SECONDS:
        raise HTTPException(409, "Review the service again before creating its rule.")
    hostname = None
    stage = "review"
    async with session.lock, sessions.provision_lock:
        try:
            settings, hostname, entry, existing, template = await prepare(session.client, ServiceInput.model_validate(plan["settings"]), sessions.config)
            if fingerprint(entry) != plan["template"]:
                raise DSMError("The settings template changed after review. Reconnect and review the service again.", 409)
            if not operation(hostname):
                with database() as db:
                    db.execute("INSERT INTO service_operations (id, hostname, settings, updated_at) VALUES (?, ?, ?, ?)",
                               (uuid4().hex, hostname, json.dumps(settings, sort_keys=True), datetime.now(timezone.utc).isoformat()))
            if not existing:
                parts = urlsplit(settings["upstream"])
                entry["description"] = f"Clonepage: {settings['name']}"
                entry["frontend"]["fqdn"] = hostname
                entry["backend"] = {"fqdn": parts.hostname, "port": parts.port or (443 if parts.scheme == "https" else 80), "protocol": 1 if parts.scheme == "https" else 0}
                stage = "creating_rule"; update_operation(hostname, stage, error=None)
                # No automatic retry of this POST: a timeout can still leave a created rule.
                await session.client.call(PROXY, "create", entry=json.dumps(entry, separators=(",", ":")))
                rules = await session.client.rules()
                candidates = [rule for rule in rules if rule_matches(rule, settings, hostname) and rule.get("description") == entry["description"]]
                if len(candidates) != 1 or not rule_id(candidates[0]):
                    raise DSMError("DSM did not confirm a unique new rule. Reconnect and retry to reconcile the setup.")
                existing = candidates[0]
            uuid = rule_id(existing)
            stage = "rule_created"; update_operation(hostname, stage, rule_id=uuid, error=None)
            await assign_certificate(session.client, uuid, settings["certificate_id"])
            stage = "certificate_assigned"; update_operation(hostname, stage, error=None)
            await verify_https(hostname, sessions.config.proxy_ip)
            stage = "https_verified"; update_operation(hostname, stage, error=None)
            shortcut_id, shortcuts = publish_shortcut(hostname, settings)
            update_operation(hostname, "ready", shortcut_id=shortcut_id, error=None)
            no_store(response)
            return {"ok": True, "url": f"https://{hostname}", "stage": "ready", "shortcuts": shortcuts, "disconnected": True}
        except DSMError as error:
            if hostname and operation(hostname):
                update_operation(hostname, stage, error=str(error))
            no_store(response)
            return {"ok": False, "stage": stage, "error": str(error), "disconnected": True,
                    "url": f"https://{hostname}" if hostname else None}
        finally:
            session.preview = None
            await sessions.remove(key)
            response.delete_cookie("__Host-clonepage-dsm", secure=True, httponly=True, samesite="strict", path="/")


IDLE_PREVIEW_SECONDS = 300
