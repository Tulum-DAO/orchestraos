"""RED-first (the operator 'resume apprvd-pm', 2026-09-16): a PLAIN spawn left generations.session_id
NULL / no resume_command until a human or the */15 reconciler attributed it. spawn-agent.sh now
runs spawn_attribute_sid after boot: bounded-poll the provider-agnostic resolver, write the sid
DB-first (session take-over + registry doc keys), re-project. Real sqlite engine fixture."""
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import orchestra_db, spawn_attribute_sid  # noqa: E402

CLI = os.path.join(_HERE, "spawn_adopt.py")


@pytest.fixture
def orch(tmp_path, monkeypatch):
    (tmp_path / "state" / "agents").mkdir(parents=True)
    (tmp_path / "state" / "wal").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps(
        {"version": 1, "machines": {"vps": {"hostname": "s"}}, "agents": {}, "_retired_agents": {}}))
    (tmp_path / "state" / "agent-sessions.json").write_text("{}")
    orchestra_db.init_db(str(tmp_path / "state" / "orchestra-registry.db"))
    (tmp_path / "state" / "identity-store-cutover.flag").touch()
    env = dict(os.environ, ORCHESTRA_DIR=str(tmp_path)); env.pop("IDENTITY_STORE_CUTOVER", None)
    r = subprocess.run([sys.executable, CLI, "seat-q", "--runtime", "claude", "--model", "m", "--tier", "T2"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return tmp_path


def _gen(orch):
    c = orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))
    try:
        return c.execute("SELECT g.session_id, g.resume_command FROM canonical cn JOIN generations g "
                         "ON g.id=cn.generation_id WHERE cn.root='seat-q'").fetchone()
    finally:
        c.close()


def test_attributes_sid_db_first_and_projects(orch):
    calls = {"n": 0}
    def resolver(alias):
        calls["n"] += 1
        return None if calls["n"] < 3 else "sid-123"          # boots on the 3rd poll
    out = spawn_attribute_sid.attribute(str(orch), "seat-q", resolve_cid_fn=resolver,
                                        runtime="claude", attempts=5, sleep_fn=lambda: None)
    assert out == {"handled": True, "sid": "sid-123"}
    row = _gen(orch)
    assert row["session_id"] == "sid-123"
    assert row["resume_command"] == "claude --resume sid-123 --dangerously-skip-permissions"
    reg = json.loads((orch / "registry.json").read_text())["agents"]["seat-q"]
    ses = json.loads((orch / "state" / "agent-sessions.json").read_text())["seat-q"]
    assert reg["session_id"] == "sid-123" and ses["session_id"] == "sid-123"


def test_unresolvable_seat_is_bounded_and_writes_nothing(orch):
    out = spawn_attribute_sid.attribute(str(orch), "seat-q", resolve_cid_fn=lambda a: None,
                                        runtime="claude", attempts=3, sleep_fn=lambda: None)
    assert out == {"handled": True, "sid": None}
    assert _gen(orch)["session_id"] is None


def test_inert_when_cutover_off(orch):
    (orch / "state" / "identity-store-cutover.flag").unlink()
    out = spawn_attribute_sid.attribute(str(orch), "seat-q", resolve_cid_fn=lambda a: "x",
                                        runtime="claude", attempts=1, sleep_fn=lambda: None)
    assert out == {"handled": False, "sid": None}
