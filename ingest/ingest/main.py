"""FastAPI app.

POST /v1/notification   logger-apk JSON
POST /v1/email          Mailpit webhook
POST /v1/sessions       Maestro runner: a scripted session started
POST /v1/sessions/{id}/end
GET  /healthz
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from psycopg_pool import ConnectionPool

from . import email_ingest
from .campaign import extract_deep_link, extract_push_campaign
from .config import PACKAGE_TO_APP, Settings, get_settings
from .models import NotificationIn, SessionEnd, SessionStart
from .store import EventRow, UnknownInstance, dedupe_key_for_push, insert_event, lookup_instance, lookup_instance_by_email

log = logging.getLogger("ingest")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.pool = ConnectionPool(settings.database_url, min_size=1, max_size=10,
                                    kwargs={"autocommit": True}, open=True)
    try:
        yield
    finally:
        app.state.pool.close()


app = FastAPI(title="study-ingest", version="0.1.0", lifespan=lifespan)


def conn_dep(request: Request):
    with request.app.state.pool.connection() as conn:
        yield conn


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


@app.get("/healthz")
def healthz(conn=Depends(conn_dep)) -> dict:
    conn.execute("select 1")
    return {"ok": True}


@app.post("/v1/notification")
def post_notification(n: NotificationIn, response: Response, conn=Depends(conn_dep),
                      settings: Settings = Depends(settings_dep)) -> dict:
    app_id = PACKAGE_TO_APP.get(n.package)
    if app_id is None:
        raise HTTPException(422, f"untracked package {n.package}")
    try:
        inst = lookup_instance(conn, n.instance_id)
    except UnknownInstance:
        raise HTTPException(422, f"unknown instance {n.instance_id}")
    if inst["app"] != app_id:
        log.warning("instance %s is %s but posted %s", n.instance_id, inst["app"], n.package)

    channel = "live_update" if n.ongoing else "push"
    hit = extract_push_campaign(n.extras)
    ev = EventRow(
        instance_id=n.instance_id, channel=channel, local_time=n.posted_at, package=n.package,
        notification_key=n.notification_key, notification_channel_id=n.channel_id,
        title=n.title, body=n.text, big_text=n.big_text,
        campaign_id=hit.campaign_id if hit else None, crm_vendor=hit.vendor if hit else None,
        deep_link=extract_deep_link(n.extras), extras=n.extras,
        screenshot_path=n.screenshot_path, action=n.action,
        raw=n.model_dump(mode="json", by_alias=True),
        dedupe_key=dedupe_key_for_push(channel, hit.campaign_id if hit else None, n.notification_key,
                                       n.channel_id, n.title, n.text, n.big_text),
        dedupe_window=settings.dedupe_window_seconds,
    )
    if n.action == "active_on_connect":
        # Re-sent by the logger when its listener reconnects: same notification as an
        # earlier "posted" row, however long ago, so match the key with no time window.
        ev.dedupe_window = None
    elif n.action != "posted":
        # "removed" callbacks are recorded but must not collide with the post itself.
        ev.dedupe_key = f"{n.action}:{ev.dedupe_key}"
    event_id, created = insert_event(conn, settings, ev, inst)
    response.status_code = 201 if created else 200
    return {"id": event_id, "duplicate": not created, "channel": channel, "campaign_id": ev.campaign_id}


def _ingest_email(conn, settings: Settings, summary: dict, client: httpx.Client | None = None) -> dict:
    message, headers = email_ingest.fetch_full(settings.mailpit_url, summary.get("ID", ""), client)
    # Mailpit's summary has no headers; the full message has the same To list plus Text/HTML.
    addrs = email_ingest.addresses(summary) or email_ingest.addresses(message)
    inst = lookup_instance_by_email(conn, addrs)
    if inst is None:
        return {"ignored": True, "reason": "no instance for recipients", "to": addrs}
    ev = email_ingest.build_email_event(inst["id"], summary, message, headers)
    event_id, created = insert_event(conn, settings, ev, inst)
    return {"id": event_id, "created": created, "duplicate": not created, "instance_id": inst["id"],
            "campaign_id": ev.campaign_id}


@app.post("/v1/email")
def post_email(summary: dict[str, Any], response: Response, conn=Depends(conn_dep),
               settings: Settings = Depends(settings_dep)) -> dict:
    """Mailpit webhook (MP_WEBHOOK_URL). Body is Mailpit's message summary."""
    result = _ingest_email(conn, settings, summary)
    response.status_code = 202 if result.get("ignored") else 201 if result["created"] else 200
    return result


@app.post("/v1/email/sync")
def sync_email(conn=Depends(conn_dep), settings: Settings = Depends(settings_dep)) -> dict:
    """Backfill: ingest every Mailpit message not yet in events (webhooks are fire-and-forget)."""
    seen = {r[0] for r in conn.execute(
        "select extras->>'mailpit_id' from events where channel = 'email' and extras ? 'mailpit_id'")}
    created = skipped = ignored = 0
    with httpx.Client(timeout=10) as client:
        for summary in email_ingest.list_messages(settings.mailpit_url, client):
            if summary.get("ID") in seen:
                skipped += 1
                continue
            r = _ingest_email(conn, settings, summary, client)
            if r.get("ignored"):
                ignored += 1
            elif r["created"]:
                created += 1
    return {"created": created, "already_present": skipped, "ignored": ignored}


@app.post("/v1/sessions", status_code=201)
def start_session(s: SessionStart, conn=Depends(conn_dep)) -> dict:
    try:
        lookup_instance(conn, s.instance_id)
    except UnknownInstance:
        raise HTTPException(422, f"unknown instance {s.instance_id}")
    sid = conn.execute(
        "insert into sessions (instance_id, started_at, script) values (%s, %s, %s) returning id",
        (s.instance_id, s.started_at or datetime.now(timezone.utc), s.script)).fetchone()[0]
    return {"id": sid}


@app.post("/v1/sessions/{session_id}/end")
def end_session(session_id: int, s: SessionEnd, conn=Depends(conn_dep)) -> dict:
    row = conn.execute(
        """update sessions set ended_at = %s, screens = screens || %s::jsonb
           where id = %s returning id""",
        (s.ended_at or datetime.now(timezone.utc), json.dumps(s.screens), session_id)).fetchone()
    if not row:
        raise HTTPException(404, "no such session")
    return {"id": row[0]}
