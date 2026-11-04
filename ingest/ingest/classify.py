"""Provisional send classification and match-context join.

Rules, in order (first match wins):
  1. A match event for a team/competition named in the message, between
     MATCH_LOOKBACK_MINUTES before the send and 60 s after it (clock skew). Live
     events beat scheduled ones; then events whose score/player the text quotes;
     then the most recent.
       -> scheduled/lineups event: match_scheduled; anything else: match_event
  2. A fixture named in the message that kicks off within MATCH_LOOKAHEAD_MINUTES
       -> match_scheduled (seconds_since_event is negative: seconds until kick-off)
  3. Commercial keywords -> commercial
  4. Lifecycle keywords  -> lifecycle
  5. An ongoing (live_update) card with no match found -> match_event
  6. Otherwise           -> content

This is a first pass so the dashboard is usable from day one. Phase 4 should relabel
with something better (e.g. per-app channel_id mappings once real payloads are seen).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

COMMERCIAL = re.compile(
    r"\b(offer|discount|% off|sale|premium|subscribe|subscription|free trial|deal|promo|bet|odds|bonus"
    r"|angebot|rabatt|offerta|sconto|oferta|descuento|割引|キャンペーン)\b", re.I)
LIFECYCLE = re.compile(
    r"(we miss you|come back|haven't seen|it's been a while|welcome|rate us|rate the app|update the app"
    r"|enable notifications|turn on notifications|complete your profile|vermissen|willkommen|ci manchi"
    r"|benvenut|te echamos de menos|bienvenid|お帰り|ようこそ)", re.I)

LIVE_TYPES = ("start", "score", "period_end", "extra", "discipline", "stoppage", "end", "postponed")


@dataclass
class Classification:
    send_class: str
    match_id: str | None = None
    match_event_id: int | None = None
    seconds_since_event: int | None = None


def _terms_sql() -> str:
    # Name terms for a match_events row: home, away, competition and any aliases in payload.
    return """
      array_remove(array[lower(me.home), lower(me.away), lower(me.competition)]
        || coalesce((select array_agg(lower(a)) from jsonb_each(coalesce(me.payload->'aliases','{}'::jsonb)) kv,
                     jsonb_array_elements_text(kv.value) a), '{}'), null)
    """


def classify(conn, *, text: str, at: datetime, channel: str, lookback_min: int, lookahead_min: int) -> Classification:
    text_l = (text or "").lower()
    if text_l.strip():
        row = conn.execute(
            f"""
            select me.id, me.match_id, me.event_type, me.occurred_at
            from match_events me
            where me.occurred_at between %(at)s - %(back)s and %(at)s + interval '60 seconds'
              and exists (select 1 from unnest({_terms_sql()}) t where length(t) >= 3 and position(t in %(txt)s) > 0)
            order by (me.event_type = any(%(live)s)) desc,
                     -- prefer the event whose detail (score, player) the message quotes
                     coalesce(position(lower(me.payload->>'score') in %(txt)s) > 0
                              or position(lower(me.payload->>'player') in %(txt)s) > 0, false) desc,
                     me.occurred_at desc
            limit 1
            """,
            {"at": at, "back": timedelta(minutes=lookback_min), "txt": text_l, "live": list(LIVE_TYPES)},
        ).fetchone()
        if row:
            eid, mid, etype, occurred = row
            cls = "match_scheduled" if etype in ("scheduled", "lineups") else "match_event"
            return Classification(cls, mid, eid, int((at - occurred).total_seconds()))

        row = conn.execute(
            f"""
            select me.id, me.match_id, (me.payload->>'kickoff')::timestamptz as ko
            from match_events me
            where me.payload ? 'kickoff'
              and (me.payload->>'kickoff')::timestamptz between %(at)s and %(at)s + %(ahead)s
              and exists (select 1 from unnest({_terms_sql()}) t where length(t) >= 3 and position(t in %(txt)s) > 0)
            order by ko asc, me.occurred_at desc
            limit 1
            """,
            {"at": at, "ahead": timedelta(minutes=lookahead_min), "txt": text_l},
        ).fetchone()
        if row:
            eid, mid, ko = row
            return Classification("match_scheduled", mid, eid, int((at - ko).total_seconds()))

    if COMMERCIAL.search(text_l):
        return Classification("commercial")
    if LIFECYCLE.search(text_l):
        return Classification("lifecycle")
    if channel == "live_update":
        return Classification("match_event")
    return Classification("content")


def engagement(conn, instance_id: str, at: datetime) -> tuple[int | None, int]:
    """(days_since_last_open, opens_last_7d) at the time of the send, from `sessions`."""
    last, n7 = conn.execute(
        """select max(started_at) filter (where started_at <= %(at)s),
                  count(*) filter (where started_at > %(at)s - interval '7 days' and started_at <= %(at)s)
           from sessions where instance_id = %(i)s""",
        {"at": at, "i": instance_id},
    ).fetchone()
    days = None if last is None else (at - last).days
    return days, int(n7)
