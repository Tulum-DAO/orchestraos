"""The test-run data-dir fence (gm msg_f0c527f4). Measured on an installed checkout: the suite wrote test
seats, state/agent-state/*.json, tasks.db rows, cursors and locks into the operator's REAL data dir.
Two layers: the repo-root conftest points every run at a temp data dir before any module freezes a path,
and the stores refuse to write under a real data dir from a test (orchestra_cli.settings.guard_test_write)."""
import importlib.util
import os
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestra_cli import settings as S  # noqa: E402

_FENCED = os.environ.get("ORCHESTRA_DIR")


# ---- layer 1: the run is fenced, and a test cannot leak its env into the next one -----------------

def test_the_run_points_at_a_temp_data_dir_and_records_the_real_ones():
    assert _FENCED and "orchestra-test-data-" in _FENCED, _FENCED
    assert os.environ["ORCH_DIR"] == _FENCED
    assert not os.path.exists(os.environ["ORCHESTRA_CONFIG"]), "the run must not read an installed toml"
    protected = S.protected_data_dirs()
    assert protected and all(os.path.isabs(p) for p in protected)
    assert os.path.realpath(_FENCED) not in protected


def test_a_raw_environ_write_in_one_test_1_of_2():
    os.environ["ORCHESTRA_DIR"] = "/tmp/a-test-that-forgot-monkeypatch"
    os.environ["ORCHESTRA_CONFIG"] = "/tmp/some.toml"


def test_a_raw_environ_write_in_one_test_2_of_2_never_reaches_the_next():
    assert os.environ["ORCHESTRA_DIR"] == _FENCED
    assert os.environ["ORCHESTRA_CONFIG"].endswith("no-orchestra.toml")


def test_a_test_may_still_set_its_own(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    assert str(S.data_dir()) == str(tmp_path)


# ---- layer 2: the stores refuse a real data dir from a test -----------------------------------------

@pytest.fixture
def real(tmp_path, monkeypatch):
    """A stand-in for an install's real data dir, registered as protected for this test."""
    d = tmp_path / "the-install"
    (d / "state" / "agent-state").mkdir(parents=True)
    (d / "registry.json").write_text('{"agents": {}}')
    monkeypatch.setenv(S.PROTECTED_ENV, str(d))
    return d


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guard_refuses_inside_and_allows_outside(real, tmp_path):
    with pytest.raises(S.RealDataDirWrite, match="registry.json"):
        S.guard_test_write(real / "registry.json", "registry.json")
    with pytest.raises(S.RealDataDirWrite):
        S.guard_test_write(real, "the data dir itself")
    S.guard_test_write(tmp_path / "scratch" / "registry.json", "registry.json")      # not protected


def test_guard_does_nothing_outside_pytest(real, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    S.guard_test_write(real / "registry.json", "registry.json")


def test_registry_lock_refuses_the_real_install(real):
    sys.path.insert(0, str(ROOT / "scripts"))
    import registry_lock
    with pytest.raises(S.RealDataDirWrite, match="registry lock"):
        with registry_lock.registry_lock(lock_path=real / "state" / "registry.lock"):
            pass
    assert not (real / "state" / "registry.lock").exists()


def test_tasks_db_connect_refuses_the_real_install(real):
    sys.path.insert(0, str(ROOT / "scripts"))
    import db_connect
    with pytest.raises(S.RealDataDirWrite, match="tasks.db"):
        db_connect.connect(str(real / "state" / "tasks.db"))
    assert not (real / "state" / "tasks.db").exists()
    db_connect.connect(":memory:").close()


def test_identity_db_refuses_the_real_install(real):
    sys.path.insert(0, str(ROOT / "scripts"))
    from identity_store import orchestra_db
    with pytest.raises(S.RealDataDirWrite, match="identity DB"):
        orchestra_db.get_connection(str(real / "state" / "orchestra-registry.db"))
    assert not (real / "state" / "orchestra-registry.db").exists()


def test_agent_state_writer_refuses_the_real_install(real, monkeypatch):
    status = _load("agent_status_fence", "scripts/agent-status.py")
    monkeypatch.setattr(status, "STATE_DIR", str(real / "state" / "agent-state"))
    with pytest.raises(S.RealDataDirWrite, match="agent state"):
        status._persist("a", {"state": "idle"})
    assert list((real / "state" / "agent-state").iterdir()) == []


def test_registry_update_refuses_the_real_install(real, monkeypatch):
    monkeypatch.setenv("REGISTRY_PATH", str(real / "registry.json"))
    ru = _load("registry_update_fence", "scripts/registry-update.py")
    monkeypatch.setattr(ru, "_cutover_registry_write", lambda *a, **k: None)
    with pytest.raises(S.RealDataDirWrite, match="registry.json"):
        ru.safe_update_registry("helper-a", {"runtime": "claude"})
    assert (real / "registry.json").read_text() == '{"agents": {}}'


def test_registry_update_default_is_the_data_dir_not_the_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("REGISTRY_PATH", raising=False)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    ru = _load("registry_update_default", "scripts/registry-update.py")
    assert ru.REGISTRY == str(tmp_path / "registry.json")


def test_seats_registry_write_refuses_the_real_install(real, monkeypatch):
    from orchestra_cli import seats
    st = S.load_settings()
    monkeypatch.setattr(st, "data_dir", real)
    with pytest.raises(S.RealDataDirWrite, match="registry.json"):
        seats.register_seat(st, "helper-a", gm=False, runtime="claude", model=None, tier=None, prompt=None)
    assert (real / "registry.json").read_text() == '{"agents": {}}'
