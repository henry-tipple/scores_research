"""Feed poller. Hourly by default; every minute while any followed (or sample) fixture is live.

  python -m feeds.poller            # run forever
  python -m feeds.poller --once     # one poll, print what happened
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import yaml

from .adapters import FeedAdapter, get_adapter
from .model import Fixture
from .store import followed_names, upsert_events

log = logging.getLogger("feeds.poller")
CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def load_config(path: Path | None = None) -> dict:
    text = (path or Path(os.environ.get("FEEDS_CONFIG", CONFIG))).read_text()
    text = re.sub(r"\$\{([A-Z0-9_]+):-([^}]*)\}", lambda m: os.environ.get(m.group(1), m.group(2)), text)
    return yaml.safe_load(text)


def relevant(fixtures: list[Fixture], adapter: FeedAdapter, followed: set[str]) -> list[Fixture]:
    """Sample fixtures always count; real fixtures count when a cohort follows a side."""
    if adapter.name == "fake":
        return fixtures
    return [f for f in fixtures if f.involves(followed)]


def next_interval(fixtures: list[Fixture], now: datetime, cfg: dict) -> int:
    lead = timedelta(minutes=cfg.get("live_lead_minutes", 10))
    if any(f.is_live(now, lead) for f in fixtures):
        return int(cfg["interval_live_seconds"])
    # Never sleep past the next kick-off's lead window.
    upcoming = [f.kickoff - lead for f in fixtures if f.kickoff - lead > now]
    idle = int(cfg["interval_idle_seconds"])
    if upcoming:
        idle = min(idle, max(int((min(upcoming) - now).total_seconds()), int(cfg["interval_live_seconds"])))
    return idle


def poll_once(conn, adapter: FeedAdapter, cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=cfg.get("lookback_minutes", 180))
    inserted, updated = upsert_events(conn, adapter.events(since, now))
    fixtures = relevant(adapter.fixtures(now - timedelta(hours=12), now + timedelta(hours=24)),
                        adapter, followed_names(conn))
    interval = next_interval(fixtures, now, cfg)
    live = [f.match_id for f in fixtures if f.is_live(now)]
    return {"inserted": inserted, "updated": updated, "live": live, "next_interval_s": interval}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true")
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config()
    adapter = get_adapter(cfg["adapter"], **(cfg.get("adapter_config") or {}))
    with psycopg.connect(a.database_url) as conn:
        while True:
            result = poll_once(conn, adapter, cfg)
            log.info("poll %s", result)
            if a.once:
                return 0
            time.sleep(result["next_interval_s"])


if __name__ == "__main__":
    raise SystemExit(main())
