"""`orchestra doctor` names Arturo's onboarding state (orchestra-builder D-7): the flag present,
inferred on an upgrade (with its evidence), or a fresh install not yet onboarded. Read-only."""
import json
import os
from pathlib import Path

import pytest

from orchestra_cli import doctor as D
from orchestra_cli import settings as S


@pytest.fixture(autouse=True)
def fence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert os.environ["HOME"].startswith(str(tmp_path)) and os.environ["ORCHESTRA_DIR"].startswith(str(tmp_path))
    assert "TMUX" not in os.environ and "TMUX_PANE" not in os.environ
    yield


@pytest.fixture()
def data(tmp_path):
    d = tmp_path / "data"
    (d / "state" / "arturo").mkdir(parents=True)
    return d


def _state(data):
    return data / "state" / "arturo"


def _snapshot(data):
    return sorted((str(p), p.read_bytes()) for p in data.rglob("*") if p.is_file())


def test_flag_present(data):
    (_state(data) / "onboarding.json").write_text('{"finished_at": 1.0}\n')
    c = D.onboarding_check(data)
    assert c.name == "arturo:onboarding" and c.status == D.OK and c.detail.startswith("present:")
    assert c.required is False


def test_inferred_on_upgrade_names_the_reason_and_evidence(data):
    (_state(data) / "onboarding.json").write_text(json.dumps(
        {"reason": "upgrade-inferred", "evidence": ["registry: gm is a T0 seat"], "inferred_at": 1.0}))
    c = D.onboarding_check(data)
    assert c.status == D.OK and c.detail.startswith("inferred-on-upgrade:")
    assert "upgrade-inferred" in c.detail and "gm is a T0 seat" in c.detail


@pytest.mark.parametrize("progress", [None, {"fresh_at": 5.0}])
def test_fresh_not_onboarded(data, progress):
    if progress is not None:
        (_state(data) / "onboarding-progress.json").write_text(json.dumps(progress))
    c = D.onboarding_check(data)
    assert c.status == D.INFO and c.detail.startswith("fresh-not-onboarded:")


def test_evidence_without_a_flag_yet_says_it_will_be_inferred_and_doctor_writes_nothing(data):
    (data / "registry.json").write_text(json.dumps({"agents": {"gm": {"tier": "T0"}}}))
    before = _snapshot(data)
    c = D.onboarding_check(data)
    assert c.status == D.INFO and c.detail.startswith("will-infer:") and "gm is a T0 seat" in c.detail
    assert _snapshot(data) == before


def test_unreadable_evidence_warns_undecided(data):
    (data / "registry.json").write_text("{")
    c = D.onboarding_check(data)
    assert c.status == D.WARN and c.detail.startswith("undecided:") and "registry.json" in c.detail


def test_run_doctor_includes_the_row(tmp_path, data):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    st = S.load_settings(repo_root=root)
    probes = D.DoctorProbes(which=lambda _c: None, run_cmd=lambda _a: "", port_owner=lambda _p: None,
                            supervisor_state=lambda _d: None, import_ok=lambda _m: True,
                            read_file=lambda p: Path(p).read_text(), now_ms=lambda: 0,
                            python_version=(3, 12, 0), git_hooks_path=lambda _r: "")
    rows = {c.name: c for c in D.run_doctor(st, probes)}
    assert rows["arturo:onboarding"].detail.startswith("fresh-not-onboarded:")


# ---- `orchestra init` decides first: before `orchestra starter` or --demo can register a seat ----

def _init(tmp_path, data, demo=False):
    from orchestra_cli import init_cmd as I
    from orchestra_cli.tests.test_init import Runner, _repo
    root = tmp_path / "repo" if (tmp_path / "repo").exists() else _repo(tmp_path)
    report = I.run_init(root, data_dir=data, run=Runner(), skip_npm=True, skip_venv=True, skip_build=True, demo=demo)
    return {r.step: r for r in report}


@pytest.mark.parametrize("demo", [False, True])
def test_init_on_a_fresh_data_dir_marks_a_first_run_and_a_later_team_does_not_flip_it(tmp_path, demo):
    data = tmp_path / "data"
    steps = _init(tmp_path, data, demo=demo)
    assert steps["arturo-onboarding"].detail == "a first run: Arturo's onboarding runs"
    assert not (_state(data) / "onboarding.json").exists()
    assert "fresh_at" in json.loads((_state(data) / "onboarding-progress.json").read_text())
    (data / "registry.json").write_text(json.dumps({"agents": {"gm": {"tier": "T0"}}}))   # orchestra starter
    assert D.onboarding_check(data).detail.startswith("fresh-not-onboarded:")
    assert _init(tmp_path, data)["arturo-onboarding"].did is False


def test_init_on_an_earlier_install_writes_the_upgrade_inferred_flag(tmp_path, data):
    (data / "registry.json").write_text(json.dumps({"agents": {"gm": {"tier": "T0"}}}))
    step = _init(tmp_path, data)["arturo-onboarding"]
    assert step.did is True and "upgrade-inferred" in step.detail
    assert json.loads((_state(data) / "onboarding.json").read_text())["reason"] == "upgrade-inferred"
    assert D.onboarding_check(data).detail.startswith("inferred-on-upgrade:")


def test_init_with_an_unreadable_registry_decides_nothing_and_does_not_fail(tmp_path, data):
    from orchestra_cli import init_cmd as I
    (data / "registry.json").write_text("{")
    step = _init(tmp_path, data)["arturo-onboarding"]
    assert step.did is False and step.detail.startswith("undecided:") and step not in I.failed_steps([step])
    assert not (_state(data) / "onboarding.json").exists() and not (_state(data) / "onboarding-progress.json").exists()
