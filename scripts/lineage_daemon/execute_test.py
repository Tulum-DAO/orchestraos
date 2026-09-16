"""Tests for execute.py -- the armed rotation orchestrator.

Every seam is a FAKE: the fake executors module records armed calls and returns
canned results, so these tests exercise the full ordering + double-kill-gate
WITHOUT shelling out, spawning, killing, or paging the operator. The safety properties
(canary-scoped, reversible-first, kill only on safe+approve, both-live on any
hold) are asserted here in code.
"""
import pytest

from scripts.lineage_daemon import execute as ex


# --- a fake executors module: records every armed call, returns canned results -

class FakeExecutors:
    def __init__(self, *, successor_alive=True, edge_ok=True):
        self.calls = []            # ordered list of (step_name, kwargs)
        self.successor_alive = successor_alive
        self.edge_ok = edge_ok

    def _rec(self, name, armed, **extra):
        self.calls.append((name, armed))
        r = {"cmd": [name], "executed": bool(armed)}
        r.update(extra)
        return r

    def plan_register_successor(self, succ, pred, gen, root, armed=False, orchestra_dir=None):
        return self._rec("register_successor", armed)

    def plan_spawn(self, succ, armed=False, orchestra_dir=None):
        return self._rec("spawn", armed)

    def verify_successor(self, succ, armed=False, orchestra_dir=None):
        return self._rec("verify_successor", armed, alive=self.successor_alive)

    def plan_inject_init(self, succ, pred, armed=False, orchestra_dir=None):
        return self._rec("inject_init", armed)

    def plan_wire_edge(self, pred, succ, gen, armed=False, orchestra_dir=None):
        return self._rec("wire_edge", armed)

    def plan_verify_edge(self, pred, succ, armed=False, orchestra_dir=None):
        return self._rec("verify_edge", armed, ok=self.edge_ok, succeeded_by=succ)

    def plan_retire(self, agent, armed=False, orchestra_dir=None):
        return self._rec("retire", armed)

    def plan_repin_canonical(self, succ, canonical, gen, armed=False, orchestra_dir=None):
        return self._rec("repin_canonical", armed)


REG = {"agents": {"cand": {"generation": 2, "system_prompt": "prompts/x.md",
                           "cwd": "/w", "tier": "T1"}}}


def _names(fake):
    return [n for (n, _armed) in fake.calls]


def _run(fake, **kw):
    kw.setdefault("safety_fn", lambda c: ("SUPERSEDED_SAFE", "ok"))
    kw.setdefault("approval_fn", lambda c, s: "approve")
    return ex.execute_rotation("cand", REG, executors_impl=fake, **kw)


# --- happy path: safe + approved -> full rotation incl. retire + repin --------

def test_full_rotation_on_safe_and_approve():
    fake = FakeExecutors()
    trace = _run(fake)
    assert trace["status"] == ex.DONE
    assert trace["successor"] == "cand-g3"   # gen 2 -> 3
    # full ordered sequence, retire BEFORE repin
    assert _names(fake) == [
        "register_successor", "spawn", "verify_successor", "inject_init",
        "wire_edge", "verify_edge", "retire", "repin_canonical"]
    # everything ran armed=True
    assert all(armed for (_n, armed) in fake.calls)


# --- KILL GATE: approval ------------------------------------------------------

def test_deny_leaves_both_live_no_retire():
    fake = FakeExecutors()
    trace = _run(fake, approval_fn=lambda c, s: "deny")
    assert trace["status"] == ex.HOLD_DENIED
    assert "retire" not in _names(fake)          # predecessor NOT killed
    assert "repin_canonical" not in _names(fake)
    # successor WAS brought up (reversible steps ran) -> transient two-heads, safe
    assert "spawn" in _names(fake) and "wire_edge" in _names(fake)


def test_timeout_leaves_both_live_no_retire():
    fake = FakeExecutors()
    trace = _run(fake, approval_fn=lambda c, s: "timeout")
    assert trace["status"] == ex.HOLD_TIMEOUT
    assert "retire" not in _names(fake)


