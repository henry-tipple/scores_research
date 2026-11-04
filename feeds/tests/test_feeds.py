from datetime import date, datetime, timedelta, timezone

import pytest

from feeds.adapters import FeedAdapter, get_adapter
from feeds.adapters.fake import FakeReplayAdapter
from feeds.adapters.sportradar import EVENT_MAP
from feeds.model import EVENT_TYPES, NormalizedEvent
from feeds.poller import load_config, next_interval, poll_once
from feeds.store import followed_names, upsert_events

DAY = date(2026, 11, 3)
UTC = timezone.utc


def at(h, m=0, d=DAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


@pytest.fixture
def fake():
    return FakeReplayAdapter(anchor=DAY)


def test_adapters_satisfy_interface(fake):
    assert isinstance(fake, FeedAdapter)
    assert isinstance(get_adapter("sportradar"), FeedAdapter)
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_fake_replays_football_and_nba(fake):
    evs = list(fake.events(at(0) - timedelta(days=2), at(23, 59)))
    sports = {e.sport for e in evs}
    assert sports == {"football", "basketball"}
    goals = [e for e in evs if e.sport == "football" and e.sub_type == "goal"]
    assert [e.payload["score"] for e in goals] == ["1-0", "1-1", "2-1"]
    assert all(e.event_type in EVENT_TYPES for e in evs)
    assert goals[0].occurred_at == at(19, 53)


def test_fake_realtime_reveals_progressively(fake):
    so_far = list(fake.events(at(18, 0), at(20, 0)))
    assert {e.event_type for e in so_far} == {"lineups", "start", "score"}


def test_normalized_event_validates():
    with pytest.raises(ValueError):
        NormalizedEvent("s", "1", "m", "football", None, None, None, "goal", None, None, at(1))
    with pytest.raises(ValueError):
        NormalizedEvent("s", "1", "m", "football", None, None, None, "score", None, None, datetime(2026, 1, 1))


def test_poll_interval_live_vs_idle(fake):
    cfg = load_config()
    fixtures = fake.fixtures(at(0) - timedelta(hours=12), at(23, 59))
    assert next_interval(fixtures, at(20, 0), cfg) == 60           # Arsenal v Chelsea live
    assert next_interval(fixtures, at(19, 25), cfg) == 60          # inside the 10-min lead
    assert next_interval(fixtures, at(12, 0), cfg) == 3600         # idle: hourly
    # never sleep past the next kick-off's lead window
    assert next_interval(fixtures, at(18, 50), cfg) == 30 * 60


def test_sportradar_stub_makes_no_calls():
    sr = get_adapter("sportradar")
    with pytest.raises(NotImplementedError):
        sr.fixtures(at(0), at(1))
    with pytest.raises(NotImplementedError):
        list(sr.events(at(0), at(1)))
    for sport, mapping in EVENT_MAP.items():
        for event_type, _ in mapping.values():
            assert event_type in EVENT_TYPES, (sport, event_type)
    assert sr.map_event("football", "score_change") == ("score", "goal")


def test_upsert_idempotent_and_poll_once(clean, fake):
    evs = list(fake.events(at(0) - timedelta(days=2), at(23, 59)))
    assert upsert_events(clean, evs) == (len(evs), 0)
    assert upsert_events(clean, evs) == (0, len(evs))
    assert "Arsenal" in followed_names(clean)
    cfg = load_config()
    res = poll_once(clean, fake, cfg, now=at(20, 0))
    assert res["next_interval_s"] == 60 and res["live"] == ["fake-epl-ars-che-2026-11-03"]
