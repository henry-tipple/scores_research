import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import health, restart, waves
from ops.common import Downtime, DryRunner, Result, load_instances, wave_is_on

UTC = timezone.utc


@pytest.fixture(scope="module")
def fleet_instances():
    return load_instances()


def _inst(instances, iid):
    return next(i for i in instances if i.id == iid)


# ---------------------------------------------------------------- restart

def test_restart_slots_local_0400_staggered(fleet_instances):
    _, instances = fleet_instances
    now = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)
    slots = restart.slots_for(instances, now)
    assert len(slots) == 38
    uk = [s for s in slots if s.instance.tz == "Europe/London"]
    assert uk[0].at == datetime(2026, 11, 4, 4, 0, tzinfo=UTC)          # 04:00 GMT
    assert all(b.at - a.at == timedelta(minutes=2) for a, b in zip(uk, uk[1:]))
    jp = [s for s in slots if s.instance.market == "JP"]
    assert jp[0].at == datetime(2026, 11, 3, 19, 0, tzinfo=UTC)         # 04:00 JST on local 4 Nov
    # Berlin, Rome and Madrid share 03:00 UTC: one queue, nobody collides
    cet = sorted(s.at for s in slots if s.instance.market in ("DE", "IT", "ES"))
    assert cet[0] == datetime(2026, 11, 4, 3, 0, tzinfo=UTC) and len(set(cet)) == len(cet) == 9


def test_restart_tick_restarts_and_logs_downtime(fleet_instances, clean, tmp_path):
    fleet, instances = fleet_instances
    runner = DryRunner(responses={"sys.boot_completed": Result(0, "1\n")})
    now = datetime(2026, 11, 4, 4, 10, 30, tzinfo=UTC)   # a Wednesday: some atlas waves are off
    # 30-min catch-up window so the CET queue (due 03:00 UTC) is already given up
    log = restart.tick(now, runner, clean, tmp_path, give_up_after=timedelta(minutes=30), sleep=lambda s: None,
                       fleet=fleet, instances=instances)
    first = next(s for s in restart.slots_for(instances, now) if s.instance.tz == "Europe/London"
                 and wave_is_on(fleet, s.instance, now)).instance
    assert log[0] == f"restart {first.id}: ok"
    assert all(line.startswith("defer") for line in log[1:])  # one restart per tick
    assert ["docker", "restart", "--time", "20", first.container] in runner.calls
    assert any(c[:3] == ["docker", "exec", first.container] and "route" in c for c in runner.calls)
    row = clean.execute("select reason, ended_at is not null, detail->>'local_date' from instance_downtime "
                        "where instance_id = %s", (first.id,)).fetchone()
    assert row == ("nightly_restart", True, "2026-11-04")
    # same local date: not restarted twice
    log2 = restart.tick(now + timedelta(minutes=1), DryRunner(responses={"sys.boot_completed": Result(0, "1")}),
                        clean, tmp_path, give_up_after=timedelta(minutes=30), sleep=lambda s: None,
                        fleet=fleet, instances=instances)
    assert all(first.id not in line for line in log2)


def test_restart_skips_c11_during_live_visit(fleet_instances, clean, tmp_path):
    fleet, instances = fleet_instances
    now = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)
    c11_slot = next(s for s in restart.slots_for(instances, now) if s.instance.cohort_id == "C11")
    c11 = c11_slot.instance
    at = c11_slot.at + timedelta(seconds=10)
    clean.execute("insert into sessions (instance_id, started_at, script) values (%s, %s, 'c11_multisport.live')",
                  (c11.id, at - timedelta(minutes=30)))
    runner = DryRunner(responses={"sys.boot_completed": Result(0, "1")})
    log = restart.tick(at, runner, clean, tmp_path, max_per_tick=40, sleep=lambda s: None,
                       fleet=fleet, instances=instances)
    assert f"skip {c11.id}: live visit running, will retry" in log
    assert not any(c11.container in c for call in runner.calls for c in call)
    # lock file works too, even without a session row
    clean.execute("update sessions set ended_at = now()")
    (tmp_path / f"{c11.id}.lock").touch()
    assert restart.live_visit_running(c11, clean, tmp_path, datetime.now(UTC))


def test_restart_failed_boot_leaves_downtime_open(fleet_instances, clean, tmp_path, monkeypatch):
    fleet, instances = fleet_instances
    slot = restart.slots_for(instances, datetime(2026, 11, 4, 2, 0, tzinfo=UTC))[0]
    ok = restart.restart_instance(slot, DryRunner(responses={"sys.boot_completed": Result(0, "0")}),
                                  Downtime(clean), boot_timeout=0, sleep=lambda s: None)
    assert not ok
    assert clean.execute("select ended_at from instance_downtime").fetchone()[0] is None


# ---------------------------------------------------------------- health