def test_approval_only_asked_after_reversible_steps_and_safety():
    order = []
    fake = FakeExecutors()

    def safety(c):
        order.append("safety")
        return ("SUPERSEDED_SAFE", "ok")

    def approval(c, s):
        order.append("approval")
        return "approve"

    _run(fake, safety_fn=safety, approval_fn=approval)
    # safety re-check happens BEFORE approval; both AFTER verify_edge, BEFORE retire
    assert order == ["safety", "approval"]


# --- KILL GATE: execute-time safety re-check ----------------------------------

@pytest.mark.parametrize("cat,reason", [
    ("PENDING_WORK", "uncommitted/unpushed work in cwd"),   # 8a
    ("HELD", "the operator viewing in app"),                         # 8b
    ("PENDING_WORK", "recent activity within cooldown"),    # 8c
    ("ATTACHED", "a client is attached"),
    ("PROTECTED", "always_on"),
])
def test_safety_recheck_aborts_kill(cat, reason):
    fake = FakeExecutors()
    approved = {"asked": False}

    def approval(c, s):
        approved["asked"] = True
        return "approve"

    trace = _run(fake, safety_fn=lambda c: (cat, reason), approval_fn=approval)
    assert trace["status"] == ex.HOLD_UNSAFE
    assert "retire" not in _names(fake)          # kill aborted
    assert approved["asked"] is False            # approval NOT even requested when unsafe
    # both live: successor spawned, predecessor untouched
    assert "spawn" in _names(fake)


def test_safety_recheck_runs_after_edge_wired():
    seen = {}
    fake = FakeExecutors()

    def safety(c):
        seen["names_at_safety"] = _names(fake)   # what ran before the re-check
        return ("SUPERSEDED_SAFE", "ok")

    _run(fake, safety_fn=safety)
    assert "verify_edge" in seen["names_at_safety"]   # edge wired before we re-check


# --- pre-kill ABORTS: successor never came up / edge not wired ----------------

def test_abort_when_successor_not_live_kills_nothing():
    fake = FakeExecutors(successor_alive=False)
    trace = _run(fake)
    assert trace["status"] == ex.ABORT_NO_SUCCESSOR
    assert "retire" not in _names(fake)
    assert "wire_edge" not in _names(fake)       # stopped right after verify
    assert _names(fake) == ["register_successor", "spawn", "verify_successor"]


def test_abort_when_edge_not_wired_kills_nothing():
    fake = FakeExecutors(edge_ok=False)
    trace = _run(fake)
    assert trace["status"] == ex.ABORT_EDGE
    assert "retire" not in _names(fake)
    assert "repin_canonical" not in _names(fake)


# --- no-live-successor gate (double-spawn guard) ------------------------------

def test_aborts_if_successor_already_live():
    fake = FakeExecutors()
    trace = _run(fake, successor_live_fn=lambda s: True)
    assert trace["status"] == ex.ABORT_LIVE_SUCCESSOR
    assert fake.calls == []                      # NOTHING ran -- no double spawn


def test_proceeds_if_no_live_successor():
    fake = FakeExecutors()
    trace = _run(fake, successor_live_fn=lambda s: False)
    assert trace["status"] == ex.DONE


# --- generation derivation mirrors the planner --------------------------------

def test_generation_from_name_suffix():
    fake = FakeExecutors()
    trace = ex.execute_rotation(
        "ob-g4", {"agents": {}}, executors_impl=fake,
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        approval_fn=lambda c, s: "approve")
    assert trace["successor"] == "ob-g5"         # pred gen4 -> succ gen5
    assert trace["lineage_root"] == "ob"


# --- S3 confirm-and-correct gate (WS3 v2, DEC-1786724046) ---

def _step_names(trace):
    return [s["step"] for s in trace["steps"]]


