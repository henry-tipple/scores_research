"""Post a realistic fake day of notifications and emails through the real ingest paths.

1. Replays the fake match feed (Arsenal v Chelsea, Lakers v Celtics) into match_events.
2. Opens/closes scripted sessions via POST /v1/sessions.
3. Posts pushes and live-update cards via POST /v1/notification (including deliberate
   duplicates, which must be dropped).
4. Sends emails over SMTP to Mailpit; Mailpit's webhook delivers them to /v1/email.

Usage: python -m ingest.fake_events [--date YYYY-MM-DD] [--ingest-url URL]
Re-running for the same date is idempotent (duplicates are detected).
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

import httpx
import psycopg

from feeds.adapters.fake import FakeReplayAdapter
from feeds.store import upsert_events

PKG = {"365scores": "com.scores365", "fotmob": "com.mobilefootie.wc2010", "livescore": "com.livescore"}


def at(day: date, hhmm: str, sec: int = 0) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, dtime(h, m, sec), tzinfo=timezone.utc)


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(parts)))


def build_pushes(day: date) -> list[dict]:
    d = day.isoformat()
    out: list[dict] = []

    def push(instance, app, hhmm, title, text, channel_id, sec=0, extras=None, ongoing=False, big=None, nid=None):
        n = len(out)
        out.append({
            "instance": instance, "package": PKG[app], "posted_at": at(day, hhmm, sec).isoformat(),
            "channel_id": channel_id, "title": title, "text": text, "big_text": big, "ongoing": ongoing,
            "notification_key": f"0|{PKG[app]}|{nid or 1000 + n}|null|10123",
            "extras": {"android.title": title, "android.text": text, **(extras or {})},
            "logger_version": "fake-0.1",
        })

    braze = lambda name: {"cid": f"braze-{name}-{d}", "ab_uri": f"livescore://campaign/{name}"}  # noqa: E731
    onesig = lambda name: {"custom": json.dumps({"i": stable_id("os", name, d), "u": f"scores365://{name}"})}  # noqa: E731

    # --- C4 power fan on FotMob: every toggle on, Arsenal v Chelsea 19:30 UTC
    i = "uk-c4-fotmob"
    push(i, "fotmob", "07:45", "Morning briefing", "Arsenal v Chelsea tonight, plus 4 more fixtures you follow", "news")
    push(i, "fotmob", "12:10", "FotMob Pro: 50% off", "Remove ads and get extra stats. Offer ends Sunday.", "promo",
         extras={"cid": f"braze-pro-sale-{d}"})
    push(i, "fotmob", "18:31", "Arsenal lineup", "Saka, Odegaard, Rice start against Chelsea", "lineups")
    push(i, "fotmob", "19:15", "Kick-off in 15 min", "Arsenal v Chelsea, Premier League", "match_reminder")
    push(i, "fotmob", "19:30", "Kick-off", "Arsenal 0-0 Chelsea", "match_start", sec=40)
    push(i, "fotmob", "19:53", "GOAL! Arsenal 1-0 Chelsea", "Bukayo Saka (23')", "goals", sec=31, nid=77)
    push(i, "fotmob", "19:53", "GOAL! Arsenal 1-0 Chelsea", "Bukayo Saka (23')", "goals", sec=45, nid=77)  # dup
    push(i, "fotmob", "20:08", "Yellow card", "Moises Caicedo (Chelsea) 38'", "cards")
    push(i, "fotmob", "20:17", "Half-time", "Arsenal 1-0 Chelsea", "period")
    push(i, "fotmob", "20:49", "GOAL! Arsenal 1-1 Chelsea", "Cole Palmer (63')", "goals", sec=20)
    push(i, "fotmob", "21:11", "GOAL! Arsenal 2-1 Chelsea", "Declan Rice (85')", "goals", sec=15)
    push(i, "fotmob", "21:23", "Full-time", "Arsenal 2-1 Chelsea", "match_end", sec=30)
    # live-update card: reposted on every change; identical repost at 19:53:50 must be dropped
    for hhmm, sec, txt in [("19:30", 41, "Arsenal 0-0 Chelsea · 1'"), ("19:53", 32, "Arsenal 1-0 Chelsea · 23'"),
                           ("19:53", 50, "Arsenal 1-0 Chelsea · 23'"), ("20:17", 5, "HT · Arsenal 1-0 Chelsea"),
                           ("20:49", 21, "Arsenal 1-1 Chelsea · 63'"), ("21:11", 16, "Arsenal 2-1 Chelsea · 85'"),
                           ("21:23", 31, "FT · Arsenal 2-1 Chelsea")]:
        push(i, "fotmob", hhmm, "Arsenal v Chelsea", txt, "live_match", sec=sec, ongoing=True, nid=4242)

    # --- C3 daily light on LiveScore (Braze campaigns)
    i = "uk-c3-livescore"
    push(i, "livescore", "09:00", "Transfer latest", "Arsenal eye January move for striker", "news", extras=braze("transfer"))
    push(i, "livescore", "09:00", "Transfer latest", "Arsenal eye January move for striker", "news",
         sec=20, extras=braze("transfer"))  # dup: same campaign within 60 s
    push(i, "livescore", "19:00", "Arsenal v Chelsea", "Starts in 30 minutes. Tap for lineups.", "match_reminder",
         extras=braze("prematch"))
    push(i, "livescore", "19:53", "Goal - Arsenal 1-0 Chelsea", "Saka 23'", "goal", sec=50)
    push(i, "livescore", "20:49", "Goal - Arsenal 1-1 Chelsea", "Palmer 63'", "goal", sec=40)
    push(i, "livescore", "21:11", "Goal - Arsenal 2-1 Chelsea", "Rice 85'", "goal", sec=35)
    push(i, "livescore", "21:24", "Full time", "Arsenal 2-1 Chelsea", "result")

    # --- C3 daily light on 365Scores (OneSignal campaigns)
    i = "uk-c3-365scores"
    push(i, "365scores", "08:30", "Your daily scores", "3 matches today for teams you follow", "digest",
         extras=onesig("digest"))
    push(i, "365scores", "19:54", "Arsenal 1-0 Chelsea", "GOAL! Saka 23'", "goals", sec=5)
    push(i, "365scores", "21:23", "Arsenal 2-1 Chelsea", "Final score", "final", sec=50)
    push(i, "365scores", "21:23", "Arsenal 0-0 Chelsea", "Live", "live", sec=55, ongoing=True, nid=901)

    # --- C7 Germany on 365Scores (German copy)
    i = "de-c7-365scores"
    push(i, "365scores", "07:30", "Bayern München", "Kompany vor dem Topspiel: \"Wir sind bereit\"", "news",
         extras=onesig("de-news"))
    push(i, "365scores", "16:00", "Exklusives Angebot", "365Scores Premium jetzt 30% Rabatt", "promo",
         extras=onesig("de-premium"))

    # --- C11 multi-sport on 365Scores: NBA overnight + Arsenal goal
    i = "uk-c11-365scores"
    push(i, "365scores", "00:31", "Lakers vs Celtics", "Tip-off! Follow live", "game_start")
    push(i, "365scores", "02:50", "LeBron clutch three!", "Lakers 109-107 Celtics, 0:41 left", "highlights")
    push(i, "365scores", "02:56", "Final: Lakers 112-108 Celtics", "LeBron James 34 PTS", "final")
    push(i, "365scores", "19:54", "GOAL Arsenal", "Arsenal 1-0 Chelsea", "goals", sec=12)

    # --- C1 dormant on LiveScore: lifecycle nudge only
    i = "uk-c1-livescore"
    push(i, "livescore", "11:00", "We miss you!", "Come back and see what Arsenal have been up to", "engagement",
         extras=braze("winback"))
    return out


def build_sessions(day: date) -> list[tuple[str, str, datetime, datetime]]:
    s = []
    for hhmm in ("08:00", "13:00", "20:00"):
        s.append(("uk-c4-fotmob", "power_fan", at(day, hhmm), at(day, hhmm) + timedelta(minutes=3)))
    s.append(("uk-c4-fotmob", "power_fan.live", at(day, "19:35"), at(day, "21:25")))
    s.append(("uk-c3-livescore", "daily_open", at(day, "08:00"), at(day, "08:02")))
    s.append(("uk-c3-365scores", "daily_open", at(day, "08:00"), at(day, "08:02")))
    s.append(("de-c7-365scores", "daily_open", at(day, "07:00"), at(day, "07:02")))
    s.append(("uk-c11-365scores", "c11_multisport", at(day, "08:00"), at(day, "08:04")))
    s.append(("uk-c5-fotmob", "daily_open", at(day, "08:00"), at(day, "08:02")))
    return s


def build_emails(day: date, domain: str) -> list[EmailMessage]:
    d = day.isoformat()
    mails = []

    def mail(to_local, sender, subject, text, hhmm, headers=None, html=None):
        m = EmailMessage()
        m["From"] = sender
        m["To"] = f"{to_local}@{domain}"
        m["Subject"] = subject
        m["Date"] = format_datetime(at(day, hhmm))
        m["Message-ID"] = f"<{stable_id('mail', to_local, subject, d)}@fake.test>"
        for k, v in (headers or {}).items():
            m[k] = v
        m.set_content(text)
        if html:
            m.add_alternative(html, subtype="html")
        mails.append(m)

    mail("uk-c5-fotmob", "FotMob <news@fotmob.fake.test>", "Your weekly football digest",
         "Arsenal beat Chelsea 2-1. Here is everything you missed this week.", "10:00",
         headers={"X-Campaign-Id": f"weekly-digest-{d}"})
    mail("uk-c1-livescore", "LiveScore <hello@livescore.fake.test>", "It's been a while",
         "We haven't seen you in a while. Come back for live scores.", "11:05",
         html=f'<p>Come back!</p><a href="https://livescore.fake.test/r?utm_source=crm&utm_campaign=winback-{d}">Open</a>')
    mail("de-c7-365scores", "365Scores <news@365scores.fake.test>", "Bayern: Die Woche im Überblick",
         "Alles zu Bayern München in dieser Woche.", "08:15",
         headers={"X-Campaign-Id": f"de-weekly-{d}"})
    mail("uk-c4-fotmob", "FotMob <promo@fotmob.fake.test>", "FotMob Pro - 50% off this week",
         "Upgrade to FotMob Pro. Offer ends Sunday.", "12:15",
         html=f'<a href="https://fotmob.fake.test/pro?utm_campaign=pro-sale-{d}">Get Pro</a>')
    return mails


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", type=date.fromisoformat,
                   default=(datetime.now(timezone.utc) - timedelta(days=1)).date())
    p.add_argument("--ingest-url", default=os.environ.get("INGEST_URL", "http://localhost:8000"))
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--smtp", default=os.environ.get("MAILPIT_SMTP", "localhost:1025"))
    p.add_argument("--domain", default=os.environ.get("STUDY_EMAIL_DOMAIN", "example.test"))
    p.add_argument("--wait", type=int, default=30, help="seconds to wait for Mailpit webhooks")
    a = p.parse_args(argv)
    day = a.date

    with psycopg.connect(a.database_url) as conn:
        adapter = FakeReplayAdapter(anchor=day)
        ins, upd = upsert_events(conn, adapter.events(at(day, "00:00") - timedelta(days=2),
                                                      at(day, "00:00") + timedelta(days=1)))
        print(f"match_events: {ins} inserted, {upd} updated (fake feed anchored {day})")

    created = dup = 0
    with httpx.Client(base_url=a.ingest_url, timeout=10) as http:
        for instance, script, start, end in build_sessions(day):
            r = http.post("/v1/sessions", json={"instance_id": instance, "script": script,
                                                "started_at": start.isoformat()})
            r.raise_for_status()
            http.post(f"/v1/sessions/{r.json()['id']}/end", json={"ended_at": end.isoformat()}).raise_for_status()
        for n in build_pushes(day):
            r = http.post("/v1/notification", json=n)
            r.raise_for_status()
            created += r.status_code == 201
            dup += r.json()["duplicate"]
    print(f"notifications: {created} created, {dup} duplicates dropped")

    host, port = a.smtp.split(":")
    mails = build_emails(day, a.domain)
    with smtplib.SMTP(host, int(port)) as smtp:
        for m in mails:
            smtp.send_message(m)
    print(f"emails: {len(mails)} sent to Mailpit at {a.smtp}; waiting for webhook...")

    with psycopg.connect(a.database_url) as conn:
        ids = [m["Message-ID"].strip("<>") for m in mails]
        deadline = time.time() + a.wait
        got = 0
        def count() -> int:
            return conn.execute("select count(*) from events where channel = 'email' and extras->>'message_id' = any(%s)",
                                (ids,)).fetchone()[0]
        got = 0
        while time.time() < deadline:
            got = count()
            if got >= len(ids):
                break
            time.sleep(1)
        print(f"emails delivered by Mailpit webhook: {got}/{len(ids)}")
        if got < len(ids):
            r = httpx.post(f"{a.ingest_url}/v1/email/sync", timeout=30).json()
            got = count()
            print(f"email sync backfilled {r['created']}; emails in events: {got}/{len(ids)}")
        print("\nevents for", day)
        rows = conn.execute(
            """select channel, send_class, count(*), count(cohort_id), count(market), count(campaign_id)
               from events where local_time::date = %s group by 1, 2 order by 1, 2""", (day,)).fetchall()
        print(f"  {'channel':12} {'send_class':16} {'rows':>4} {'cohort':>6} {'market':>6} {'campaign':>8}")
        for r in rows:
            print(f"  {r[0]:12} {r[1] or '-':16} {r[2]:>4} {r[3]:>6} {r[4]:>6} {r[5]:>8}")
    return 0 if got >= len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
