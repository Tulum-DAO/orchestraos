"""RED-first — spawn_adopt.py: the fail-closed identity gate for spawn-agent.sh
under the identity-store cutover (gm msg_00bc6d2a, spawn-under-cutover class).

Root cause being fixed: spawn-agent.sh's only registration seam (registry-update.py
generic CLI) REFUSES to establish a NEW identity under cutover, and the sanctioned
U16 adopt seam (identity_writer.adopt_identity) was never wired into the general
spawn path — so operators route around it with raw tmux+claude spawns that come up
with ZERO identity rows (orphan/dual-chip; router dead-letters DB-first mail).

Contract (exit codes are the spawn-agent.sh interface):
  exit 0 + {"handled": false}  -> cutover inactive; caller does its legacy path
  exit 0 + {"handled": true}   -> identity registered (or already canonical) AND
                                  verified by DB re-read AND projected
  exit 3                       -> REFUSED: incomplete identity / verify failed /
                                  projection raised. Caller must NOT tmux-spawn.

Tests use the real sqlite engine via orchestra_db.init_db (real-engine fixture
rule) — hand-typed DB state forbidden.
"""
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import orchestra_db  # noqa: E402

CLI = os.path.join(_HERE, "spawn_adopt.py")


@pytest.fixture
def orch(tmp_path):
    (tmp_path / "state" / "agents").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps(
        {"version": 1, "machines": {"vps": {"hostname": "s"}},
         "agents": {}, "_retired_agents": {}}, indent=2))
    (tmp_path / "state" / "agent-sessions.json").write_text("{}")
    orchestra_db.init_db(str(tmp_path / "state" / "orchestra-registry.db"))
    return tmp_path


def _run(orch, args, cutover=True):
    env = dict(os.environ)
    env["ORCHESTRA_DIR"] = str(orch)
    env.pop("IDENTITY_STORE_CUTOVER", None)
    if cutover:
        (orch / "state" / "identity-store-cutover.flag").touch()
    else:
        flag = orch / "state" / "identity-store-cutover.flag"
        if flag.exists():
            flag.unlink()
    return subprocess.run([sys.executable, CLI, *args],
                          capture_output=True, text=True, env=env)


def _db(orch):
    return orchestra_db.get_connection(
        str(orch / "state" / "orchestra-registry.db"))


def _rows(orch, agent_id):
    c = _db(orch)
    try:
        return {
            "lineage": c.execute("SELECT * FROM lineages WHERE root=?",
                                 (agent_id,)).fetchone(),
            "canonical": c.execute("SELECT * FROM canonical WHERE root=?",
                                   (agent_id,)).fetchone(),
            "generation": c.execute(
                "SELECT * FROM generations WHERE root=? ORDER BY generation DESC",
                (agent_id,)).fetchone(),
        }
    finally:
        c.close()


def test_cutover_inactive_is_a_noop_passthrough(orch):
    """Flag absent -> handled:false, exit 0, ZERO DB writes (legacy path owns it)."""
    r = _run(orch, ["rider-x", "--runtime", "claude", "--model", "m",
                    "--tier", "T2"], cutover=False)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip())["handled"] is False
    assert _rows(orch, "rider-x")["canonical"] is None


