import json
import shlex

import pytest

from fleet import generate as g


@pytest.fixture(scope="module")
def cfg():
    fleet, cohorts = g.load_config()
    return fleet, cohorts, g.build_instances(fleet, cohorts)


def test_counts(cfg):
    fleet, cohorts, inst = cfg
    assert len(cohorts) == 12
    assert sum(i.kind == "cohort" for i in inst) == 32
    assert sum(i.kind == "atlas" for i in inst) == 6
    assert len(inst) == 38


def test_multisport_cohorts_skip_fotmob(cfg):
    _, _, inst = cfg
    for c in ("C11", "C12"):
        apps = {i.app for i in inst if i.cohort_id == c}
        assert apps == {"365scores", "livescore"}


def test_email_and_ids(cfg):
    _, _, inst = cfg
    c4 = next(i for i in inst if i.id == "uk-c4-fotmob")
    assert c4.email == "uk-c4-fotmob@example.test"
    assert c4.package == "com.mobilefootie.wc2010"
    de = next(i for i in inst if i.cohort_id == "C7" and i.app == "livescore")
    assert (de.locale, de.tz, de.network) == ("de-DE", "Europe/Berlin", "vpn-de")


def test_validate_clean(cfg):
    assert g.validate(*cfg) == []


def test_same_device_across_apps_in_cohort(cfg):
    _, _, inst = cfg
    for c in {i.cohort_id for i in inst if i.cohort_id}:
        assert len({i.device_model for i in inst if i.cohort_id == c}) == 1


def test_redroid_commands_valid(cfg):
    fleet, _, inst = cfg
    lines = g.render_redroid(fleet, inst).strip().splitlines()
    assert len(lines) == 38
    names, ports = set(), set()
    for line in lines:
        argv = shlex.split(line)
        assert argv[:3] == ["docker", "run", "-d"]
        opts = dict(zip(argv[3::1], argv[4::1]))
        for flag in ("--name", "--network", "--ip", "--cpus", "--memory", "-p", "-v"):
            assert flag in argv, (flag, line)
        assert "--privileged" in argv
        names.add(opts["--name"])
        ports.add(opts["-p"])
        assert any(a.startswith("persist.sys.timezone=") for a in argv)
        assert fleet["redroid"]["image"] in argv
    assert len(names) == len(ports) == 38


def test_resource_overrides(cfg):
    fleet, cohorts, _ = cfg
    fleet = json.loads(json.dumps(fleet))
    fleet["resources"]["instance"] = {"uk-c4-fotmob": {"memory_gb": 3}}
    inst = g.build_instances(fleet, cohorts)
    c4 = next(i for i in inst if i.id == "uk-c4-fotmob")
    atlas = next(i for i in inst if i.kind == "atlas")
    assert c4.memory_gb == 3 and c4.cpus == 1.0
    assert atlas.memory_gb == 2.5
    assert "--memory 3072m" in shlex.join(g.redroid_args(fleet, c4))


def test_budget_waves(cfg):
    fleet, _, inst = cfg
    b = g.budget(fleet, inst)
    assert b["ram_all_on_gb"] > b["ram_peak_gb"]   # waves reduce the peak
    assert b["ram_ok"]
    fleet = json.loads(json.dumps(fleet))
    fleet["waves"] = {k: {} for k in fleet["waves"]}  # everything always on
    assert not g.budget(fleet, inst)["ram_ok"]


def test_wave_hours_wrap():
    assert g.wave_active({"hours": ["22-02"]}, "mon", 23)
    assert g.wave_active({"hours": ["22-02"]}, "mon", 1)
    assert not g.wave_active({"hours": ["22-02"]}, "mon", 2)
    assert not g.wave_active({"days": ["sat"]}, "mon", 1)


def test_env_override(monkeypatch):
    monkeypatch.setenv("STUDY_EMAIL_DOMAIN", "study.invalid")
    monkeypatch.setenv("FLEET_HOST_RAM_GB", "128")
    fleet, cohorts = g.load_config()
    assert fleet["host"]["ram_gb"] == 128
    assert g.build_instances(fleet, cohorts)[0].email.endswith("@study.invalid")


def test_sql_is_idempotent_upsert(cfg):
    sql = g.render_sql(*cfg)
    assert sql.count("insert into cohorts") == 12
    assert sql.count("insert into instances") == 38
    assert sql.count("on conflict (id) do update") == 50
