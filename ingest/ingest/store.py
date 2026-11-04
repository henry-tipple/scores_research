"""Event persistence with windowed idempotency."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .classify import classify, engagement
from .config import Settings


class UnknownInstance(LookupError):
    pass


@dataclass
class EventRow:
    instance_id: str
    channel: str
    local_time: datetime
    package: str | None = None
    notification_key: str | None = None
    notification_channel_id: str | None = None
    title: str | None = None
    body: str | None = None
    big_text: str | None = None
    campaign_id: str | None = None
    crm_vendor: str | None = None
    deep_link: str | None = None
    extras: dict = field(default_factory=dict)
    screenshot_path: str | None = None
    action: str | None = None
    raw: dict = field(default_factory=dict)
    dedupe_key: str = ""
    dedupe_window: int | None = 60   # seconds; None = exact key, any time


def content_hash(*parts: str | None) -> str:
    return hashlib.sha1("\x1f".join(p or "" for p in parts).encode()).hexdigest()[:16]


def dedupe_key_for_push(channel: str, campaign_id: str | None, notification_key: str | None,
                        channel_id: str | None, title: str | None, body: str | None, big_text: str | None) -> str:
    """Same instance + campaign_id within the window is a duplicate.

    Live-update cards repost on every score change, so they always key on content:
    one row per change, exact repeats inside the window are dropped.
    """
    if campaign_id and channel != "live_update":
        return f"cid:{campaign_id}"
    return f"nk:{notification_key or ''}:{content_hash(channel_id, title, body, big_text)}"


def lookup_instance(conn, instance_id: str) -> dict[str, Any]:
    row = conn.execute(
        "select id, app, cohort_id, market, package from instances where id = %s", (instance_id,)).fetchone()
    if not row:
        raise UnknownInstance(instance_id)
    return dict(zip(("id", "app", "cohort_id", "market", "package"), row))


def lookup_instance_by_email(conn, addresses: list[str]) -> dict[str, Any] | None:
    if not addresses:
        return None
    row = conn.execute(
        "select id, app, cohort_id, market, package from instances where lower(email) = any(%s) limit 1",
        ([a.lower() for a in addresses],)).fetchone()
    return dict(zip(("id", "app", "cohort_id", "market", "package"), row)) if row else None


def insert_event(conn, settings: Settings, ev: EventRow, instance: dict | None = None) -> tuple[int, bool]:
    """Insert unless a duplicate exists. Returns (event_id, created)."""
    inst = instance or lookup_instance(conn, ev.instance_id)
    with conn.transaction():
        # Serialise concurrent posts for the same key so the window check is race-free.
        conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{ev.instance_id}|{ev.dedupe_key}",))
        if ev.dedupe_window is None:
            dup = conn.execute("select id from events where instance_id = %s and dedupe_key = %s limit 1",
                               (ev.instance_id, ev.dedupe_key)).fetchone()
        else:
            w = timedelta(seconds=ev.dedupe_window)
            dup = conn.execute(
                """select id from events where instance_id = %s and dedupe_key = %s
                   and local_time between %s and %s limit 1""",
                (ev.instance_id, ev.dedupe_key, ev.local_time - w, ev.local_time + w)).fetchone()
        if dup:
            return dup[0], False

        text = " ".join(x for x in (ev.title, ev.body, ev.big_text) if x)
        cls = classify(conn, text=text, at=ev.local_time, channel=ev.channel,
                       lookback_min=settings.match_lookback_minutes, lookahead_min=settings.match_lookahead_minutes)
        days, opens7 = engagement(conn, ev.instance_id, ev.local_time)
        margin = timedelta(minutes=settings.downtime_margin_minutes)
        adjacent = conn.execute(
            """select exists(select 1 from instance_downtime where instance_id = %s
                 and %s between started_at - %s and coalesce(ended_at, now()) + %s)""",
            (ev.instance_id, ev.local_time, margin, margin)).fetchone()[0]

        new_id = conn.execute(
            """insert into events (local_time, instance_id, app, cohort_id, market, channel, package,
                 notification_key, notification_channel_id, title, body, big_text, campaign_id, crm_vendor,
                 deep_link, extras, match_id, match_event_id, seconds_since_event, days_since_last_open,
                 opens_last_7d, send_class, screenshot_path, action, downtime_adjacent, dedupe_key, raw)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               returning id""",
            (ev.local_time, ev.instance_id, inst["app"], inst["cohort_id"], inst["market"], ev.channel,
             ev.package or inst["package"], ev.notification_key, ev.notification_channel_id, ev.title, ev.body,
             ev.big_text, ev.campaign_id, ev.crm_vendor, ev.deep_link, json.dumps(ev.extras, default=str),
             cls.match_id, cls.match_event_id, cls.seconds_since_event, days, opens7, cls.send_class,
             ev.screenshot_path, ev.action, adjacent, ev.dedupe_key, json.dumps(ev.raw, default=str)),
        ).fetchone()[0]
    return new_id, True
