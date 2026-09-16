"""Tests for executors.py -- ARMED action wiring that MUST NOT execute.

Every test here runs with armed=False and asserts on the *planned* argv
lists only. subprocess.run is monkeypatched to raise, proving nothing
ever shells out on the unarmed path.
"""

import json

import pytest

from scripts.lineage_daemon import executors

FAKE = "/tmp/fake"


@pytest.fixture(autouse=True)
def _forbid_subprocess(monkeypatch):
    """No test in this module may ever shell out."""
    def _boom(*a, **k):
        raise AssertionError("subprocess.run was called on an unarmed path")
    monkeypatch.setattr(executors.subprocess, "run", _boom)


# --- plan_spawn ---

def test_plan_spawn_unarmed_cmd():
    r = executors.plan_spawn("cand-g2", armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"] == [FAKE + "/spawn-agent.sh", "cand-g2"]


# --- plan_retire ---

def test_plan_retire_unarmed_cmd():
    r = executors.plan_retire("cand", armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"] == [
        "python3", FAKE + "/scripts/park-idle.py",
        "--agent", "cand", "--execute",
    ]


# --- plan_wire_edge ---

def test_plan_wire_edge_builds_three_commands():
    r = executors.plan_wire_edge(
        "cand", "cand-g2", 2, armed=False, orchestra_dir=FAKE
    )
    pred_succ = r["pred_succession"]
    succ_succ = r["succ_succession"]
    succ_gen = r["succ_generation"]
    assert pred_succ["executed"] is False
    assert succ_succ["executed"] is False
    assert succ_gen["executed"] is False

    # pred_succession: succession edge goes to agent-sessions (consumer store).
    # sessions-update.py cand --json {"succeeded_by": "cand-g2"}
    assert pred_succ["cmd"][:3] == [
        "python3", FAKE + "/scripts/sessions-update.py", "cand",
    ]
    assert pred_succ["cmd"][3] == "--json"
    assert json.loads(pred_succ["cmd"][4]) == {"succeeded_by": "cand-g2"}

    # succ_succession: successor lineage edge also in agent-sessions.
    # sessions-update.py cand-g2 --json {lineage_root, handoff_from}
    assert succ_succ["cmd"][:3] == [
        "python3", FAKE + "/scripts/sessions-update.py", "cand-g2",
    ]
    assert succ_succ["cmd"][3] == "--json"
    assert json.loads(succ_succ["cmd"][4]) == {
        "lineage_root": "cand",
        "handoff_from": "cand",
    }

    # succ_generation: generation is a registry display/recovery field.
    # registry-update.py cand-g2 --json {generation, handoff_from}
    assert succ_gen["cmd"][:3] == [
        "python3", FAKE + "/scripts/registry-update.py", "cand-g2",
    ]
    assert succ_gen["cmd"][3] == "--json"
    assert json.loads(succ_gen["cmd"][4]) == {
        "generation": 2,
        "handoff_from": "cand",
    }


# --- verify_successor (read-only, still gated behind armed) ---

def test_verify_successor_unarmed_cmd():
    r = executors.verify_successor("cand-g2", armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"] == [
        "python3", FAKE + "/scripts/agent-status.py", "cand-g2",
    ]


# --- Gap 1: plan_register_successor inherits the predecessor's role ---

def test_plan_register_successor_inherits_role():
    pred = {"tier": "T1", "machine": "vps", "cwd": "/home/x",
            "system_prompt": "prompts/gm.md", "model": "claude-opus-4-8[1m]",
            "always_on": True, "session_id": "should-not-copy"}
    r = executors.plan_register_successor("cand-g3", pred, 3, "cand",
                                          armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"][:3] == ["python3", FAKE + "/scripts/registry-update.py", "cand-g3"]
    fields = json.loads(r["cmd"][4])
    assert fields["system_prompt"] == "prompts/gm.md"
    assert fields["cwd"] == "/home/x" and fields["model"] == "claude-opus-4-8[1m]"
    assert fields["always_on"] is True and fields["tier"] == "T1"
    assert fields["name"] == "cand-g3" and fields["tmux_session"] == "cand-g3"
    assert fields["generation"] == 3 and fields["lineage_root"] == "cand"
    assert "session_id" not in fields   # never copy a stale sid


# --- Gap 2: plan_inject_init builds a durable msg_store init task ---

def test_plan_inject_init_cmd():
    r = executors.plan_inject_init("cand-g3", "cand", armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"][:2] == ["python3", FAKE + "/msg_store.py"]
    assert "--to" in r["cmd"] and r["cmd"][r["cmd"].index("--to") + 1] == "cand-g3"
    body = r["cmd"][-1]
    assert "handoff" in body.lower() and "MEMORY.md" in body
    assert "EXACTLY ONCE" in body   # R1 idempotent callback instruction


def test_plan_inject_init_commands_a_gradable_readback():
    """Gap-2: the init task must command the successor to produce a MACHINE-GRADABLE
    readback artifact — answer its .canary.json + write/commit <succ>.readback.md in
    the grader's per-question section convention — so the in-loop auto_grade has a
    parseable artifact instead of HOLD_GRADE. Without this the unattended T2 loop can
    never reach a strict PASS. Still unarmed: asserts on the planned body only."""
    r = executors.plan_inject_init("cand-g3", "cand", armed=False, orchestra_dir=FAKE)
    body = r["cmd"][-1]
    # points the successor at its canary artifact
    assert "cand-g3.canary.json" in body
    # names the exact readback artifact path the grader reads
    assert "cand-g3.readback.md" in body
    # demands the grader's line-start per-question section convention (**Q<N>**)
    assert "**Q" in body
    # must COMMIT the readback (T4 gate + auto_grade both read the committed file)
    assert "commit" in body.lower()
    # readback happens BEFORE continuing work (grade gates the rotation)
    assert body.lower().index("readback") < body.lower().index("continue")


# --- Gap 5: plan_verify_edge is a read-only check-mode call ---

def test_plan_verify_edge_unarmed_cmd():
    r = executors.plan_verify_edge("cand", "cand-g3", armed=False, orchestra_dir=FAKE)
    assert r["executed"] is False
    assert r["cmd"] == ["python3", FAKE + "/scripts/sessions-update.py", "cand"]


# --- Gap 7: plan_repin_canonical renames + clears succeeded_by, never guesses sid ---

def test_plan_repin_canonical_steps():
    r = executors.plan_repin_canonical("cand-g3", "cand", 3, armed=False,
                                       orchestra_dir=FAKE)
    assert r["rename"]["cmd"] == ["tmux", "rename-session", "-t", "cand-g3", "cand"]
    assert r["rename"]["executed"] is False
    reg = json.loads(r["registry_repin"]["cmd"][4])
    assert reg["name"] == "cand" and reg["tmux_session"] == "cand"
    assert reg["generation"] == 3 and reg["succeeded_by"] is None
    sess = json.loads(r["sessions_repin"]["cmd"][4])
    assert sess["tmux_session"] == "cand" and sess["succeeded_by"] is None
    assert "NEVER guess" in r["sid"]["note"] and r["sid"]["from_id"] == "cand-g3"


# --- no subprocess on any unarmed path (fixture already forbids it) ---

def test_no_subprocess_when_unarmed():
    executors.plan_spawn("a", armed=False, orchestra_dir=FAKE)
    executors.plan_retire("a", armed=False, orchestra_dir=FAKE)
    executors.plan_wire_edge("a", "b", 3, armed=False, orchestra_dir=FAKE)
    executors.verify_successor("a", armed=False, orchestra_dir=FAKE)
    executors.plan_register_successor("a", {}, 2, "a", armed=False, orchestra_dir=FAKE)
    executors.plan_inject_init("a-g2", "a", armed=False, orchestra_dir=FAKE)
    executors.plan_verify_edge("a", "a-g2", armed=False, orchestra_dir=FAKE)
    executors.plan_repin_canonical("a-g2", "a", 2, armed=False, orchestra_dir=FAKE)
    # If any had executed, the autouse fixture's _boom would have raised.


# --- default orchestra_dir falls back to module constant ---

def test_default_orchestra_dir_used():
    r = executors.plan_spawn("a", armed=False)
    assert r["cmd"][0] == executors.ORCHESTRA_DIR + "/spawn-agent.sh"
