"""Write normalised events into match_events (idempotent on source + source_event_id)."""
from __future__ import annotations

import json
from typing import Iterable

from .model import NormalizedEvent

UPSERT = """
insert into match_events (source, source_event_id, match_id, competition, sport, home, away,
                          event_type, sub_type, minute, occurred_at, payload)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
on conflict (source, source_event_id) do update set
  match_id = excluded.match_id, competition = excluded.competition, sport = excluded.sport,
  home = excluded.home, away = excluded.away, event_type = excluded.event_type,
  sub_type = excluded.sub_type, minute = excluded.minute, occurred_at = excluded.occurred_at,
  payload = excluded.payload
returning (xmax = 0) as inserted
"""


def upsert_events(conn, events: Iterable[NormalizedEvent]) -> tuple[int, int]:
    """Returns (inserted, updated)."""
    inserted = updated = 0
    with conn.cursor() as cur:
        for e in events:
            cur.execute(UPSERT, (e.source, e.source_event_id, e.match_id, e.competition, e.sport, e.home,
                                 e.away, e.event_type, e.sub_type, e.minute, e.occurred_at,
                                 json.dumps(e.payload)))
            if cur.fetchone()[0]:
                inserted += 1
            else:
                updated += 1
    conn.commit()
    return inserted, updated


def followed_names(conn) -> set[str]:
    """Every team/player display name any cohort follows (from cohorts.follows)."""
    names: set[str] = set()
    for (follows,) in conn.execute("select follows from cohorts"):
        for kind in ("teams", "players", "leagues"):
            for item in follows.get(kind, []) or []:
                names.add(item["name"] if isinstance(item, dict) else item)
    return names
