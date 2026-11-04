"""Fleet generator: cohorts.yaml + fleet.yaml -> instance definitions.

Outputs (pick with the subcommand):
  summary   human-readable table plus the RAM/CPU budget per wave
  sql       idempotent seed SQL for `cohorts` and `instances`
  docker    `docker network create`, VPN gateway and ReDroid `docker run` commands
  redroid   only the ReDroid `docker run` commands, one per line
  manifest  JSON list of instance definitions (consumed by ops/ and logger-apk builds)
  check     validate and exit non-zero on errors (budget overrun is an error with --strict)
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shlex
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

FLEET_DIR = Path(__file__).resolve().parent.parent
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


# ---------------------------------------------------------------- config loading

def _expand(value: Any) -> Any:
    """Expand ${VAR:-default} in every string, then coerce pure numbers."""
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, str) and "${" in value:
        out = _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
        if re.fullmatch(r"-?\d+", out):
            return int(out)
        if re.fullmatch(r"-?\d+\.\d+", out):
            return float(out)
        return out
    return value


def load_config(fleet_path: Path | None = None, cohorts_path: Path | None = None) -> tuple[dict, list[dict]]:
    fleet = _expand(yaml.safe_load((fleet_path or FLEET_DIR / "fleet.yaml").read_text()))
    cohorts = yaml.safe_load((cohorts_path or FLEET_DIR / "cohorts.yaml").read_text())["cohorts"]
    return fleet, cohorts


# ---------------------------------------------------------------- model

@dataclass
class Instance:
    id: str
    kind: str                     # cohort | atlas
    cohort_id: str | None
    app: str
    package: str
    market: str
    locale: str
    tz: str
    email: str
    device: dict
    wave: str
    cpus: float
    memory_gb: float
    network: str                  # docker network == vpn_exit
    ip: str
    gateway_ip: str
    adb_port: int
    ingest_url: str
    data_dir: str
    flows: dict = field(default_factory=dict)

    @property
    def device_model(self) -> str:
        return self.device["model"]

    @property
    def container(self) -> str:
        return self.id


def _resources(fleet: dict, kind: str, instance_id: str) -> tuple[float, float]:
    res = dict(fleet["resources"]["default"])
    res.update(fleet["resources"].get("kind", {}).get(kind, {}) or {})
    res.update((fleet["resources"].get("instance") or {}).get(instance_id, {}) or {})
    return float(res["cpus"]), float(res["memory_gb"])


def build_instances(fleet: dict, cohorts: list[dict]) -> list[Instance]:
    apps = fleet["apps"]
    domain = fleet["email"]["domain"]
    profiles = fleet["device_profiles"]
    vpn = fleet["vpn"]
    rd = fleet["redroid"]
    next_host: dict[str, int] = {}
    out: list[Instance] = []

    def make(iid: str, kind: str, cohort: dict | None, app: str, market: str, profile: dict) -> Instance:
        m = fleet["markets"][market]
        net = ipaddress.ip_network(m["subnet"])
        host_n = next_host.get(market, vpn["first_instance_host"])
        next_host[market] = host_n + 1
        cpus, mem = _resources(fleet, kind, iid)
        wave_tpl = fleet["assign_waves"][kind]
        ingest_ip = str(net.network_address + vpn["ingest_host"])
        return Instance(
            id=iid, kind=kind, cohort_id=cohort["id"] if cohort else None,
            app=app, package=apps[app]["package"], market=market,
            locale=m["locale"], tz=m["tz"], email=f"{iid}@{domain}".lower(),  # <market>-<cohort>-<app>@<domain>
            device=profile, wave=wave_tpl.format(app=app, market=market.lower()),
            cpus=cpus, memory_gb=mem, network=f"vpn-{market.lower()}",
            ip=str(net.network_address + host_n),
            gateway_ip=str(net.network_address + vpn["gateway_host"]),
            adb_port=0, ingest_url=f"http://{ingest_ip}:{fleet['ingest']['port']}",
            data_dir=f"{rd['data_root']}/{iid}",
            flows=(cohort or {}).get("flows", {"daily": "atlas_match_capture"}),
        )

    slot = 0
    for c in cohorts:
        profile = profiles[slot % len(profiles)]
        slot += 1
        for app in c.get("apps") or list(apps):
            iid = f"{c['market']}-{c['id']}-{app}".lower()
            out.append(make(iid, "cohort", c, app, c["market"], profile))
    for n in range(1, fleet["atlas"]["per_app"] + 1):
        profile = profiles[slot % len(profiles)]
        slot += 1
        for app in apps:
            out.append(make(f"atlas-{n}-{app}", "atlas", None, app, fleet["atlas"]["market"], profile))
    for i, inst in enumerate(out):
        inst.adb_port = rd["adb_port_base"] + i
    return out


# ---------------------------------------------------------------- waves & budget

def _hour_ranges(spec: list[str]) -> set[int]:
    hours: set[int] = set()
    for r in spec:
        a, b = (int(x) for x in r.split("-"))
        h = a
        while True:
            hours.add(h % 24)
            h = (h + 1) % 24
            if h == b % 24:
                break
    return hours


def wave_active(wave: dict | None, day: str, hour: int) -> bool:
    wave = wave or {}
    if wave.get("days") and day not in wave["days"]:
        return False
    if wave.get("hours") and hour not in _hour_ranges(wave["hours"]):
        return False
    return True


def budget(fleet: dict, instances: list[Instance]) -> dict:
    """Peak concurrent RAM/CPU over a week of hourly slots, honouring waves."""
    waves = fleet["waves"]
    limit_ram = float(fleet["host"]["ram_gb"]) - float(fleet["host"]["reserved_ram_gb"])
    limit_cpu = float(fleet["host"]["cpus"]) * float(fleet["host"]["max_cpu_overcommit"])
    peak = {"ram_gb": 0.0, "cpus": 0.0, "slot": None, "running": 0}
    for day in DAYS:
        for hour in range(24):
            running = [i for i in instances if wave_active(waves.get(i.wave), day, hour)]
            ram = sum(i.memory_gb for i in running)
            if ram > peak["ram_gb"]:
                peak = {"ram_gb": ram, "cpus": sum(i.cpus for i in running),
                        "slot": f"{day} {hour:02d}:00 UTC", "running": len(running)}
    all_on = sum(i.memory_gb for i in instances)
    return {
        "instances": len(instances),
        "ram_all_on_gb": round(all_on, 2),
        "ram_peak_gb": round(peak["ram_gb"], 2),
        "cpus_at_peak": round(peak["cpus"], 2),
        "peak_slot": peak["slot"],
        "running_at_peak": peak["running"],
        "ram_available_gb": limit_ram,
        "cpu_limit": limit_cpu,
        "ram_ok": peak["ram_gb"] <= limit_ram,
        "cpu_ok": peak["cpus"] <= limit_cpu,
    }


# ---------------------------------------------------------------- validation

def validate(fleet: dict, cohorts: list[dict], instances: list[Instance]) -> list[str]:
    errors: list[str] = []
    for attr in ("id", "email", "adb_port", "ip"):
        vals = [getattr(i, attr) for i in instances]
        dupes = {v for v in vals if vals.count(v) > 1}
        if dupes:
            errors.append(f"duplicate {attr}: {sorted(map(str, dupes))}")
    ids = [c["id"] for c in cohorts]
    if len(set(ids)) != len(ids):
        errors.append("duplicate cohort ids")
    for c in cohorts:
        if c["market"] not in fleet["markets"]:
            errors.append(f"{c['id']}: unknown market {c['market']}")
        for app in c.get("apps") or []:
            if app not in fleet["apps"]:
                errors.append(f"{c['id']}: unknown app {app}")
    for i in instances:
        if i.wave not in fleet["waves"]:
            errors.append(f"{i.id}: wave {i.wave} not defined")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]+", i.id):
            errors.append(f"{i.id}: not a valid container name")
        net = ipaddress.ip_network(fleet["markets"][i.market]["subnet"])
        if ipaddress.ip_address(i.ip) not in net:
            errors.append(f"{i.id}: ip {i.ip} outside {net}")
    for name, w in fleet["waves"].items():
        for d in (w or {}).get("days", []):
            if d not in DAYS:
                errors.append(f"wave {name}: bad day {d}")
    return errors


# ---------------------------------------------------------------- renderers

def _sql_lit(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return _sql_lit(json.dumps(v, ensure_ascii=False)) + "::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def render_sql(fleet: dict, cohorts: list[dict], instances: list[Instance]) -> str:
    lines = ["-- Generated by fleet/fleet/generate.py. Do not edit by hand.", "begin;"]
    for c in cohorts:
        apps_arr = "array[" + ",".join(_sql_lit(a) for a in (c.get("apps") or list(fleet["apps"]))) + "]"
        lines.append(
            "insert into cohorts (id, name, market, follows, notif_settings, behaviour, hypothesis, apps) values ("
            + ", ".join([_sql_lit(c["id"]), _sql_lit(c["name"]), _sql_lit(c["market"]),
                         _sql_lit(c.get("follows", {})), _sql_lit(c.get("notif_settings", {})),
                         _sql_lit(c.get("behaviour")), _sql_lit(c.get("hypothesis")), apps_arr])
            + ") on conflict (id) do update set name = excluded.name, market = excluded.market,"
            " follows = excluded.follows, notif_settings = excluded.notif_settings,"
            " behaviour = excluded.behaviour, hypothesis = excluded.hypothesis, apps = excluded.apps;")
    for i in instances:
        lines.append(
            "insert into instances (id, kind, cohort_id, app, package, market, email, device_model, locale, tz, vpn_exit, wave) values ("
            + ", ".join(_sql_lit(v) for v in [i.id, i.kind, i.cohort_id, i.app, i.package, i.market,
                                              i.email, i.device_model, i.locale, i.tz, i.network, i.wave])
            + ") on conflict (id) do update set kind = excluded.kind, cohort_id = excluded.cohort_id,"
            " app = excluded.app, package = excluded.package, market = excluded.market, email = excluded.email,"
            " device_model = excluded.device_model, locale = excluded.locale, tz = excluded.tz,"
            " vpn_exit = excluded.vpn_exit, wave = excluded.wave;")
    lines.append("commit;")
    return "\n".join(lines) + "\n"


def redroid_args(fleet: dict, i: Instance) -> list[str]:
    rd = fleet["redroid"]
    d = i.device
    mem = f"{int(round(i.memory_gb * 1024))}m"
    args = [
        "docker", "run", "-d",
        "--name", i.container, "--hostname", i.container,
        "--privileged",
        "--restart", "unless-stopped",
        "--network", i.network, "--ip", i.ip,
        "--cpus", f"{i.cpus:g}", "--memory", mem, "--memory-swap", mem,
        "-p", f"{rd['adb_bind']}:{i.adb_port}:5555",
        "-v", f"{i.data_dir}:/data",
        "--label", f"study.instance={i.id}", "--label", f"study.kind={i.kind}",
        "--label", f"study.app={i.app}", "--label", f"study.market={i.market}",
        "--label", f"study.wave={i.wave}",
        rd["image"],
        f"androidboot.redroid_width={d['width']}",
        f"androidboot.redroid_height={d['height']}",
        f"androidboot.redroid_dpi={d['dpi']}",
        f"androidboot.redroid_gpu_mode={rd['gpu_mode']}",
        f"ro.product.model={d['model']}",
        f"ro.product.brand={d['brand']}",
        f"ro.product.manufacturer={d['manufacturer']}",
        f"ro.product.device={d['device']}",
        f"persist.sys.locale={i.locale}",
        f"persist.sys.timezone={i.tz}",
        f"net.dns1={i.gateway_ip}",
        *rd.get("extra_args", []),
    ]
    return args


def render_redroid(fleet: dict, instances: list[Instance]) -> str:
    return "\n".join(shlex.join(redroid_args(fleet, i)) for i in instances) + "\n"


def render_docker(fleet: dict, instances: list[Instance]) -> str:
    vpn = fleet["vpn"]
    out = ["#!/usr/bin/env bash", "# Generated by fleet/fleet/generate.py. Review before running on the server.",
           "set -euo pipefail", "", "# 1. One docker network per market"]
    used = sorted({i.market for i in instances})
    for mk in used:
        net = ipaddress.ip_network(fleet["markets"][mk]["subnet"])
        out.append(shlex.join(["docker", "network", "create", "--driver", "bridge", "--subnet", str(net),
                               f"vpn-{mk.lower()}"]) + " || true")
    out += ["", "# 2. One VPN gateway per market (credentials come from an env file on the server)"]
    for mk in used:
        net = ipaddress.ip_network(fleet["markets"][mk]["subnet"])
        gw = str(net.network_address + vpn["gateway_host"])
        out.append(shlex.join([
            "docker", "run", "-d", "--name", f"vpn-gw-{mk.lower()}", "--restart", "unless-stopped",
            "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
            "--network", f"vpn-{mk.lower()}", "--ip", gw,
            "--env-file", f"{vpn['env_file_dir']}/vpn-{mk.lower()}.env",
            "-e", f"SERVER_COUNTRIES={fleet['markets'][mk]['country']}",
            "-e", f"FIREWALL_OUTBOUND_SUBNETS={net}",
            "--label", f"study.vpn={mk}",
            vpn["gateway_image"]]))
    out += ["", "# 3. Attach ingest to every market network so loggers can reach it without the VPN"]
    for mk in used:
        net = ipaddress.ip_network(fleet["markets"][mk]["subnet"])
        ip = str(net.network_address + vpn["ingest_host"])
        out.append(f'docker network connect --ip {ip} vpn-{mk.lower()} "${{INGEST_CONTAINER:-study-ingest}}" || true')
    out += ["", f"# 4. ReDroid instances ({len(instances)})"]
    out += [shlex.join(redroid_args(fleet, i)) for i in instances]
    out += ["", "# 5. Route each instance through its market gateway (see fleet/vpn/README.md)",
            "# Validate on the real server: ReDroid's netd policy routing may need the rule in table eth0."]
    for i in instances:
        out.append(f"docker exec {i.container} ip route replace default via {i.gateway_ip}  # {i.id}")
    return "\n".join(out) + "\n"


def render_manifest(fleet: dict, instances: list[Instance]) -> str:
    rows = []
    for i in instances:
        d = asdict(i)
        d["container"] = i.container
        d["device_model"] = i.device_model
        rows.append(d)
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def render_summary(fleet: dict, instances: list[Instance]) -> str:
    b = budget(fleet, instances)
    n_cohort = sum(i.kind == "cohort" for i in instances)
    n_atlas = sum(i.kind == "atlas" for i in instances)
    lines = [f"{'instance':24} {'wave':16} {'mkt':3} {'cpus':>4} {'ram':>5}  {'adb':>5}  email"]
    for i in instances:
        lines.append(f"{i.id:24} {i.wave:16} {i.market:3} {i.cpus:>4g} {i.memory_gb:>4g}G  {i.adb_port:>5}  {i.email}")
    lines += [
        "",
        f"{n_cohort} cohort instances + {n_atlas} atlas instances = {len(instances)}",
        f"RAM if everything ran at once: {b['ram_all_on_gb']} GB",
        f"RAM at peak with waves:       {b['ram_peak_gb']} GB ({b['running_at_peak']} running, {b['peak_slot']})",
        f"RAM available for instances:  {b['ram_available_gb']} GB  -> {'OK' if b['ram_ok'] else 'OVER BUDGET'}",
        f"CPUs at peak: {b['cpus_at_peak']} of {b['cpu_limit']} allowed -> {'OK' if b['cpu_ok'] else 'OVERCOMMITTED'}",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["summary", "sql", "docker", "redroid", "manifest", "check"])
    p.add_argument("--fleet", type=Path)
    p.add_argument("--cohorts", type=Path)
    p.add_argument("--strict", action="store_true", help="check: treat budget overrun as an error")
    p.add_argument("--out", type=Path, help="also write fleet.sh, redroid.txt, manifest.json, instances.sql here")
    a = p.parse_args(argv)

    fleet, cohorts = load_config(a.fleet, a.cohorts)
    instances = build_instances(fleet, cohorts)
    errors = validate(fleet, cohorts, instances)
    if errors:
        for e in errors:
            print("error: " + e, file=sys.stderr)
        return 1
    b = budget(fleet, instances)
    if not b["ram_ok"]:
        print(f"warning: peak RAM {b['ram_peak_gb']} GB exceeds {b['ram_available_gb']} GB available "
              "- lower resources or add waves in fleet.yaml", file=sys.stderr)

    render = {
        "summary": lambda: render_summary(fleet, instances),
        "sql": lambda: render_sql(fleet, cohorts, instances),
        "docker": lambda: render_docker(fleet, instances),
        "redroid": lambda: render_redroid(fleet, instances),
        "manifest": lambda: render_manifest(fleet, instances),
        "check": lambda: render_summary(fleet, instances),
    }[a.command]
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / "fleet.sh").write_text(render_docker(fleet, instances))
        (a.out / "redroid.txt").write_text(render_redroid(fleet, instances))
        (a.out / "manifest.json").write_text(render_manifest(fleet, instances))
        (a.out / "instances.sql").write_text(render_sql(fleet, cohorts, instances))
        print(f"wrote fleet.sh, redroid.txt, manifest.json, instances.sql to {a.out}", file=sys.stderr)
    sys.stdout.write(render())
    if a.command == "check" and a.strict and not (b["ram_ok"] and b["cpu_ok"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
