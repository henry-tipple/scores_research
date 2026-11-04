"""Nightly restart at 04:00 market-local time, staggered 2 minutes apart.

Run `tick` every minute (infra/systemd/study-restart.timer). Each tick restarts at most
`--max-per-tick` instances whose slot has arrived and that have not been restarted for
that local date yet. Slots: 04:00 local + 2 min x position among instances whose local
04:00 falls at the same instant. An instance that cannot restart (e.g. C11 mid live visit) is retried on
later ticks until `--give-up-after` minutes past its slot.

Every restart is a row in instance_downtime (reason nightly_restart) covering the stop
until the device has booted and its VPN route is restored.

  python -m ops.restart plan                 # print tonight's schedule
  python -m ops.restart tick [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fleet.generate import Instance

from .common import Downtime, DryRunner, Runner, adb, connect_db, load_instances, now_utc, route_fix_cmd, run, wave_is_on

RESTART_AT = dtime(4, 0)
STAGGER = timedelta(minutes=2)
LIVE_VISIT_COHORTS = {"C11"}


@dataclass
class Slot:
    instance: Instance
    local_date: date
    at: datetime          # UTC


def slots_for(instances: list[Instance], now: datetime) -> list[Slot]:
    """Today's local 04:00 slot for every instance, in a stable order.

    Instances whose local 04:00 is the same instant (Berlin, Rome and Madrid share an
    offset) form one queue, so nobody restarts at the same moment as anybody else.
    """
    base: dict[datetime, list[tuple[Instance, date]]] = {}
    for i in instances:
        local_date = now.astimezone(ZoneInfo(i.tz)).date()
        at = datetime.combine(local_date, RESTART_AT, tzinfo=ZoneInfo(i.tz)).astimezone(timezone.utc)
        base.setdefault(at, []).append((i, local_date))
    out = []
    for at, group in base.items():
        for n, (inst, local_date) in enumerate(sorted(group, key=lambda g: g[0].id)):
            out.append(Slot(inst, local_date, at + STAGGER * n))
    return sorted(out, key=lambda s: (s.at, s.instance.id))


def live_visit_running(inst: Instance, conn, lock_dir: Path, now: datetime) -> bool:
    lock = lock_dir / f"{inst.id}.lock"
    if lock.exists() and now.timestamp() - lock.stat().st_mtime < 4 * 3600:
        return True
    if conn is not None:
        row = conn.execute(
            "select 1 from sessions where instance_id = %s and ended_at is null and script like '%%live%%' "
            "and started_at > %s - interval '4 hours' limit 1", (inst.id, now)).fetchone()
        return row is not None
    return False


def already_done(inst: Instance, local_date: date, conn) -> bool:
    if conn is None:
        return False
    return conn.execute(
        "select 1 from instance_downtime where instance_id = %s and reason = 'nightly_restart' "
        "and detail->>'local_date' = %s limit 1", (inst.id, local_date.isoformat())).fetchone() is not None


def wait_for_boot(inst: Instance, runner: Runner, timeout_s: int, sleep=time.sleep) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        runner([os.environ.get("ADB_BIN", "adb"), "connect", f"127.0.0.1:{inst.adb_port}"], timeout=10)
        r = runner(adb(inst, "shell", "getprop", "sys.boot_completed"), timeout=10)
        if r.ok and r.out.strip() == "1":
            return True
        if time.monotonic() > deadline:
            return False
        sleep(5)


def restart_instance(slot: Slot, runner: Runner, dt: Downtime, boot_timeout: int = 240, sleep=time.sleep) -> bool:
    inst = slot.instance
    did = dt.open(inst.id, "nightly_restart", detail={"local_date": slot.local_date.isoformat()})
    r = runner(["docker", "restart", "--time", "20", inst.container], timeout=120)
    booted = r.ok and wait_for_boot(inst, runner, boot_timeout, sleep)
    if booted:
        runner(route_fix_cmd(inst))
        dt.close(did)
    else:
        print(f"restart of {inst.id} did not complete (docker rc={r.rc}); downtime left open for health check")
    return booted


def tick(now: datetime, runner: Runner, conn, lock_dir: Path, max_per_tick: int = 1,
         give_up_after: timedelta = timedelta(hours=2), sleep=time.sleep, fleet=None, instances=None) -> list[str]:
    if instances is None:
        fleet, instances = load_instances()
    dt = Downtime(conn)
    log: list[str] = []
    done = 0
    for slot in slots_for(instances, now):
        inst = slot.instance
        if now < slot.at or now > slot.at + give_up_after:
            continue
        if not wave_is_on(fleet, inst, now) or already_done(inst, slot.local_date, conn):
            continue
        if inst.cohort_id in LIVE_VISIT_COHORTS and live_visit_running(inst, conn, lock_dir, now):
            log.append(f"skip {inst.id}: live visit running, will retry")
            continue
        if done >= max_per_tick:
            log.append(f"defer {inst.id}: max {max_per_tick} restart(s) per tick")
            continue
        ok = restart_instance(slot, runner, dt, sleep=sleep)
        log.append(f"restart {inst.id}: {'ok' if ok else 'FAILED'}")
        done += 1
    return log


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["plan", "tick"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-per-tick", type=int, default=1)
    p.add_argument("--give-up-after", type=int, default=120, help="minutes past the slot")
    a = p.parse_args(argv)
    now = now_utc()
    if a.command == "plan":
        _, instances = load_instances()
        for s in slots_for(instances, now):
            local = s.at.astimezone(ZoneInfo(s.instance.tz))
            print(f"{s.instance.id:24} {s.instance.tz:16} {local:%Y-%m-%d %H:%M} local  {s.at:%H:%M} UTC")
        return 0
    runner = DryRunner() if a.dry_run else run
    conn = None if a.dry_run else connect_db()
    lock_dir = Path(os.environ.get("LIVE_VISIT_LOCK_DIR", "/run/study/live-visit"))
    for line in tick(now, runner, conn, lock_dir, a.max_per_tick, timedelta(minutes=a.give_up_after),
                     sleep=(lambda s: None) if a.dry_run else time.sleep):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
