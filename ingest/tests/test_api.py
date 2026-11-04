from datetime import datetime, timedelta, timezone

import pytest

from ingest import email_ingest
from ingest.config import PACKAGE_TO_APP

T0 = datetime(2026, 11, 3, 19, 30, tzinfo=timezone.utc)


def notif(**kw):
    base = {"instance": "uk-c4-fotmob", "package": "com.mobilefootie.wc2010", "posted_at": T0.isoformat(),
            "channel_id": "goals", "title": "GOAL", "text": "Arsenal 1-0", "ongoing": False, "extras": {},
            "notification_key": "0|com.mobilefootie.wc2010|1|null|10123"}
    base.update(kw)
    return base


def test_packages_match_apps_table(conn):
    rows = dict(conn.execute("select package, id from apps").fetchall())
    assert rows == PACKAGE_TO_APP


def test_seed_counts(conn):
    assert conn.execute("select count(*) from cohorts").fetchone()[0] == 12
    assert conn.execute("select count(*) from instances").fetchone()[0] == 40
    assert conn.execute("select count(*) from instances where kind='atlas'").fetchone()[0] == 6


def test_push_row_has_cohort_market_campaign(client, clean):
    r = client.post("/v1/notification", json=notif(extras={"cid": "camp-1"}))
    assert r.status_code == 201, r.text
    row = clean.execute("select app, cohort_id, market, channel, campaign_id, crm_vendor, package "
                        "from events where id = %s", (r.json()["id"],)).fetchone()
    assert row == ("fotmob", "C4", "UK", "push", "camp-1", "braze", "com.mobilefootie.wc2010")


def test_epoch_millis_posted_at(client, clean):
    r = client.post("/v1/notification", json=notif(posted_at=int(T0.timestamp() * 1000)))
    assert r.status_code == 201
    assert clean.execute("select local_time from events").fetchone()[0] == T0


def test_ongoing_is_live_update(client):
    r = client.post("/v1/notification", json=notif(ongoing=True))
    assert r.json()["channel"] == "live_update"


def test_unknown_package_and_instance(client):
    assert client.post("/v1/notification", json=notif(package="com.example")).status_code == 422
    assert client.post("/v1/notification", json=notif(instance="nope")).status_code == 422


def test_campaign_dedupe_window(client, clean):
    first = client.post("/v1/notification", json=notif(extras={"cid": "c"}))
    dup = client.post("/v1/notification", json=notif(extras={"cid": "c"}, text="different text",
                                                     posted_at=(T0 + timedelta(seconds=59)).isoformat()))
    later = client.post("/v1/notification", json=notif(extras={"cid": "c"},
                                                       posted_at=(T0 + timedelta(seconds=61)).isoformat()))
    other_inst = client.post("/v1/notification", json=notif(extras={"cid": "c"}, instance="uk-c3-fotmob"))
    assert first.status_code == 201
    assert dup.status_code == 200 and dup.json()["duplicate"] and dup.json()["id"] == first.json()["id"]
    assert later.status_code == 201
    assert other_inst.status_code == 201
    assert clean.execute("select count(*) from events").fetchone()[0] == 3


def test_live_update_row_per_change(client, clean):
    for sec, txt in [(0, "1-0 23'"), (10, "1-0 23'"), (20, "2-0 30'"), (90, "2-0 30'")]:
        client.post("/v1/notification", json=notif(ongoing=True, text=txt, extras={"cid": "live-card"},
                                                   posted_at=(T0 + timedelta(seconds=sec)).isoformat()))
    rows = clean.execute("select body from events order by local_time").fetchall()
    # exact repeat within 60 s dropped; change kept; repeat after 60 s kept
    assert [r[0] for r in rows] == ["1-0 23'", "2-0 30'", "2-0 30'"]


def test_removed_action_does_not_collide(client, clean):
    client.post("/v1/notification", json=notif())
    r = client.post("/v1/notification", json=notif(action="removed"))
    assert r.status_code == 201


def test_match_event_join_and_engagement(client, clean):
    clean.execute(
        """insert into match_events (source, source_event_id, match_id, sport, competition, home, away,
             event_type, sub_type, minute, occurred_at, payload)
           values ('t', '1', 'm1', 'football', 'Premier League', 'Arsenal', 'Chelsea', 'score', 'goal', 23, %s,
                   '{"score": "1-0"}'),
                  ('t', '2', 'm1', 'football', 'Premier League', 'Arsenal', 'Chelsea', 'discipline', 'yellow_card', 25,
                   %s, '{}')""", (T0, T0 + timedelta(minutes=2)))
    clean.execute("insert into sessions (instance_id, started_at, script) values "
                  "('uk-c4-fotmob', %s, 'x'), ('uk-c4-fotmob', %s, 'x'), ('uk-c4-fotmob', %s, 'x')",
                  (T0 - timedelta(days=3, hours=1), T0 - timedelta(days=9), T0 + timedelta(hours=1)))
    r = client.post("/v1/notification", json=notif(
        title="GOAL! Arsenal 1-0 Chelsea", text="Saka", posted_at=(T0 + timedelta(minutes=3)).isoformat()))
    row = clean.execute("select send_class, match_id, seconds_since_event, days_since_last_open, opens_last_7d "
                        "from events where id = %s", (r.json()["id"],)).fetchone()
    # the quoted score picks the goal, not the later yellow card
    assert row == ("match_event", "m1", 180, 3, 1)