def test_new_agent_registers_projects_and_verifies(orch):
    """The sanctioned NEW-agent path the cutover was missing: complete identity ->
    lineage + generation + canonical + runtime_state online + source_record doc,
    projected so the flat registry.json serves the row immediately."""
    r = _run(orch, ["rider-x", "--runtime", "claude", "--model",
                    "claude-opus-4-8[1m]", "--tier", "T2", "--cwd", "/x"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip())
    assert out["handled"] is True and out["verified"] is True
    rows = _rows(orch, "rider-x")
    assert rows["lineage"] is not None
    assert rows["canonical"] is not None
    assert rows["generation"]["generation"] == 1
    c = _db(orch)
    try:
        st = c.execute(
            "SELECT rs.status FROM runtime_state rs JOIN canonical cn "
            "ON cn.generation_id = rs.generation_id WHERE cn.root=?",
            ("rider-x",)).fetchone()
        doc = c.execute(
            "SELECT payload_json FROM source_records WHERE file='registry.json' "
            "AND kind='agent' AND key=?", ("rider-x",)).fetchone()
    finally:
        c.close()
    assert st is not None and st["status"] == "online"
    assert doc is not None, "full source_record doc missing (gutted-row class)"
    payload = json.loads(doc["payload_json"])
    assert payload.get("runtime") == "claude" and payload.get("name") == "rider-x"
    # projection: the flat registry now serves the row (F3 immediate-reader rule)
    flat = json.loads((orch / "registry.json").read_text())
    assert "rider-x" in flat.get("agents", {}), "row not projected to registry.json"


def test_incomplete_identity_refuses_and_writes_nothing(orch):
    """Missing model -> exit 3, NO rows minted (never fabricate a partial identity)."""
    r = _run(orch, ["rider-x", "--runtime", "claude", "--tier", "T2"])
    assert r.returncode == 3, f"expected refuse rc=3, got {r.returncode}: {r.stdout}"
    assert _rows(orch, "rider-x")["canonical"] is None
    assert _rows(orch, "rider-x")["lineage"] is None


def test_missing_runtime_refuses(orch):
    r = _run(orch, ["rider-x", "--model", "m", "--tier", "T2"])
    assert r.returncode == 3
    assert _rows(orch, "rider-x")["canonical"] is None


def test_existing_canonical_agent_passes_without_duplicate_mint(orch):
    """Respawn of an already-registered agent: no second generation, exit 0."""
    r1 = _run(orch, ["rider-x", "--runtime", "claude", "--model", "m",
                     "--tier", "T2"])
    assert r1.returncode == 0, r1.stderr
    r2 = _run(orch, ["rider-x", "--runtime", "claude", "--model", "m",
                     "--tier", "T2"])
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout.strip())["existing"] is True
    c = _db(orch)
    try:
        n = c.execute("SELECT COUNT(*) AS n FROM generations WHERE root=?",
                      ("rider-x",)).fetchone()["n"]
    finally:
        c.close()
    assert n == 1, "respawn must not mint a duplicate generation"


def test_projection_failure_fail_closes(orch, monkeypatch):
    """The spawn path OWNS the spawn (rotate_agent posture): a projection raise
    must abort BEFORE the seat comes up -> exit 3. Simulated by making the flat
    registry.json unwritable so project_now cannot replace it."""
    os.chmod(orch / "registry.json", 0o444)
    os.chmod(orch, 0o555)
    try:
        r = _run(orch, ["rider-x", "--runtime", "claude", "--model", "m",
                        "--tier", "T2"])
    finally:
        os.chmod(orch, 0o755)
        os.chmod(orch / "registry.json", 0o644)
    assert r.returncode == 3, (
        f"projection failure must fail-close the spawn, got rc={r.returncode}: "
        f"{r.stdout} {r.stderr}")


# --- gm msg_f145ea51: respawn of a root whose CANONICAL generation is retired -------------------
# 2026-09-16 13:43 ET: jarvis-gm-query.sh's pool fallback ran spawn-agent.sh task-gm; the root
# already had canonical -> gen 1, which the reconciler had retired 09-15 (no process). The gate
# said {"existing": true} and the spawn wrote flat status=online with NO new DB generation, so
# test_no_canonical_row_points_at_a_retired_generation failed every tick (guards_red). A respawn
# of a retired canonical must mint the next generation + repoint canonical BEFORE the flat write.

def _canon(orch, root):
    c = _db(orch)
    try:
        return c.execute(
            "SELECT g.generation, g.retired_at, g.promoted_at, g.model FROM canonical cn "
            "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?", (root,)).fetchone()
    finally:
        c.close()


def _retire_canonical(orch, root):
    c = _db(orch)
    try:
        c.execute("UPDATE generations SET retired_at=? WHERE id=(SELECT generation_id FROM canonical WHERE root=?)",
                  (orchestra_db._utcnow(), root))
        c.commit()
    finally:
        c.close()


