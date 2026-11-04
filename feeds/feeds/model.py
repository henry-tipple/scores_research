"""Provider-neutral match model. Every adapter maps into these types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

EVENT_TYPES = (
    "scheduled",    # fixture announced / kick-off time set or changed
    "lineups",      # lineups / starting players published
    "start",        # match (or first period) begins
    "score",        # any scoring event: goal, try, basket run, wicket-as-score, point...
    "period_end",   # half-time, quarter end, set end, innings end, full period end
    "extra",        # extra time, overtime, super over, tie-break start
    "discipline",   # cards, ejections, penalties in the disciplinary sense
    "stoppage",     # VAR check, rain delay, injury stoppage, timeout of note
    "end",          # final whistle / result
    "postponed",    # postponed, abandoned, suspended
)

# Typical match length used to decide whether a fixture is still "live" when a feed
# has not (yet) sent an `end` event.
TYPICAL_DURATION = {
    "football": timedelta(hours=2),
    "basketball": timedelta(hours=2, minutes=30),
    "american_football": timedelta(hours=3, minutes=30),
    "ice_hockey": timedelta(hours=2, minutes=45),
    "tennis": timedelta(hours=4),
    "cricket": timedelta(hours=8),
}


@dataclass(frozen=True)
class Fixture:
    match_id: str
    sport: str
    competition: str | None
    home: str | None
    away: str | None
    kickoff: datetime
    ended_at: datetime | None = None
    postponed: bool = False

    def is_live(self, now: datetime, lead: timedelta = timedelta(minutes=10)) -> bool:
        """True from `lead` before kick-off until the end (or the typical duration)."""
        if self.postponed:
            return False
        end = self.ended_at or self.kickoff + TYPICAL_DURATION.get(self.sport, timedelta(hours=3))
        return self.kickoff - lead <= now < end

    def involves(self, names: set[str]) -> bool:
        lowered = {n.lower() for n in names}
        return bool({(self.home or "").lower(), (self.away or "").lower()} & lowered)


@dataclass(frozen=True)
class NormalizedEvent:
    source: str
    source_event_id: str
    match_id: str
    sport: str
    competition: str | None
    home: str | None
    away: str | None
    event_type: str
    sub_type: str | None
    minute: int | None
    occurred_at: datetime
    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {self.event_type!r}")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
