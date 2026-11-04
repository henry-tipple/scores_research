"""Fleet health check. Run every 15 minutes (infra/systemd/study-health.timer).

Per instance whose wave is on:
  adb       ADB answers `echo ok`
  logger    the logger process is running and is an enabled notification listener
  vpn       the market's VPN gateway reports a public IP (checked once per market)
  screenshot newest file in SCREENSHOT_ROOT/<instance>/ is younger than SCREENSHOT_MAX_AGE_HOURS
            (skipped for cohorts that are not meant to open the app, e.g. C1 Dormant)

Failures alert to SLACK_WEBHOOK_URL when the set of failures changes (new or recovered),
so a stuck instance does not page every 15 minutes. ADB/logger failures also open a
`health_fail` downtime window, closed again on recovery, so the gap is never read as
"no sends".

  python -m ops.health [--dry-run] [--state-file PATH]
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fleet.generate import Instance

from .common import (LOGGER_PACKAGE, Downtime, DryRunner, Result, Runner, adb, alert, connect_db, load_instances,
                     now_utc, run, wave_is_on)


@dataclass
class Check:
    instance: str
    name: str
    ok: bool
    detail: str = ""


def expects_screenshots(inst: Instance, today: date, study_start: date | None) -> bool:
    flows = inst.flows or {}
    if not flows.get("daily"):
        return False                                   # C1 dormant never opens the app
    if study_start and flows.get("active_days"):
        first, last = flows["active_days"]
        day_n = (today - study_start).days + 1
        if not first <= day_n <= last + 1:            # C2 lapsed stops after day 7
            return False
    return True


def newest_mtime(folder: Path) -> datetime | None:
    if not folder.is_dir():
        return None
    times = [p.stat().st_mtime for p in folder.rglob("*.png")]
    return datetime.fromtimestamp(max(times)).astimezone() if times else None


def check_instance(inst: Instance, runner: Runner, shots_root: Path, max_age: timedelta,
                   now: datetime, study_start: date | None) -> list[Check]:
    out = []
    runner([os.environ.get("ADB_BIN", "adb"), "connect", f"127.0.0.1:{inst.adb_port}"], timeout=10)
    r = runner(adb(inst, "shell", "echo", "ok"), timeout=10)
    adb_ok = r.ok and "ok" in r.out
    out.append(Check(inst.id, "adb", adb_ok, "" if adb_ok else r.out.strip()[:200]))
    if adb_ok:
        pid = runner(adb(inst, "shell", "pidof", LOGGER_PACKAGE))
        listeners = runner(adb(inst, "shell", "settings", "get", "secure", "enabled_notification_listeners"))
        ok = pid.ok and pid.out.strip() != "" and LOGGER_PACKAGE in listeners.out
        out.append(Check(inst.id, "logger", ok, "" if ok else
                         f"pid={pid.out.strip() or 'none'} listener_enabled={LOGGER_PACKAGE in listeners.out}"))
    else:
        out.append(Check(inst.id, "logger", False, "adb down"))
    if expects_screenshots(inst, now.date(), study_start):
        newest = newest_mtime(shots_root / inst.id)
        ok = newest is not None and now - newest < max_age
        out.append(Check(inst.id, "screenshot", ok,
                         "" if ok else f"newest={newest.isoformat() if newest else 'none'}"))
    return out


def check_vpn(market: str, runner: Runner) -> Check:
    cmd = os.environ.get("VPN_CHECK_CMD", "docker exec vpn-gw-{market} wget -qO- http://127.0.0.1:8000/v1/publicip/ip")
    r = runner(cmd.format(market=market.lower()).split(), timeout=20)
    ok = r.ok and r.out.strip() != ""
    return Check(f"vpn-{market.lower()}", "vpn", ok, r.out.strip()[:200])


def run_checks(runner: Runner, now: datetime | None = None, instances=None, fleet=None) -> list[Check]:
    now = now or now_utc()
    if instances is None:
        fleet, instances = load_instances()
    shots = Path(os.environ.get("SCREENSHOT_ROOT", "/srv/study/screenshots"))
    max_age = timedelta(hours=float(os.environ.get("SCREENSHOT_MAX_AGE_HOURS", "8")))
    start = os.environ.get("STUDY_START_DATE")
    study_start = date.fromisoformat(start) if start else None
    active = [i for i in instances if wave_is_on(fleet, i, now)]
    checks: list[Check] = []
    vpn = {m: check_vpn(m, runner) for m in sorted({i.market for i in active})}
    checks.extend(vpn.values())
    for inst in active:
        checks.extend(check_instance(inst, runner, shots, max_age, now, study_start))
    return checks


def record_downtime(checks: list[Check], dt: Downtime, now: datetime) -> None:
    by_inst: dict[str, bool] = {}
    for c in checks:
        if c.name in ("adb", "logger"):
            by_inst[c.instance] = by_inst.get(c.instance, True) and c.ok
    for inst, ok in by_inst.items():
        open_id = dt.open_id(inst, "health_fail")
        if not ok and open_id is None:
            dt.open(inst, "health_fail", now, {"checks": [c.name for c in checks if c.instance == inst and not c.ok]})
        elif ok and open_id is not None:
            dt.close(open_id, now)


def diff_and_alert(checks: list[Check], state_file: Path, post=None) -> str | None:
    failing = {f"{c.instance}:{c.name}": c.detail for c in checks if not c.ok}
    try:
        previous = json.loads(state_file.read_text())
    except (FileNotFoundError, ValueError):
        previous = {}
    new = sorted(set(failing) - set(previous))
    recovered = sorted(set(previous) - set(failing))
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(failing, indent=1))
    except OSError as e:
        print(f"warning: could not write {state_file}: {e}")
    if not new and not recovered:
        return None
    lines = [f":rotating_light: study health: {len(failing)} failing"]
    lines += [f"• NEW {k} {failing[k]}" for k in new]
    lines += [f"• recovered {k}" for k in recovered]
    text = "\n".join(lines)
    alert(text, post=post)
    return text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="don't run adb/docker; pretend everything answers")
    p.add_argument("--state-file", type=Path,
                   default=Path(os.environ.get("HEALTH_STATE_FILE", "/var/lib/study/health-state.json")))
    a = p.parse_args(argv)
    runner = DryRunner(responses={"echo ok": Result(0, "ok")}) if a.dry_run else run
    now = now_utc()
    checks = run_checks(runner, now)
    conn = None if a.dry_run else connect_db()
    record_downtime(checks, Downtime(conn), now)
    failing = [c for c in checks if not c.ok]
    for c in failing:
        print(f"FAIL {c.instance:24} {c.name:10} {c.detail}")
    print(f"{len(checks) - len(failing)}/{len(checks)} checks ok")
    if not a.dry_run:
        diff_and_alert(checks, a.state_file)
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
