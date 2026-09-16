"""Writer-resume p7 (RED) — the §B sanctioned shim CLIs route to the store under cutover.

registry-update.py / sessions-update.py are the HIGHEST-frequency identity-write path
(every spawn-agent.sh auto-register + every lineage_daemon succession edge). The shims.py
LIBRARY writes the DB and is tested — but the CLI SCRIPTS the real callers invoke still do
open()+json.dump with NO cutover branch, so under cutover every spawn/lineage write bypasses
the store => drift-on-every-spawn (WRITER-ENUMERATION §F). This is the gap a library-only test
masked. p7 converts the CLI ENTRYPOINTS: under cutover the body routes to the store (no JSON),
flag-off is BYTE-IDENTICAL, ZERO caller churn. The tests below invoke the CLI as a subprocess
(the real entrypoint), NOT the shims.py library.

RED until the CLI bodies grow the cutover routing.
"""
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


def _conn(orch):
    return orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))


def _reg(orch):
    return json.loads((orch / "registry.json").read_text())


def _sess(orch):
    return json.loads((orch / "state" / "agent-sessions.json").read_text())


# --- registry-update.py ----------------------------------------------------

def test_registry_flag_off_byte_identical(orch):
    """INERT: flag off -> the CLI writes registry.json exactly as before."""
    r = _run(REG_CLI, ["a1", "--json", json.dumps({"status": "parked"})], orch)
    assert r.returncode == 0, r.stderr
    assert _reg(orch)["agents"]["a1"]["status"] == "parked"


