"""Run a Maestro flow against one instance, with the right env, and record the session.

  python -m ops.flow_runner uk-c3-fotmob                       # the cohort's daily flow
  python -m ops.flow_runner uk-c4-fotmob --live --live-team Arsenal --live-minutes 120
  python -m ops.flow_runner uk-c11-365scores --day-n 9 --live  # rotating sport of day 9
  python -m ops.flow_runner atlas-1-fotmob --match-entity Arsenal --capture-point t-1d
  ... --dry-run   prints the maestro command instead of running it

What it does:
  1. builds env: APP_ID, selectors (flows/selectors/<app>.yaml), cohort follows, rotation
  2. POST /v1/sessions to ingest (so days_since_last_open / opens_last_7d are right)
  3. holds LIVE_VISIT_LOCK_DIR/<instance>.lock during live visits (nightly restart skips it)
  4. runs `maestro --device 127.0.0.1:<adb port> test -e K=V ... flows/<flow>.yaml`
  5. POST /v1/sessions/<id>/end with the screenshots taken and the sport of each
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
from datetime import date, datetime
from pathlib import Path

import httpx
import yaml

from fleet import generate

from .common import DryRunner, Runner, now_utc, run

FLOWS_DIR = Path(__file__).resolve().parents[2] / "flows"

# How each app labels each sport in its sport picker. TODO(selector): confirm on device.
SPORT_LABELS = {
    "football": "Football", "basketball": "Basketball", "american_football": "American Football",
    "tennis": "Tennis", "cricket": "Cricket", "ice_hockey": "Ice Hockey",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _name(x) -> str:
    return x["name"] if isinstance(x, dict) else x


def rotation(cohort: dict) -> list[tuple[str, str]]:
    """(sport, entity) pairs in follows order: teams then players."""
    f = cohort.get("follows", {})
    return [(x.get("sport", "football"), x["name"]) for x in (f.get("teams") or []) + (f.get("players") or [])
            if isinstance(x, dict)]


def study_day(today: date) -> int:
    start = os.environ.get("STUDY_START_DATE")
    return (today - date.fromisoformat(start)).days + 1 if start else 1


def build_env(inst: generate.Instance, cohort: dict | None, flow: str, *, day_n: int, now: datetime,
              live: bool = False, live_team: str | None = None, live_minutes: int = 120,
              match_entity: str | None = None, capture_point: str = "adhoc", sport: str = "football") -> dict[str, str]:
    sel = yaml.safe_load((FLOWS_DIR / "selectors" / f"{inst.app}.yaml").read_text())
    shots = Path(os.environ.get("SCREENSHOT_ROOT", "/srv/study/screenshots")) / inst.id / f"{now:%Y-%m-%d}"
    flows_cfg = (cohort or {}).get("flows", {})
    env: dict[str, str] = {
        "APP_ID": inst.package, "INSTANCE_ID": inst.id,
        "SHOT_DIR": str(shots), "SHOT_PREFIX": f"{now:%H%M}_{flow}",
        "SESSION_MS": str(int(flows_cfg.get("session_seconds", 120)) * 1000),
        "DAY_N": str(day_n), "ACTIVE_UNTIL": str((flows_cfg.get("active_days") or [1, 10**6])[1]),
        "LIVE_VISIT": "true" if live else "false",
        "LIVE_MS": str(live_minutes * 60 * 1000), "LIVE_SHOT_MS": "300000", "MAX_SCROLLS": "25",
        **{k: str(v) for k, v in sel.items()},
    }
    follows = (cohort or {}).get("follows", {})
    teams = [_name(t) for t in follows.get("teams") or []]
    first_team = teams[0] if teams else (match_entity or "")
    env["TEAM_NAME"], env["TEAM_SLUG"] = first_team, slug(first_team)
    for kind, prefix, n in (("teams", "TEAM", 5), ("leagues", "LEAGUE", 2), ("players", "PLAYER", 3)):
        names = [_name(x) for x in follows.get(kind) or []]
        for i in range(n):
            env[f"{prefix}_{i + 1}"] = names[i] if i < len(names) else ""
    all_follows = [_name(x) for k in ("teams", "leagues", "players") for x in follows.get(k) or []]
    env["FOLLOW_LIST"] = ",".join(all_follows)
    notif = (cohort or {}).get("notif_settings", {})
    env["NOTIF_PRESET"] = "push_off" if notif.get("push") is False else notif.get("preset", "defaults")
    lt = live_team or (teams[0] if teams else "")
    env["LIVE_TEAM"], env["LIVE_TEAM_SLUG"] = lt, slug(lt)
    rot = rotation(cohort or {})
    if rot:
        sport_of_day, entity = rot[(day_n - 1) % len(rot)]
    else:
        sport_of_day, entity = sport, match_entity or (teams[0] if teams else "")
    env.update({"SPORT_OF_DAY": sport_of_day, "SPORT_LABEL": SPORT_LABELS.get(sport_of_day, sport_of_day),
                "ENTITY_OF_DAY": entity, "ENTITY_SLUG": slug(entity)})
    env.update({"MATCH_ENTITY": match_entity or entity, "MATCH_SLUG": slug(match_entity or entity),
                "CAPTURE_POINT": capture_point})
    return env


def maestro_cmd(inst: generate.Instance, flow: str, env: dict[str, str]) -> list[str]:
    cmd = [os.environ.get("MAESTRO_BIN", "maestro"), "--device", f"127.0.0.1:{inst.adb_port}", "test"]
    for k, v in sorted(env.items()):
        cmd += ["-e", f"{k}={v}"]
    return cmd + [str(FLOWS_DIR / f"{flow}.yaml")]


@contextlib.contextmanager
def live_lock(inst: generate.Instance, active: bool):
    if not active:
        yield
        return
    d = Path(os.environ.get("LIVE_VISIT_LOCK_DIR", "/run/study/live-visit"))
    d.mkdir(parents=True, exist_ok=True)
    lock = d / f"{inst.id}.lock"
    lock.write_text(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def run_flow(instance_id: str, flow: str | None, runner: Runner, ingest_url: str | None, **kw) -> int:
    fleet, cohorts = generate.load_config()
    instances = {i.id: i for i in generate.build_instances(fleet, cohorts)}
    inst = instances[instance_id]
    cohort = next((c for c in cohorts if c["id"] == inst.cohort_id), None)
    flow = flow or (inst.flows or {}).get("daily")
    if not flow:
        print(f"{inst.id}: cohort has no daily flow (e.g. C1 dormant); nothing to do")
        return 0
    now = now_utc()
    env = build_env(inst, cohort, flow, day_n=kw.pop("day_n") or study_day(now.date()), now=now, **kw)
    cmd = maestro_cmd(inst, flow, env)
    script = flow + (".live" if env["LIVE_VISIT"] == "true" else "")
    session_id = None
    http = httpx.Client(base_url=ingest_url, timeout=10) if ingest_url else None
    if http:
        session_id = http.post("/v1/sessions", json={"instance_id": inst.id, "script": script}).json()["id"]
    with live_lock(inst, env["LIVE_VISIT"] == "true"):
        r = runner(cmd, timeout=6 * 3600)
    shots = sorted(Path(env["SHOT_DIR"]).glob(f"{env['SHOT_PREFIX']}*.png")) if Path(env["SHOT_DIR"]).is_dir() else []
    if http and session_id:
        http.post(f"/v1/sessions/{session_id}/end", json={"screens": [
            {"screenshot": str(p), "sport": env["SPORT_OF_DAY"], "flow": flow} for p in shots]})
    return r.rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("instance")
    p.add_argument("--flow")
    p.add_argument("--day-n", type=int)
    p.add_argument("--live", action="store_true")
    p.add_argument("--live-team")
    p.add_argument("--live-minutes", type=int, default=120)
    p.add_argument("--match-entity")
    p.add_argument("--capture-point", default="adhoc")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    runner = DryRunner() if a.dry_run else run
    rc = run_flow(a.instance, a.flow, runner, None if a.dry_run else os.environ.get("INGEST_URL"),
                  day_n=a.day_n, live=a.live, live_team=a.live_team, live_minutes=a.live_minutes,
                  match_entity=a.match_entity, capture_point=a.capture_point)
    if a.dry_run:
        import shlex
        for c in runner.calls:
            print(shlex.join(c))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
