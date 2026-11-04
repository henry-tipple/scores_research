"""Sportradar adapter: STUB. Interface and config only, no keys, no live calls.

When a contract exists: fill in `_get` with an httpx client, implement `fixtures` from
each sport's daily schedule endpoint and `events` from the match timeline / push feed,
and map provider event types through `EVENT_MAP` below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from ..model import Fixture, NormalizedEvent
from .base import register

# Per-sport API products and the provider event types we expect, mapped to the generic
# event_type (and sub_type). Draft, to be checked against the contracted API versions.
EVENT_MAP: dict[str, dict[str, tuple[str, str | None]]] = {
    "football": {   # Soccer API v4 timeline
        "match_started": ("start", None), "score_change": ("score", "goal"),
        "break_start": ("period_end", "half_time"), "period_start": ("start", "second_half"),
        "yellow_card": ("discipline", "yellow_card"), "red_card": ("discipline", "red_card"),
        "yellow_red_card": ("discipline", "second_yellow"), "video_assistant_referee": ("stoppage", "var"),
        "injury_time_shown": ("extra", "added_time"), "match_ended": ("end", None),
        "penalty_shootout": ("extra", "penalties"),
    },
    "basketball": {  # NBA API v8 play-by-play
        "opentip": ("start", None), "twopointmade": ("score", "two_pointer"),
        "threepointmade": ("score", "three_pointer"), "freethrowmade": ("score", "free_throw"),
        "endperiod": ("period_end", "quarter"), "ejection": ("discipline", "ejection"),
        "technicalfoul": ("discipline", "technical"), "endgame": ("end", None),
    },
    "american_football": {"kickoff": ("start", None), "touchdown": ("score", "touchdown"),
                          "fieldgoal": ("score", "field_goal"), "end_period": ("period_end", "quarter")},
    "ice_hockey": {"goal": ("score", "goal"), "penalty": ("discipline", "penalty"),
                   "endperiod": ("period_end", "period"), "shootout": ("extra", "shootout")},
    "tennis": {"match_started": ("start", None), "set_ended": ("period_end", "set"),
               "tiebreak_started": ("extra", "tie_break"), "rain_delay": ("stoppage", "rain_delay")},
    "cricket": {"wicket": ("score", "wicket"), "innings_ended": ("period_end", "innings"),
                "rain_delay": ("stoppage", "rain_delay"), "super_over": ("extra", "super_over")},
}


@dataclass
class SportradarConfig:
    api_key_env: str = "SPORTRADAR_API_KEY"      # read from env at call time; never committed
    access_level: str = "trial"                  # trial | production
    base_url: str = "https://api.sportradar.com"
    sports: dict[str, str] = field(default_factory=lambda: {
        "football": "soccer/{access_level}/v4/en",
        "basketball": "nba/{access_level}/v8/en",
        "american_football": "nfl/official/{access_level}/v7/en",
        "ice_hockey": "nhl/{access_level}/v7/en",
        "tennis": "tennis/{access_level}/v3/en",
        "cricket": "cricket-t2/{access_level}/v2/en",
    })


@register("sportradar")
class SportradarAdapter:
    name = "sportradar"

    def __init__(self, **config):
        self.config = SportradarConfig(**config)

    def _api_key(self) -> str:
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise RuntimeError(f"{self.config.api_key_env} not set (Sportradar adapter is a stub)")
        return key

    def map_event(self, sport: str, provider_type: str) -> tuple[str, str | None] | None:
        return EVENT_MAP.get(sport, {}).get(provider_type)

    def fixtures(self, start: datetime, end: datetime) -> list[Fixture]:
        raise NotImplementedError("Sportradar adapter is a stub: no live calls until a contract and key exist")

    def events(self, since: datetime, until: datetime) -> Iterable[NormalizedEvent]:
        raise NotImplementedError("Sportradar adapter is a stub: no live calls until a contract and key exist")