def test_registry_cutover_update_routes_to_store_no_drift(orch):
    """Under cutover: the CLI routes the write to the store (no DIRECT json.dump), then
    the F3-chokepoint synchronously PROJECTS, so registry.json now REFLECTS the store
    (faithful projection = zero drift between file and store), not a frozen pre-write copy."""
    r = _run(REG_CLI, ["a1", "--json", json.dumps({"status": "parked"})], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    c = _conn(orch)
    try:
        row = c.execute("SELECT status FROM canonical WHERE root='a1'").fetchone()
    finally:
        c.close()
    assert row["status"] == "parked", "cutover: the status must land in the store"
    assert _reg(orch)["agents"]["a1"]["status"] == "parked", \
        "F3-chokepoint: registry.json reflects the store (projected), not frozen"


def test_registry_cutover_provisional_successor(orch):
    """A NEW successor (lineage_root of an existing lineage + generation) registers as
    a PROVISIONAL non-canonical generation -> projects as <root>-g<N>, no JSON write."""
    fields = {"name": "a1-g2", "tmux_session": "a1-g2", "lineage_root": "a1",
              "generation": 2, "model": "m", "tier": "T2", "runtime": "claude"}
    r = _run(REG_CLI, ["a1-g2", "--json", json.dumps(fields)], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    c = _conn(orch)
    try:
        reg = projector._build_faithful_registry(projector._read_faithful_snapshot(c))
    finally:
        c.close()
    assert "a1-g2" in reg["agents"] and reg["agents"]["a1-g2"]["status"] == "provisioning"
    # F3-chokepoint: the alias is now readable in registry.json IMMEDIATELY (projected),
    # so plan_spawn's registry read right after sees it — the whole point of the fix.
    assert _reg(orch)["agents"]["a1-g2"]["status"] == "provisioning", \
        "F3-chokepoint: the provisional alias must be spawn-readable in registry.json"


def test_registry_cutover_failclosed_unknown_identity(orch):
    """(b) no partial identities: a brand-new id with no lineage HARD-FAILS under
    cutover (never silently canonicalize via the generic config CLI)."""
    r = _run(REG_CLI, ["totally-new", "--json", json.dumps({"name": "totally-new"})],
             orch, cutover=True)
    assert r.returncode != 0, "cutover: unknown identity must be refused (fail-closed)"
    assert "totally-new" not in _reg(orch)["agents"]
    c = _conn(orch)
    try:
        assert c.execute("SELECT 1 FROM lineages WHERE root='totally-new'").fetchone() is None
    finally:
        c.close()


# --- sessions-update.py ----------------------------------------------------

def test_sessions_flag_off_byte_identical(orch):
    r = _run(SESS_CLI, ["a1", "--json", json.dumps({"note": "hello"})], orch)
    assert r.returncode == 0, r.stderr
    assert _sess(orch)["a1"]["note"] == "hello"


def test_sessions_cutover_routes_to_store_no_drift(orch):
    r = _run(SESS_CLI, ["a1", "--json", json.dumps({"note": "viadb"})], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    c = _conn(orch)
    try:
        row = c.execute("SELECT g.note FROM generations g JOIN canonical c "
                        "ON c.generation_id=g.id WHERE c.root='a1'").fetchone()
    finally:
        c.close()
    assert row["note"] == "viadb", "cutover: the session field must land in the store"
    assert _sess(orch)["a1"]["note"] == "viadb", \
        "F3-chokepoint: agent-sessions.json reflects the store (projected), not frozen"


# --- gm p7 tightenings: real caller shapes + phantom-lineage + integer gen ---

# The ACTUAL spawn-agent.sh:302 auto-register payload (no lineage_root, no generation).
_SPAWN_AGENT_SHAPE = {"name": "raw-new", "tier": "T2", "machine": "vps", "cwd": "/x",
                      "tmux_session": "raw-new", "system_prompt": "", "always_on": False,
                      "auto_registered": "2026-09-02T00:00:00Z"}

# The ACTUAL executors.py plan_register_successor:77 payload (inherited + successor id).
def _executors_successor_shape(root="a1", gen=2):
    return {"tier": "T2", "machine": "vps", "cwd": "/x", "system_prompt": "p",
            "model": "m", "always_on": True, "runtime": "claude",
            "name": f"{root}-g{gen}", "tmux_session": f"{root}-g{gen}",
            "generation": gen, "lineage_root": root, "handoff_from": root}


def test_spawn_agent_autoregister_shape_failclosed(orch):
    """Real spawn-agent.sh:302 shape (no lineage_root/generation) -> case 3 refuse."""
    r = _run(REG_CLI, ["raw-new", "--json", json.dumps(_SPAWN_AGENT_SHAPE)], orch, cutover=True)
    assert r.returncode != 0, "spawn auto-register of an unknown id must fail-closed under cutover"
    assert "raw-new" not in _reg(orch)["agents"]
    c = _conn(orch)
    try:
        assert c.execute("SELECT 1 FROM lineages WHERE root='raw-new'").fetchone() is None
    finally:
        c.close()


def test_executors_successor_shape_provisional(orch):
    """Real executors.py plan_register_successor shape -> case 2 provisional lands."""
    r = _run(REG_CLI, ["a1-g2", "--json", json.dumps(_executors_successor_shape())],
             orch, cutover=True)
    assert r.returncode == 0, r.stderr
    c = _conn(orch)
    try:
        reg = projector._build_faithful_registry(projector._read_faithful_snapshot(c))
    finally:
        c.close()
    assert reg["agents"]["a1-g2"]["status"] == "provisioning"
    # F3-chokepoint: readable in registry.json immediately (plan_spawn reads it next)
    assert _reg(orch)["agents"]["a1-g2"]["status"] == "provisioning"


def test_case2_phantom_lineage_failclosed(orch):
    """A lineage_root that does NOT resolve to a live lineage row must FALL to case 3
    fail-closed — never mint a provisional against a phantom lineage."""
    payload = _executors_successor_shape(root="ghost-lineage", gen=2)
    r = _run(REG_CLI, ["ghost-lineage-g2", "--json", json.dumps(payload)], orch, cutover=True)
    assert r.returncode != 0, "phantom lineage_root must fail-closed, not mint a provisional"
    c = _conn(orch)
    try:
        assert c.execute("SELECT 1 FROM generations WHERE root='ghost-lineage'").fetchone() is None
    finally:
        c.close()


def test_case2_noninteger_generation_failclosed(orch):
    """A real lineage but a NON-integer generation must CLEANLY fail-closed (exit 2),
    not crash — case 2 keys on an integer generation, not mere field-presence."""
    payload = _executors_successor_shape()
    payload["generation"] = "latest"  # not an int
    r = _run(REG_CLI, ["a1-glatest", "--json", json.dumps(payload)], orch, cutover=True)
    assert r.returncode == 2, f"expected clean refuse exit 2, got {r.returncode}: {r.stderr}"
    c = _conn(orch)
    try:
        assert c.execute("SELECT 1 FROM generations WHERE root='a1' AND generation=0"
                         ).fetchone() is None
    finally:
        c.close()


def test_sessions_sid_takeover_under_cutover(orch):
    """sessions-update sid change routes through the same-txn stale-sid take-over."""
    r = _run(SESS_CLI, ["a1", "--json", json.dumps({"session_id": "s2-new"})], orch, cutover=True)
    assert r.returncode == 0, r.stderr
    c = _conn(orch)
    try:
        row = c.execute("SELECT g.session_id FROM generations g JOIN canonical c "
                        "ON c.generation_id=g.id WHERE c.root='a1'").fetchone()
    finally:
        c.close()
    assert row["session_id"] == "s2-new", "sid take-over must land in the store"
    assert _sess(orch)["a1"]["session_id"] == "s2-new", \
        "F3-chokepoint: agent-sessions.json reflects the store sid (projected), not frozen"


def test_sessions_failclosed_no_canonical(orch):
    """sessions-update for a root with no canonical generation fail-closes under cutover."""
    r = _run(SESS_CLI, ["no-such-agent", "--json", json.dumps({"note": "x"})], orch, cutover=True)
    assert r.returncode == 2, f"expected clean refuse exit 2, got {r.returncode}: {r.stderr}"


# --- U17 convergence -------------------------------------------------------

def test_u17_spawn_plus_lineage_edge_converge_in_store_and_projection(orch):
    """U17: a successor register (spawn) + a lineage-edge update, both via the CLIs under
    cutover, converge in the STORE (write-truth) AND the file is a faithful projection of
    it (F3-chokepoint: the CLIs project synchronously, so registry/sessions REFLECT the
    store — zero drift between file and store — rather than a frozen pre-write copy)."""
    # spawn: provisional successor
    _run(REG_CLI, ["a1-g2", "--json", json.dumps(
        {"name": "a1-g2", "tmux_session": "a1-g2", "lineage_root": "a1",
         "generation": 2, "model": "m", "tier": "T2", "runtime": "claude"})],
        orch, cutover=True)
    # lineage edge: predecessor status (registry) + a session sid take-over (sessions)
    _run(REG_CLI, ["a1", "--json", json.dumps({"status": "parked"})], orch, cutover=True)
    _run(SESS_CLI, ["a1", "--json", json.dumps({"session_id": "s2", "note": "handoff"})],
         orch, cutover=True)
    # both converge in the store projection:
    c = _conn(orch)
    try:
        reg = projector._build_faithful_registry(projector._read_faithful_snapshot(c))
        crow = c.execute("SELECT status FROM canonical WHERE root='a1'").fetchone()
        srow = c.execute("SELECT g.session_id, g.note FROM generations g JOIN canonical c "
                         "ON c.generation_id=g.id WHERE c.root='a1'").fetchone()
    finally:
        c.close()
    assert "a1-g2" in reg["agents"], "spawn converged (provisional alias)"
    assert crow["status"] == "parked", "lineage edge converged (status)"
    assert srow["session_id"] == "s2" and srow["note"] == "handoff", \
        "session sid take-over + note converged in the store"
    # ZERO DRIFT: the on-disk files now equal the store projection (chokepoint projected):
    assert "a1-g2" in _reg(orch)["agents"], "registry.json reflects the store (projected)"
    assert _reg(orch)["agents"]["a1"]["status"] == "parked"
    assert _sess(orch)["a1"]["note"] == "handoff"
