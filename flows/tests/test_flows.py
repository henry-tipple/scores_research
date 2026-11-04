"""Static checks on the Maestro templates (no device needed)."""
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from fleet import generate
from ops import flow_runner

FLOWS = Path(__file__).resolve().parents[1]
TOP = ["daily_open", "power_fan", "lapsed", "c11_multisport", "c12_no_football", "atlas_match_capture", "onboard"]
EXPR = re.compile(r"\$\{(.*?)\}", re.S)
TOKEN = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")


def load_flow(path: Path):
    docs = list(yaml.safe_load_all(path.read_text()))
    assert len(docs) == 2, f"{path.name}: expected config + commands documents"
    config, commands = docs
    assert config["appId"] == "${APP_ID}"
    assert isinstance(commands, list) and commands
    return config, commands


def all_flow_files():
    return sorted(FLOWS.glob("*.yaml")) + sorted((FLOWS / "common").glob("*.yaml"))


@pytest.mark.parametrize("name", TOP)
def test_top_level_flow_parses(name):
    load_flow(FLOWS / f"{name}.yaml")


def test_common_flows_parse_and_runflow_targets_exist():
    for path in all_flow_files():
        _, commands = load_flow(path)
        for ref in re.findall(r"file:\s*(\S+\.yaml)|runFlow:\s*(\S+\.yaml)", path.read_text()):
            target = next(r for r in ref if r)
            assert (path.parent / target).exists(), f"{path.name} -> {target}"


def test_selectors_marked_todo():
    for app in ("365scores", "fotmob", "livescore"):
        sel = yaml.safe_load((FLOWS / "selectors" / f"{app}.yaml").read_text())
        assert sel and all(str(v).startswith("TODO(selector)") for v in sel.values()), app
    text = "".join(p.read_text() for p in all_flow_files())
    assert text.count("TODO(selector)") >= 20


def _env_names_set_inside_flows() -> set[str]:
    names = set()
    for path in all_flow_files():
        for m in re.finditer(r"env:\n((?:\s+[A-Z_]+:.*\n)+)", path.read_text()):
            names |= set(re.findall(r"([A-Z_][A-Z0-9_]+):", m.group(1)))
    return names


def _tokens_used() -> set[str]:
    used = set()
    for path in all_flow_files():
        for expr in EXPR.findall(path.read_text()):
            used |= {t for t in TOKEN.findall(expr)}
    return used


def test_runner_provides_every_variable():
    fleet, cohorts = generate.load_config()
    instances = generate.build_instances(fleet, cohorts)
    needed = _tokens_used() - _env_names_set_inside_flows()
    now = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)
    for inst in instances:
        cohort = next((c for c in cohorts if c["id"] == inst.cohort_id), None)
        env = flow_runner.build_env(inst, cohort, inst.flows.get("daily") or "onboard", day_n=3, now=now)
        missing = needed - set(env)
        assert not missing, (inst.id, sorted(missing))


def test_c11_rotation_and_c12_never_football():
    fleet, cohorts = generate.load_config()
    by_id = {c["id"]: c for c in cohorts}
    sports11 = [s for s, _ in flow_runner.rotation(by_id["C11"])]
    assert set(sports11) == {"football", "basketball", "american_football", "cricket", "ice_hockey", "tennis"}
    assert "football" not in {s for s, _ in flow_runner.rotation(by_id["C12"])}


def test_maestro_command_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_VISIT_LOCK_DIR", str(tmp_path))
    from ops.common import DryRunner

    seen_lock = []

    class LockSpy(DryRunner):
        def __call__(self, cmd, timeout=20):
            seen_lock.append((tmp_path / "uk-c11-365scores.lock").exists())
            return super().__call__(cmd, timeout)

    runner = LockSpy()
    rc = flow_runner.run_flow("uk-c11-365scores", None, runner, None, day_n=2, live=True, live_team=None,
                              live_minutes=60, match_entity=None, capture_point="adhoc")
    assert rc == 0 and seen_lock == [True]
    assert not (tmp_path / "uk-c11-365scores.lock").exists()
    cmd = shlex.join(runner.calls[0])
    assert "--device 127.0.0.1:" in cmd and cmd.endswith("flows/c11_multisport.yaml")
    assert "-e SPORT_OF_DAY=basketball" in cmd and "-e APP_ID=com.scores365" in cmd
    # C1 dormant has no daily flow
    assert flow_runner.run_flow("uk-c1-fotmob", None, DryRunner(), None, day_n=1) == 0