def test_s3_confirmed_proceeds_to_retire():
    """confirm_fn -> confirmed: S3 step recorded, rotation proceeds to retire+repin."""
    fake = FakeExecutors()
    trace = _run(fake, confirm_fn=lambda c, s: {"outcome": "confirmed", "rounds": 0})
    assert trace["status"] == ex.DONE
    steps = _step_names(trace)
    assert "confirm_correct" in steps
    # S3 sits BEFORE retire in the trace
    assert steps.index("confirm_correct") < steps.index("retire")
    # the executors DID run retire + repin
    assert "retire" in _names(fake) and "repin_canonical" in _names(fake)


def test_s3_held_stops_before_kill_gates_no_retire():
    """confirm_fn -> held: HOLD_UNCONFIRMED, NOTHING retired, both stay live."""
    fake = FakeExecutors()
    approvals = []
    trace = _run(
        fake,
        confirm_fn=lambda c, s: {"outcome": "held", "rounds": 3,
                                 "reasons": ["comprehension:not-absorbed"]},
        approval_fn=lambda c, s: approvals.append(1) or "approve",
    )
    assert trace["status"] == ex.HOLD_UNCONFIRMED
    assert "confirm_correct" in _step_names(trace)
    assert "retire" not in _names(fake)          # predecessor NOT killed
    assert "repin_canonical" not in _names(fake)
    assert approvals == []                        # never even reached the approval gate


def test_s3_skipped_when_confirm_fn_none_v1_behavior():
    """No confirm_fn -> S3 skipped entirely (legacy/canary path unchanged)."""
    fake = FakeExecutors()
    trace = _run(fake)                       # _run does not set confirm_fn
    assert trace["status"] == ex.DONE
    assert "confirm_correct" not in _step_names(trace)


# --- grade_fn: the T2 auto-grade machine gate (after S3, before KILL GATE 2) ---

def test_grade_pass_records_step_and_proceeds_to_retire():
    """grade_fn -> PASS: auto_grade step recorded BEFORE the kill gates; retire runs."""
    fake = FakeExecutors()
    trace = _run(
        fake,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "PASS", "strict": True},
    )
    assert trace["status"] == ex.DONE
    steps = _step_names(trace)
    assert "auto_grade" in steps
    # auto_grade sits AFTER S3 confirm and BEFORE the approval gate + retire
    assert steps.index("confirm_correct") < steps.index("auto_grade")
    assert steps.index("auto_grade") < steps.index("approval")
    assert steps.index("auto_grade") < steps.index("retire")
    assert "retire" in _names(fake) and "repin_canonical" in _names(fake)


def test_grade_fail_holds_no_approval_no_retire():
    """grade_fn -> FAIL: HOLD_GRADE, approval never reached, NOTHING retired."""
    fake = FakeExecutors()
    approvals = []
    trace = _run(
        fake,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "FAIL"},
        approval_fn=lambda c, s: approvals.append(1) or "approve",
    )
    assert trace["status"] == ex.HOLD_GRADE
    assert "auto_grade" in _step_names(trace)
    assert "approval" not in _step_names(trace)   # kill gate 2 never reached
    assert "retire" not in _names(fake)           # predecessor NOT killed
    assert "repin_canonical" not in _names(fake)
    assert approvals == []


def test_grade_refused_holds_no_retire():
    """grade_fn -> REFUSED (harness/input defect): HOLD_GRADE, no retire."""
    fake = FakeExecutors()
    trace = _run(
        fake,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "REFUSED"},
    )
    assert trace["status"] == ex.HOLD_GRADE
    assert "auto_grade" in _step_names(trace)
    assert "retire" not in _names(fake)


