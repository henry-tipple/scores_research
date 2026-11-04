"""Start/stop instances so only the waves scheduled for this hour are running.

Run every 5 minutes (infra/systemd/study-waves.timer). Stopping an instance opens a
`wave_off` downtime window; starting it closes the window and restores the VPN route.

  python -m ops.waves [--dry-run]
"""
from __future__ import annotations

import argparse
from datetime import datetime

from .common import Downtime, DryRunner, Runner, connect_db, load_instances, now_utc, route_fix_cmd, run, wave_is_on


def is_running(container: str, runner: Runner) -> bool | None:
    r = runner(["docker", "inspect", "-f", "{{.State.Running}}", container])
    if not r.ok:
        return None                       # container does not exist (yet)
    return r.out.strip() == "true"


def reconcile(now: datetime, runner: Runner, conn, fleet=None, instances=None) -> list[str]:
    if instances is None:
        fleet, instances = load_instances()
    dt = Downtime(conn)
    log = []
    for inst in instances:
        want = wave_is_on(fleet, inst, now)
        running = is_running(inst.container, runner)
        if running is None:
            log.append(f"missing {inst.id}: container not created")
            continue
        if want and not running:
            runner(["docker", "start", inst.container], timeout=120)
            runner(route_fix_cmd(inst))
            dt.close(dt.open_id(inst.id, "wave_off"), now)
            log.append(f"start {inst.id} ({inst.wave})")
        elif running and not want:
            if dt.open_id(inst.id, "wave_off") is None:
                dt.open(inst.id, "wave_off", now, {"wave": inst.wave})
            runner(["docker", "stop", "--time", "20", inst.container], timeout=120)
            log.append(f"stop {inst.id} ({inst.wave})")
    return log


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    runner = DryRunner() if a.dry_run else run
    for line in reconcile(now_utc(), runner, None if a.dry_run else connect_db()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
