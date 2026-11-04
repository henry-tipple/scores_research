"""Snapshot / restore an instance's /data volume.

Used after onboarding (so a cohort can be reset to "just onboarded") and before risky
changes. The container is stopped for the duration; that window is logged as downtime
(reason snapshot / restore).

  python -m ops.snapshot take uk-c4-fotmob [--label onboarded] [--dry-run]
  python -m ops.snapshot restore uk-c4-fotmob /srv/study/snapshots/uk-c4-fotmob/2026...tar.zst
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .common import Downtime, DryRunner, Runner, connect_db, load_instances, now_utc, route_fix_cmd, run


def _instance(instance_id: str):
    _, instances = load_instances()
    for i in instances:
        if i.id == instance_id:
            return i
    raise SystemExit(f"unknown instance {instance_id}")


def take(instance_id: str, runner: Runner, conn, root: Path, label: str = "manual") -> Path:
    inst = _instance(instance_id)
    target = root / inst.id / f"{now_utc():%Y%m%dT%H%M%SZ}-{label}.tar.zst"
    dt = Downtime(conn)
    did = dt.open(inst.id, "snapshot", detail={"path": str(target)})
    runner(["docker", "stop", "--time", "20", inst.container], timeout=120)
    runner(["mkdir", "-p", str(target.parent)])
    r = runner(["tar", "--zstd", "-cf", str(target), "-C", inst.data_dir, "."], timeout=3600)
    runner(["docker", "start", inst.container], timeout=120)
    runner(route_fix_cmd(inst))
    dt.close(did)
    if not r.ok:
        raise SystemExit(f"snapshot failed: {r.out}")
    if conn is not None:
        conn.execute("update instances set snapshot_path = %s where id = %s", (str(target), inst.id))
    return target


def restore(instance_id: str, archive: Path, runner: Runner, conn) -> None:
    inst = _instance(instance_id)
    dt = Downtime(conn)
    did = dt.open(inst.id, "restore", detail={"path": str(archive)})
    runner(["docker", "stop", "--time", "20", inst.container], timeout=120)
    runner(["find", inst.data_dir, "-mindepth", "1", "-delete"], timeout=600)
    r = runner(["tar", "--zstd", "-xpf", str(archive), "-C", inst.data_dir], timeout=3600)
    runner(["docker", "start", inst.container], timeout=120)
    runner(route_fix_cmd(inst))
    dt.close(did)
    if not r.ok:
        raise SystemExit(f"restore failed: {r.out}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("take")
    t.add_argument("instance")
    t.add_argument("--label", default="manual")
    r = sub.add_parser("restore")
    r.add_argument("instance")
    r.add_argument("archive", type=Path)
    for s in (t, r):
        s.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    runner = DryRunner() if a.dry_run else run
    conn = None if a.dry_run else connect_db()
    if a.command == "take":
        root = Path(os.environ.get("SNAPSHOT_ROOT", "/srv/study/snapshots"))
        print(take(a.instance, runner, conn, root, a.label))
    else:
        restore(a.instance, a.archive, runner, conn)
    if a.dry_run:
        for c in runner.calls:
            print("would run:", " ".join(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