def test_prematch_reminder_is_match_scheduled(client, clean):
    clean.execute(
        """insert into match_events (source, source_event_id, match_id, sport, home, away, event_type, occurred_at, payload)
           values ('t', 's', 'm2', 'football', 'Arsenal', 'Chelsea', 'scheduled', %s, %s::jsonb)""",
        (T0 - timedelta(days=1), '{"kickoff": "%s"}' % T0.isoformat()))
    r = client.post("/v1/notification", json=notif(title="Kick-off in 15", text="Arsenal v Chelsea",
                                                   posted_at=(T0 - timedelta(minutes=15)).isoformat()))
    row = clean.execute("select send_class, seconds_since_event from events where id = %s",
                        (r.json()["id"],)).fetchone()
    assert row == ("match_scheduled", -900)


@pytest.mark.parametrize("title,text,expected", [
    ("FotMob Pro: 50% off", "Offer ends Sunday", "commercial"),
    ("We miss you!", "Come back", "lifecycle"),
    ("Transfer news", "Club signs striker", "content"),
])
def test_keyword_classes(client, clean, title, text, expected):
    r = client.post("/v1/notification", json=notif(title=title, text=text))
    assert clean.execute("select send_class from events where id = %s", (r.json()["id"],)).fetchone()[0] == expected


def test_downtime_adjacent(client, clean):
    clean.execute("insert into instance_downtime (instance_id, started_at, ended_at, reason) values "
                  "('uk-c4-fotmob', %s, %s, 'nightly_restart')", (T0 - timedelta(minutes=20), T0 - timedelta(minutes=5)))
    r = client.post("/v1/notification", json=notif())
    assert clean.execute("select downtime_adjacent from events where id = %s", (r.json()["id"],)).fetchone()[0]
    far = client.post("/v1/notification", json=notif(posted_at=(T0 + timedelta(hours=2)).isoformat(), text="x"))
    assert not clean.execute("select downtime_adjacent from events where id = %s", (far.json()["id"],)).fetchone()[0]
    # a later downtime window re-flags already-stored events
    clean.execute("insert into instance_downtime (instance_id, started_at, ended_at, reason) values "
                  "('uk-c4-fotmob', %s, %s, 'crash')", (T0 + timedelta(hours=2, minutes=3), T0 + timedelta(hours=3)))
    assert clean.execute("select refresh_downtime_adjacent()").fetchone()[0] == 1


def _summary(to="uk-c5-fotmob@example.test", mid="m-1", subject="Weekly digest"):
    return {"ID": f"mp-{mid}", "MessageID": f"{mid}@fake.test", "From": {"Name": "X", "Address": "news@x.test"},
            "To": [{"Name": "", "Address": to}], "Subject": subject, "Snippet": "hello",
            "Created": "2026-11-03T10:00:00Z"}


def test_email_webhook(client, clean, monkeypatch):
    monkeypatch.setattr(email_ingest, "fetch_full", lambda url, mid, client=None: (
        {"Text": "Arsenal news", "HTML": '<a href="https://x.test/?utm_campaign=wk">'},
        {"Date": ["Tue, 03 Nov 2026 10:00:00 +0000"], "X-Campaign-Id": ["weekly-1"]}))
    r = client.post("/v1/email", json=_summary())
    assert r.status_code == 201, r.text
    row = clean.execute("select channel, app, cohort_id, market, campaign_id, crm_vendor, title, local_time "
                        "from events where id = %s", (r.json()["id"],)).fetchone()
    assert row[:7] == ("email", "fotmob", "C5", "UK", "weekly-1", "header", "Weekly digest")
    assert row[7] == datetime(2026, 11, 3, 10, tzinfo=timezone.utc)
    again = client.post("/v1/email", json=_summary())
    assert again.status_code == 200 and again.json()["duplicate"]


def test_email_unknown_recipient(client, monkeypatch):
    monkeypatch.setattr(email_ingest, "fetch_full", lambda *a, **k: ({}, {}))
    r = client.post("/v1/email", json=_summary(to="someone@else.test"))
    assert r.status_code == 202 and r.json()["ignored"]


def test_sessions_endpoints(client, clean):
    sid = client.post("/v1/sessions", json={"instance_id": "uk-c3-fotmob", "script": "daily_open"}).json()["id"]
    r = client.post(f"/v1/sessions/{sid}/end", json={"screens": [{"screen": "home", "sport": "football"}]})
    assert r.status_code == 200
    ended, screens = clean.execute("select ended_at, screens from sessions where id = %s", (sid,)).fetchone()
    assert ended is not None and screens[0]["sport"] == "football"
    assert client.post("/v1/sessions/999999/end", json={}).status_code == 404
