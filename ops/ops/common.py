"""Shared helpers for ops scripts: instance list, command runner, Slack, downtime log."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol

import httpx

from fleet import generate

LOGGER_PACKAGE = os.environ.get("LOGGER_PACKAGE", "com.dazn.study.notiflogger")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_instances() -> tuple[dict, list[generate.Instance]]:
    fleet, cohorts = generate.load_config()
    return fleet, generate.build_instances(fleet, cohorts)


def wave_is_on(fleet: dict, inst: generate.Instance, at: datetime) -> bool:
    day = generate.DAYS[at.astimezone(timezone.utc).weekday()]
    return generate.wave_active(fleet["waves"].get(inst.wave), day, at.astimezone(timezone.utc).hour)


# ---------------------------------------------------------------- commands

@dataclass
class Result:
    rc: int
    out: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Runner(Protocol):
    def __call__(self, cmd: list[str], timeout: float = 20) -> Result: ...


def run(cmd: list[str], timeout: float = 20) -> Result:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return Result(p.returncode, (p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return Result(124, "timeout")
    except FileNotFoundError as e:
        return Result(127, str(e))


@dataclass
class DryRunner:
    """Records commands instead of running them. `responses` maps a substring to a Result."""
    responses: dict[str, Result] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, cmd: list[str], timeout: float = 20) -> Result:
        self.calls.append(cmd)
        line = " ".join(cmd)
        for needle, res in self.responses.items():
            if needle in line:
                return res
        return Result(0, "")


def adb(inst: generate.Instance, *args: str) -> list[str]:
    return [os.environ.get("ADB_BIN", "adb"), "-s", f"127.0.0.1:{inst.adb_port}", *args]


def route_fix_cmd(inst: generate.Instance) -> list[str]:
    """Re-point the default route at the market VPN gateway (lost on every container restart)."""
    return ["docker", "exec", inst.container, "ip", "route", "replace", "default", "via", inst.gateway_ip]


# ---------------------------------------------------------------- alerts

def alert(text: str, webhook: str | None = None, post: Callable[..., object] | None = None) -> None:
    webhook = webhook if webhook is not None else os.environ.get("SLACK_WEBHOOK_URL", "")
    print(text)
    if webhook:
        (post or httpx.post)(webhook, json={"text": text}, timeout=10)


# ---------------------------------------------------------------- downtime

class Downtime:
    """Writes instance_downtime rows. With no DATABASE_URL it only prints (dev/dry runs)."""

    def __init__(self, conn=None):
        self.conn = conn

    def open(self, instance_id: str, reason: str, at: datetime | None = None, detail: dict | None = None) -> int | None:
        at = at or now_utc()
        if self.conn is None:
            print(f"[downtime] open {instance_id} {reason} {at.isoformat()} {detail or ''}")
            return None
        return self.conn.execute(
            "insert into instance_downtime (instance_id, started_at, reason, detail) values (%s, %s, %s, %s::jsonb) "
            "returning id", (instance_id, at, reason, json.dumps(detail or {}))).fetchone()[0]

    def close(self, downtime_id: int | None, at: datetime | None = None) -> None:
        at = at or now_utc()
        if self.conn is None or downtime_id is None:
            print(f"[downtime] close {downtime_id} {at.isoformat()}")
            return
        self.conn.execute("update instance_downtime set ended_at = %s where id = %s", (at, downtime_id))
        self.conn.execute("select refresh_downtime_adjacent()")

    def open_id(self, instance_id: str, reason: str) -> int | None:
        if self.conn is None:
            return None
        row = self.conn.execute(
            "select id from instance_downtime where instance_id = %s and reason = %s and ended_at is null "
            "order by started_at desc limit 1", (instance_id, reason)).fetchone()
        return row[0] if row else None


def connect_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    import psycopg
    return psycopg.connect(url, autocommit=True)
