"""Fake adapter: replays sample matches from feeds/samples/*.json.

Each sample gives a kick-off time-of-day and events as minutes after kick-off. The
replay is anchored to a date (default: today, UTC). Only events whose computed time is
<= `until` are returned, so polling in real time reveals the match as it "happens".
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from ..model import Fixture, NormalizedEvent
from .base import register

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"


@register("fake")
class FakeReplayAdapter:
    name = "fake"

    def __init__(self, samples: list[str | Path] | None = None, anchor: date | str | None = None,
                 kickoff_overrides: dict[str, str] | None = None):
        paths = [Path(p) for p in samples] if samples else sorted(SAMPLES_DIR.glob("*.json"))
        if isinstance(anchor, str):
            anchor = date.fromisoformat(anchor)
        self.anchor = anchor or datetime.now(timezone.utc).date()
        self.samples = [json.loads(p.read_text()) for p in paths]
        self.kickoff_overrides = kickoff_overrides or {}

    def _kickoff(self, s: dict) -> datetime:
        raw = self.kickoff_overrides.get(s["match_id"], s["kickoff_utc"])
        hh, mm = (int(x) for x in raw.split(":"))
        return datetime.combine(self.anchor + timedelta(days=s.get("day_offset", 0)),
                                time(hh, mm), tzinfo=timezone.utc)

    def _match_id(self, s: dict) -> str:
        return f"{s['match_id']}-{self.anchor.isoformat()}"

    def fixtures(self, start: datetime, end: datetime) -> list[Fixture]:
        out = []
        for s in self.samples:
            ko = self._kickoff(s)
            end_ev = next((e for e in s["events"] if e["event_type"] == "end"), None)
            fx = Fixture(self._match_id(s), s["sport"], s.get("competition"), s.get("home"), s.get("away"), ko,
                         ended_at=ko + timedelta(minutes=end_ev["t"]) if end_ev else None)
            if start <= ko <= end or fx.is_live(start):
                out.append(fx)
        return out

    def events(self, since: datetime, until: datetime) -> Iterable[NormalizedEvent]:
        for s in self.samples:
            ko = self._kickoff(s)
            mid = self._match_id(s)
            for n, e in enumerate(s["events"]):
                at = ko + timedelta(minutes=e["t"])
                if not (since <= at <= until):
                    continue
                yield NormalizedEvent(
                    source=self.name, source_event_id=f"{mid}:{n}", match_id=mid, sport=s["sport"],
                    competition=s.get("competition"), home=s.get("home"), away=s.get("away"),
                    event_type=e["event_type"], sub_type=e.get("sub_type"), minute=e.get("minute"),
                    occurred_at=at,
                    payload={**e.get("payload", {}), "aliases": s.get("aliases", {}), "kickoff": ko.isoformat()},
                )