def test_grade_runs_after_confirm_before_safety_and_approval():
    """Order: confirm_correct -> auto_grade -> safety_recheck -> approval -> retire."""
    order = []
    fake = FakeExecutors()

    def confirm(c, s):
        order.append("confirm")
        return {"outcome": "confirmed"}

    def grade(c, s):
        order.append("grade")
        return {"disposition": "PASS"}

    def safety(c):
        order.append("safety")
        return ("SUPERSEDED_SAFE", "ok")

    def approval(c, s):
        order.append("approval")
        return "approve"

    ex.execute_rotation("cand", REG, executors_impl=fake, confirm_fn=confirm,
                        grade_fn=grade, safety_fn=safety, approval_fn=approval)
    assert order == ["confirm", "grade", "safety", "approval"]


def test_grade_holds_before_safety_recheck_runs():
    """A grade FAIL holds BEFORE KILL GATE 1 (safety re-check) even fires."""
    fake = FakeExecutors()
    safety_calls = []
    trace = _run(
        fake,
        grade_fn=lambda c, s: {"disposition": "FAIL"},
        safety_fn=lambda c: safety_calls.append(1) or ("SUPERSEDED_SAFE", "ok"),
    )
    assert trace["status"] == ex.HOLD_GRADE
    assert safety_calls == []                     # safety re-check never reached


def test_grade_fn_none_v1_behavior_byte_identical():
    """No grade_fn -> auto_grade skipped entirely; happy path unchanged."""
    fake = FakeExecutors()
    trace = _run(fake)                       # _run does not set grade_fn
    assert trace["status"] == ex.DONE
    assert "auto_grade" not in _step_names(trace)
    assert _names(fake) == [
        "register_successor", "spawn", "verify_successor", "inject_init",
        "wire_edge", "verify_edge", "retire", "repin_canonical"]


# --- INTEGRATION: the full unattended auto-loop (grade PASS + graduation auto-approve) ---

def test_autoloop_graded_confirmed_graduated_reaches_retire_no_card():
    """The end-state this whole task wires: a STRICT-graded + S3-confirmed +
    graduated+armed rotation reaches retire with the auto_grade step recorded BEFORE
    KILL GATE 2, and KILL GATE 2 auto-approves (NO the operator card is posted)."""
    from scripts.lineage_daemon.graduation_approval import (
        make_graduation_gated_approval)
    fake = FakeExecutors()
    card_calls = []
    approval = make_graduation_gated_approval(
        {"agent_id": "cand", "lineage_root": "cand", "successor": "cand-g3"},
        graduated_fn=lambda seat: True,          # cold-verified graduated-T2
        confirmed_fn=lambda c, s: True,          # S3 confirmed for THIS rotation
        fallback_fn=lambda c, s: card_calls.append(1) or "deny")  # the the operator card
    trace = ex.execute_rotation(
        "cand", REG, executors_impl=fake,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "PASS", "strict": True,
                               "artifact_written": True},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        approval_fn=approval)
    assert trace["status"] == ex.DONE
    steps = _step_names(trace)
    assert steps.index("auto_grade") < steps.index("approval")   # grade BEFORE kill gate 2
    assert "retire" in _names(fake) and "repin_canonical" in _names(fake)
    assert card_calls == []                       # auto-approved: NO the operator card posted


def test_autoloop_not_graduated_falls_back_to_card():
    """Same rotation but NOT graduated -> KILL GATE 2 falls back to the the operator card
    (auto-approve is fail-toward-the-card)."""
    from scripts.lineage_daemon.graduation_approval import (
        make_graduation_gated_approval)
    fake = FakeExecutors()
    card_calls = []
    approval = make_graduation_gated_approval(
        {"agent_id": "cand", "lineage_root": "cand", "successor": "cand-g3"},
        graduated_fn=lambda seat: False,         # supervised / not graduated
        confirmed_fn=lambda c, s: True,
        fallback_fn=lambda c, s: card_calls.append(1) or "approve")
    trace = ex.execute_rotation(
        "cand", REG, executors_impl=fake,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "PASS"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        approval_fn=approval)
    assert trace["status"] == ex.DONE            # card said approve -> still retires
    assert card_calls == [1]                      # the the operator card WAS consulted
