"""F3-chokepoint (RED) — project_now INSIDE the shim CLIs (registry-update/sessions-update).

ob's F3 (a) fixed the DIRECT identity_writer caller (rotate_agent). But the SAME
read-your-writes race exists on 2 more write-then-spawn paths: lineage_daemon/executors.py
(plan_register_successor via registry-update.py → plan_spawn) and spinup-orchestra-builder
-v2.sh (DB-CLI write → tmux spawn). The chokepoint fix (gm): call identity_writer.project_now
INSIDE the shim CLIs after a successful DB write under cutover — one place covers executors
+ spinup + every current/future CLI writer BY CONSTRUCTION (the §B-CLI-gap lesson).

POSTURE CONTRAST (documented, RED'd):
  * rotate_agent (OWNS the spawn) — project_now RAISES → abort BEFORE spawn (never spawn
    into an unprojected state).
  * shim CLI (does NOT own the spawn) — the DB write is durable + correct; a project_now
    failure is SURFACED LOUDLY but does NOT roll back the committed write. The daemon's
    debounced pass refreshes the projection; if a caller spawns before that, U16 (b)
    refuses safely (no partial mint). Rolling back a durable identity write would be worse.

RED until the shim CLIs call project_now post-write under cutover.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.identity_store import migrate, orchestra_db, projector

REPO = Path(__file__).resolve().parents[2]
REG_CLI = REPO / "scripts" / "registry-update.py"
SESS_CLI = REPO / "scripts" / "sessions-update.py"


def _run(cli, args, orch, cutover=False):
    env = dict(os.environ)
    env["ORCHESTRA_DIR"] = str(orch)
    env["REGISTRY_PATH"] = str(orch / "registry.json")
    env.pop("IDENTITY_STORE_CUTOVER", None)
    if cutover:
        env["IDENTITY_STORE_CUTOVER"] = "1"
    return subprocess.run([sys.executable, str(cli), *args], env=env,
                          capture_output=True, text=True, cwd=str(REPO))


def _load_cli(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def orch(tmp_path):
    (tmp_path / "state" / "agents").mkdir(parents=True)
    reg = {"version": 1, "last_updated": "t0", "machines": {"vps": {"hostname": "s"}},
           "agents": {"a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                             "runtime": "claude", "model": "m", "tmux_session": "a1",
                             "always_on": True, "system_prompt": "p", "status": "online",
                             "generation": 1, "session_id": "s1", "lineage_root": "a1"}},
           "_retired_agents": {}, "_provisional": {}, "_canonical": {"a1": "x"}}
    (tmp_path / "registry.json").write_text(json.dumps(reg, indent=2))
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(
        {"a1": {"session_id": "s1", "model": "m", "generation": 1, "status": "online",
                "tmux_session": "a1"}}))
    (tmp_path / "state" / "agents" / "a1.json").write_text(json.dumps(
        {"agent_id": "a1", "status": "online"}))
    dbp = tmp_path / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        migrate.migrate(c, registry_path=str(tmp_path / "registry.json"),
                        sessions_path=str(tmp_path / "state" / "agent-sessions.json"),
                        agents_dir=str(tmp_path / "state" / "agents"))
    finally:
        c.close()
    return tmp_path


def _reg(orch):
    return json.loads((orch / "registry.json").read_text())


# executors.py plan_register_successor:77 shape (inherited + successor id)
def _executors_shape(root="a1", gen=2):
    return {"tier": "T2", "machine": "vps", "cwd": "/x", "system_prompt": "p",
            "model": "m", "always_on": True, "runtime": "claude",
            "name": f"{root}-g{gen}", "tmux_session": f"{root}-g{gen}",
            "generation": gen, "lineage_root": root, "handoff_from": root}


def test_executors_shape_alias_readable_immediately_via_cli(orch):
    """THE chokepoint proof: registry-update.py with the executors successor shape under
    cutover projects the -g2 alias into registry.json SYNCHRONOUSLY (no daemon in the
    test), so plan_spawn's registry read right after sees it — F3 race closed."""
    r = _run(REG_CLI, ["a1-g2", "--json", json.dumps(_executors_shape())], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    reg = _reg(orch)
    assert "a1-g2" in reg["agents"], \
        "chokepoint: the provisional alias must be in registry.json immediately post-CLI"
    assert reg["agents"]["a1-g2"]["status"] == "provisioning"


def test_sessions_cli_refreshes_projection_immediately(orch):
    """sessions-update.py under cutover also force-projects: an existing-canonical session
    field write refreshes registry.json/agent-sessions.json synchronously (sidecar fresh)."""
    r = _run(SESS_CLI, ["a1", "--json", json.dumps({"note": "hello"})], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    # the faithful projection sidecar exists (project_now ran)
    assert (orch / projector.FAITHFUL_META_NAME).exists(), \
        "chokepoint: sessions CLI must project synchronously under cutover"


def test_inert_flag_off_no_projection(orch):
    """INERT: flag off -> the CLI does its legacy JSON write and does NOT project (no
    sidecar); byte-identical behavior, zero added cost."""
    r = _run(REG_CLI, ["a1", "--json", json.dumps({"status": "parked"})], orch, cutover=False)
    assert r.returncode == 0, r.stderr
    assert not (orch / projector.FAITHFUL_META_NAME).exists(), \
        "flag-off: no synchronous projection"
    assert _reg(orch)["agents"]["a1"]["status"] == "parked", "legacy JSON write unchanged"


def test_project_now_failure_surfaces_but_db_persists(orch, monkeypatch):
    """POSTURE (shim != rotate_agent): a project_now failure is SURFACED but does NOT roll
    back the committed DB write (the daemon catches up). In-process (patch project_now)."""
    m = _load_cli("regupd_posture", str(REG_CLI))
    monkeypatch.setenv("ORCHESTRA_DIR", str(orch))
    monkeypatch.setenv("REGISTRY_PATH", str(orch / "registry.json"))
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    from scripts.identity_store import identity_writer
    monkeypatch.setattr(identity_writer, "project_now",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("projector down")))
    # the CLI write must NOT raise (durable DB write persists); posture is surface-not-abort
    res = m.safe_update_registry("a1", {"status": "parked"})
    assert res is not None
    c = orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))
    try:
        row = c.execute("SELECT status FROM canonical WHERE root='a1'").fetchone()
    finally:
        c.close()
    assert row["status"] == "parked", "DB write must PERSIST despite project_now failure"