def test_respawn_of_retired_canonical_mints_next_generation_before_flat_write(orch):
    r = _run(orch, ["task-x", "--runtime", "claude", "--model", "claude-opus-4-8[1m]", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    _retire_canonical(orch, "task-x")
    assert _canon(orch, "task-x")["retired_at"] is not None
    r2 = _run(orch, ["task-x", "--runtime", "claude", "--model", "claude-opus-4-8[1m]", "--tier", "T2"])
    assert r2.returncode == 0, r2.stderr
    out = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out["handled"] is True and out.get("respawned") is True and out["generation"] == 2
    row = _canon(orch, "task-x")
    assert row["generation"] == 2 and row["retired_at"] is None and row["promoted_at"]
    assert row["model"] == "claude-opus-4-8[1m]"
    reg = json.loads((orch / "registry.json").read_text())["agents"]["task-x"]
    assert reg.get("generation") == 2 and reg.get("status") == "online"        # projected DB-first
    c = _db(orch)
    try:
        bad = c.execute("SELECT COUNT(*) FROM canonical cn JOIN generations g ON g.id=cn.generation_id "
                        "WHERE g.retired_at IS NOT NULL").fetchone()[0]
    finally:
        c.close()
    assert bad == 0                                                             # the guard's predicate


def test_live_canonical_is_left_alone(orch):
    r = _run(orch, ["task-y", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    r2 = _run(orch, ["task-y", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    out = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out["existing"] is True and not out.get("respawned")
    assert _canon(orch, "task-y")["generation"] == 1


def test_respawn_without_a_model_refuses(orch):
    r = _run(orch, ["task-z", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    _retire_canonical(orch, "task-z")
    c = _db(orch)
    try:
        c.execute("UPDATE generations SET model='unknown' WHERE root='task-z'"); c.commit()
    finally:
        c.close()
    r2 = _run(orch, ["task-z"])
    assert r2.returncode == 3 and "model" in r2.stderr.lower()
    assert _canon(orch, "task-z")["generation"] == 1                         # nothing minted


def test_reestablish_of_fully_retired_lineage_mints_next_generation(orch):
    """the operator 2026-09-16 'resume apprvd-pm': lineage present, gen 1 retired by the reconciler
    (no process / no resume), NO canonical row. The default gate would adopt generation 1 —
    reusing the retired row and pointing canonical at it (the morning's guard failure). A
    re-establish must mint max(generation)+1 and point canonical at the live row."""
    r = _run(orch, ["apprvd-x", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    c = _db(orch)
    try:
        c.execute("UPDATE generations SET retired_at=? WHERE root='apprvd-x'", (orchestra_db._utcnow(),))
        c.execute("DELETE FROM canonical WHERE root='apprvd-x'")
        c.commit()
    finally:
        c.close()
    r2 = _run(orch, ["apprvd-x", "--runtime", "claude", "--model", "m2", "--tier", "T2"])
    assert r2.returncode == 0, r2.stderr
    out = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out["handled"] is True and out["generation"] == 2 and out.get("reestablished") is True
    row = _canon(orch, "apprvd-x")
    assert row["generation"] == 2 and row["retired_at"] is None and row["model"] == "m2"


def test_doc_system_prompt_defaults_to_prompts_file_when_present(orch):
    """the operator 'resume apprvd-pm' 2026-09-16: the seat booted on FOUNDATION_STATIC only because the
    auto-register doc carried system_prompt='' although prompts/apprvd-pm.md exists. The gate's
    document must default system_prompt to prompts/<id>.md when that file exists (else '')."""
    (orch / "prompts").mkdir()
    (orch / "prompts" / "role-x.md").write_text("# role-x\n")
    r = _run(orch, ["role-x", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    reg = json.loads((orch / "registry.json").read_text())["agents"]
    assert reg["role-x"]["system_prompt"] == "prompts/role-x.md"
    r2 = _run(orch, ["role-y", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r2.returncode == 0, r2.stderr
    reg = json.loads((orch / "registry.json").read_text())["agents"]
    assert reg["role-y"].get("system_prompt", "") == ""


# gm msg_e9a921fe (2026-09-16) ruling (2): roster-resume-all.sh raw tmux+`claude --resume` spawns
# registered NOTHING (orphan class). Every resume goes through spawn-agent.sh --resume <sid>,
# whose adopt gate receives the KNOWN sid: a resumed seat has a DB canonical + that sid at
# adopt time, not after a spawn_attribute_sid poll.

def test_respawn_of_retired_canonical_carries_a_supplied_session_id(orch):
    r = _run(orch, ["task-y", "--runtime", "claude", "--model", "claude-opus-4-8[1m]", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    _retire_canonical(orch, "task-y")
    sid = "5df4fb47-e0e3-42bb-8cb3-0a9b5788df37"
    r2 = _run(orch, ["task-y", "--runtime", "claude", "--model", "claude-opus-4-8[1m]", "--tier", "T2",
                     "--session-id", sid])
    assert r2.returncode == 0, r2.stderr
    c = _db(orch)
    try:
        row = c.execute("SELECT g.generation, g.session_id, g.retired_at, g.resume_command FROM canonical cn "
                        "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?", ("task-y",)).fetchone()
    finally:
        c.close()
    assert row["generation"] == 2 and row["retired_at"] is None
    assert row["session_id"] == sid, "resumed seat must carry the known sid on its canonical generation"
    reg = json.loads((orch / "registry.json").read_text())["agents"]["task-y"]
    assert reg.get("session_id") == sid and reg.get("generation") == 2