def test_health_checks(fleet_instances, tmp_path, monkeypatch):
    fleet, instances = fleet_instances
    monkeypatch.setenv("SCREENSHOT_ROOT", str(tmp_path))
    c3 = _inst(instances, "uk-c3-fotmob")
    (tmp_path / c3.id).mkdir()
    (tmp_path / c3.id / "fresh.png").touch()
    runner = DryRunner(responses={
        "127.0.0.1:%d shell echo" % _inst(instances, "uk-c4-fotmob").adb_port: Result(1, "device offline"),
        "echo ok": Result(0, "ok"),
        "pidof": Result(0, "1234"),
        "enabled_notification_listeners": Result(0, "com.dazn.study.notiflogger/.NotifLoggerService"),
        "publicip": Result(0, "203.0.113.7"),
    })
    now = datetime.now(UTC)
    checks = health.run_checks(runner, now, instances=instances, fleet=fleet)
    by = {(c.instance, c.name): c for c in checks}
    assert by[("vpn-uk", "vpn")].ok
    assert by[("uk-c3-fotmob", "adb")].ok and by[("uk-c3-fotmob", "logger")].ok
    assert by[("uk-c3-fotmob", "screenshot")].ok
    assert not by[("uk-c4-fotmob", "adb")].ok
    assert not by[("uk-c5-fotmob", "screenshot")].ok              # no screenshots at all
    assert ("uk-c1-fotmob", "screenshot") not in by               # C1 dormant never opens the app


def test_health_screenshot_rules():
    _, instances = load_instances()
    c2 = _inst(instances, "uk-c2-fotmob")
    start = date(2026, 11, 1)
    assert health.expects_screenshots(c2, date(2026, 11, 5), start)
    assert not health.expects_screenshots(c2, date(2026, 11, 20), start)


def test_health_alerts_only_on_change(tmp_path):
    state = tmp_path / "state.json"
    sent = []
    post = lambda url, json, timeout: sent.append(json)  # noqa: E731
    os.environ["SLACK_WEBHOOK_URL"] = "https://hooks.example.invalid/x"
    try:
        fail = [health.Check("a", "adb", False, "offline"), health.Check("b", "adb", True)]
        assert "NEW a:adb" in health.diff_and_alert(fail, state, post=post)
        assert health.diff_and_alert(fail, state, post=post) is None      # unchanged: quiet
        assert "recovered a:adb" in health.diff_and_alert([health.Check("a", "adb", True)], state, post=post)
        assert len(sent) == 2
    finally:
        del os.environ["SLACK_WEBHOOK_URL"]


def test_health_failure_opens_and_closes_downtime(clean):
    dt = Downtime(clean)
    t = datetime(2026, 11, 4, 12, tzinfo=UTC)
    health.record_downtime([health.Check("uk-c4-fotmob", "adb", False)], dt, t)
    health.record_downtime([health.Check("uk-c4-fotmob", "adb", False)], dt, t + timedelta(minutes=15))
    assert clean.execute("select count(*) from instance_downtime").fetchone()[0] == 1
    health.record_downtime([health.Check("uk-c4-fotmob", "adb", True)], dt, t + timedelta(minutes=30))
    assert clean.execute("select ended_at from instance_downtime").fetchone()[0] == t + timedelta(minutes=30)


# ---------------------------------------------------------------- waves

def test_waves_reconcile(fleet_instances, clean):
    fleet, instances = fleet_instances
    monday = datetime(2026, 11, 2, 12, tzinfo=UTC)
    # pretend every container is running
    runner = DryRunner(responses={"{{.State.Running}}": Result(0, "true")})
    log = waves.reconcile(monday, runner, clean, fleet=fleet, instances=instances)
    stopped = sorted(line.split()[1] for line in log if line.startswith("stop"))
    # Monday: only atlas-365scores runs, so the other four atlas instances stop
    assert stopped == ["atlas-1-fotmob", "atlas-1-livescore", "atlas-2-fotmob", "atlas-2-livescore"]
    assert clean.execute("select count(*) from instance_downtime where reason='wave_off' and ended_at is null"
                         ).fetchone()[0] == 4
    # Tuesday: livescore atlas comes back, its downtime closes
    runner = DryRunner(responses={"atlas-1-livescore": Result(0, "false"), "atlas-2-livescore": Result(0, "false"),
                                  "{{.State.Running}}": Result(0, "true")})
    log = waves.reconcile(monday + timedelta(days=1), runner, clean, fleet=fleet, instances=instances)
    assert "start atlas-1-livescore (atlas-livescore)" in log
    assert clean.execute("select count(*) from instance_downtime where reason='wave_off' and ended_at is not null"
                         ).fetchone()[0] == 2


def test_systemd_templates_present():
    root = Path(__file__).resolve().parents[2] / "infra" / "systemd"
    for name in ("health", "restart", "waves"):
        assert (root / f"study-{name}.service").exists() and (root / f"study-{name}.timer").exists()
    assert "OnCalendar=*:0/15" in (root / "study-health.timer").read_text()
