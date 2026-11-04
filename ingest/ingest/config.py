from __future__ import annotations

import os
from dataclasses import dataclass, field

# Tracked packages -> app ids (mirrors the `apps` table; a test checks they agree).
PACKAGE_TO_APP: dict[str, str] = {
    "com.scores365": "365scores",
    "com.mobilefootie.wc2010": "fotmob",
    "com.livescore": "livescore",
}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://study:study@localhost:5432/study"))
    mailpit_url: str = field(default_factory=lambda: os.environ.get("MAILPIT_URL", "http://localhost:8025"))
    dedupe_window_seconds: int = field(default_factory=lambda: int(os.environ.get("DEDUPE_WINDOW_SECONDS", "60")))
    downtime_margin_minutes: int = field(default_factory=lambda: int(os.environ.get("DOWNTIME_MARGIN_MINUTES", "10")))
    match_lookback_minutes: int = field(default_factory=lambda: int(os.environ.get("MATCH_LOOKBACK_MINUTES", "30")))
    match_lookahead_minutes: int = field(default_factory=lambda: int(os.environ.get("MATCH_LOOKAHEAD_MINUTES", "180")))


def get_settings() -> Settings:
    return Settings()
