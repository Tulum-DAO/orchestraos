"""Tests for beat.py — the canary-scoped rotation-beat composition (WS3 v2).

Every seam is a FAKE: fake executors record armed calls, the confirm_fn is a
canned outcome or the real two_sample_confirm over synthetic snapshots. Nothing
spawns, kills, injects, or pages the operator. The invariants asserted here:
  * canary scope (non-canary -> observe, never armed),
  * rotation lock blocks a lineage already rotating,
  * soft_handoff emits the author-trigger + blocks on a hollow/stale handoff,
  * hard_rotate wires the S3 confirm_fn + records a HOLD in the ledger,
  * two_sample_confirm requires BOTH samples (drift at sample 2 -> held).
"""
import pytest
from scripts.lineage_daemon import beat
from scripts.lineage_daemon import execute as ex
from scripts.focus_registry.gate import rotation_gate


# --- fake executors (mirror execute_test) -------------------------------------

class FakeExecutors:
    def __init__(self, *, successor_alive=True, edge_ok=True):
        self.calls = []
        self.successor_alive = successor_alive
        self.edge_ok = edge_ok

    def _rec(self, name, armed, **extra):
        self.calls.append(name)
        r = {"cmd": [name], "executed": bool(armed)}
        r.update(extra)
        return r

    def plan_register_successor(self, s, p, g, r, armed=False, orchestra_dir=None):
        return self._rec("register_successor", armed)

    def plan_spawn(self, s, armed=False, orchestra_dir=None):
        return self._rec("spawn", armed)

    def verify_successor(self, s, armed=False, orchestra_dir=None):
        return self._rec("verify_successor", armed, alive=self.successor_alive)

    def plan_inject_init(self, s, p, armed=False, orchestra_dir=None):
        return self._rec("inject_init", armed)

    def plan_wire_edge(self, p, s, g, armed=False, orchestra_dir=None):
        return self._rec("wire_edge", armed)

    def plan_verify_edge(self, p, s, armed=False, orchestra_dir=None):
        return self._rec("verify_edge", armed, ok=self.edge_ok, succeeded_by=s)

    def plan_retire(self, a, armed=False, orchestra_dir=None):
        return self._rec("retire", armed)

    def plan_repin_canonical(self, s, c, g, armed=False, orchestra_dir=None):
        return self._rec("repin_canonical", armed)


REG = {"agents": {"cx-canary": {"generation": 1, "system_prompt": "prompts/x.md",
                                "cwd": "/w", "tier": "T2"}}}
SAFE = lambda c: ("SUPERSEDED_SAFE", "ok")
APPROVE = lambda c, s: "approve"


def _agent(pct, *, aid="cx-canary", tier_class="T2", death=None):
    return {"agent_id": aid, "tier_class": tier_class,
            "death": death or {}, "ctx": {"status_bar_pct": pct}}


# --- canary scope -------------------------------------------------------------

def test_non_canary_is_observe_never_armed():
    fake = FakeExecutors()
    t = beat.rotation_beat(_agent(95, aid="someone-else"), REG,
                           canary="cx-canary", now=0, executors_impl=fake,
                           safety_fn=SAFE, approval_fn=APPROVE)
    assert t["status"] == beat.OBSERVE
    assert fake.calls == []            # NOTHING ran for a non-canary agent


def test_healthy_ctx_is_noop():
    fake = FakeExecutors()
    t = beat.rotation_beat(_agent(40), REG, canary="cx-canary", now=0,
                           executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE)
    assert t["status"] == beat.NOOP
    assert fake.calls == []


# --- rotation lock (H10) ------------------------------------------------------

def test_locked_when_lineage_already_rotating():
    fake = FakeExecutors()
    locks = {"rotations": [{"canary": "cx-canary-other", "lineage_root": "cx-canary",
                            "successor": "cx-canary-g9", "verifier": "cx-canary-other",
                            "escalation_target": "gm", "acquired_at": 0}]}
    t = beat.rotation_beat(_agent(95), REG, canary="cx-canary", now=1, locks=locks,
                           executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
                           confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert t["status"] == beat.LOCKED
    assert "already rotating" in t["reason"]
    assert fake.calls == []            # execute_rotation never entered under the lock


def test_lock_released_after_a_completed_beat():
    fake = FakeExecutors()
    t = beat.rotation_beat(_agent(95), REG, canary="cx-canary", now=5,
                           executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
                           confirm_fn=lambda c, s: {"outcome": "confirmed"})
    # no in-flight rotations remain -> the next beat for this lineage is not blocked
    assert t["locks"]["rotations"] == []


# --- soft_handoff: author-trigger + readiness gate (S1, H2) -------------------

_RICH_HANDOFF = {
    "current_goal": "wire the beat",
    "phase_state": {"next_gate": "re-canary passes"},
    "open_loops": ["deep-loop-xyz789"],
    "decisions": [{"text": "single-trunk", "rationale": "collision-free"}],
    "next_3_actions": ["run the re-canary"],
    "canary_questions": [{"id": "q1", "question": "a?", "source_pointer": "jsonl:msg_aa"},
                         {"id": "q2", "question": "b?", "source_pointer": "jsonl:msg_bb"},
                         {"id": "q3", "question": "c?", "source_pointer": "jsonl:msg_cc"}],
    "hazards": ["shared-dirty-tree"],
    "first_effect": {"kind": "session", "target": "cx-canary-g2"},
}


def test_soft_handoff_blocks_on_missing_handoff_and_emits_trigger():
    injected = []
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None))     # not committed yet
    assert t["status"] == beat.SOFT_AUTHORING
    assert injected and injected[0][0] == "cx-canary"
    assert "SOFT-HANDOFF" in injected[0][1]         # the durable author instruction fired
    assert t["readiness"]["ready"] is False


def test_soft_handoff_ready_on_rich_fresh_handoff():
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: None,
        handoff_provider=lambda: (_RICH_HANDOFF, 100))   # fresh mtime == now
    assert t["status"] == beat.SOFT_READY
    assert t["readiness"]["ready"] is True


def test_soft_handoff_blocks_on_stale_handoff():
    # DEC-1788165818 piece-3: mtime staleness is REPLACED by baseline-relative
    # rev. A rich handoff whose rev == the per-seat baseline (unchanged since the
    # last rotation / soft-window open) stays SOFT_AUTHORING — no false SOFT_READY.
    staleh = dict(_RICH_HANDOFF, handoff_commit_sha="shaOLD", file_hash="hashOLD")
    hist = {"handoff_baseline": {"cx-canary": "shaOLD:hashOLD"}}
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda: (staleh, 9e9))    # fresh mtime -> must NOT rescue
    assert t["status"] == beat.SOFT_AUTHORING
    assert any("freshness" in r for r in t["readiness"]["reasons"])


def test_soft_ready_when_authored_rev_differs_from_baseline():
    # the fresh path: a rich handoff whose rev DIFFERS from the per-seat baseline
    # -> SOFT_READY (nudge suppressed).
    freshh = dict(_RICH_HANDOFF, handoff_commit_sha="shaNEW", file_hash="hashNEW")
    hist = {"handoff_baseline": {"cx-canary": "shaOLD:hashOLD"}}
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda: (freshh, 0))
    assert t["status"] == beat.SOFT_READY


def test_c1_no_handoff_at_open_sentinel_then_authored_is_fresh():
    # CONCUR 3: the "no handoff at open" sentinel is DISTINCT from absent baseline.
    # Beat 1: no handoff -> C1 captures the sentinel + nudges (SOFT_AUTHORING).
    # Beat 2: the seat has now AUTHORED a rich handoff (a real rev) -> rev !=
    # sentinel -> FRESH -> SOFT_READY. (Proves the sentinel path is ELIGIBLE.)
    hist = {}
    t1 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda: (None, None))       # no handoff at open
    assert t1["status"] == beat.SOFT_AUTHORING
    assert hist["handoff_baseline"]["cx-canary"] == beat._BASELINE_NO_HANDOFF
    authored = dict(_RICH_HANDOFF, handoff_commit_sha="shaNEW", file_hash="hashNEW")
    t2 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100001, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda: (authored, 0))
    assert t2["status"] == beat.SOFT_READY


def test_c3_promote_baseline_path_identity_unchanged_gen_stays_soft():
    # CONTRACT item-7 (path-identity BY EFFECT): set_promote_baseline records the
    # CONSUMED rev via a provider; a beat reading the SAME provider on an UNCHANGED
    # (non-authored) generation gets rev==baseline -> stays SOFT (no false
    # SOFT_READY). Proves baseline + readiness resolve the SAME candidate.
    handoff = dict(_RICH_HANDOFF, handoff_commit_sha="shaCONSUMED",
                   file_hash="hashCONSUMED")
    provider = lambda seat, root=None: (handoff, 100)
    hist = {}
    beat.set_promote_baseline(hist, "cx-canary", provider)    # C3 at promote
    assert hist["handoff_baseline"]["cx-canary"] == "shaCONSUMED:hashCONSUMED"
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=provider)                            # SAME provider, unchanged
    assert t["status"] == beat.SOFT_AUTHORING                 # rev==baseline -> SOFT


def test_c3_promote_baseline_then_new_author_is_fresh_even_proactive():
    # C3 fixes the proactive-author false-negative: baseline is set at promote to
    # the CONSUMED (old) rev, so the new gen's authored handoff (new rev) is FRESH
    # -> SOFT_READY, even though it authored before its first soft beat.
    consumed = dict(_RICH_HANDOFF, handoff_commit_sha="shaOLD", file_hash="hashOLD")
    hist = {}
    beat.set_promote_baseline(hist, "cx-canary",
                              lambda s, r=None: (consumed, 100))
    authored = dict(_RICH_HANDOFF, handoff_commit_sha="shaNEW", file_hash="hashNEW")
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda s, r=None: (authored, 100))
    assert t["status"] == beat.SOFT_READY


def test_c3_promote_baseline_no_handoff_sets_sentinel_not_absent():
    # no handoff at promote -> the sentinel (distinct from absent/None), so a later
    # authored rev is fresh — NOT fail-closed.
    hist = {}
    beat.set_promote_baseline(hist, "cx-canary", lambda s, r=None: (None, None))
    assert hist["handoff_baseline"]["cx-canary"] == beat._BASELINE_NO_HANDOFF


def test_baseline_persists_across_daemon_restart_roundtrip(tmp_path):
    # STORE (gen28): handoff_baseline lives in the same durable history dict as
    # soft_reminders (state/fleet-beat-state.json). A capture must survive a
    # save_state -> load_state round-trip (durability is the whole point).
    from scripts.lineage_daemon import cron_beat
    state_path = tmp_path / "fleet-beat-state.json"
    hist = {}
    beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda: (None, None))
    captured = hist["handoff_baseline"]["cx-canary"]
    cron_beat.save_state({"locks": {"rotations": []}, "ledger": {"holds": []},
                          "history": hist}, path=str(state_path))
    reloaded = cron_beat.load_state(path=str(state_path))
    assert reloaded["history"]["handoff_baseline"]["cx-canary"] == captured


# --- F17: soft_handoff recognizes a COMPLETED handoff (no re-ping) ------------

def test_soft_handoff_complete_suppresses_author_reping():
    # The three-artifact handoff is already COMPLETE (doc committed + canary
    # authored + readback PASS). re-verify-at-emit (b53311d69 dedup class) must
    # NOT re-ping the author-trigger; the beat reports SOFT_COMPLETE.
    injected = []
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (_RICH_HANDOFF, 100),
        completion_fn=lambda successor: True)          # successor already passed
    assert t["status"] == beat.SOFT_COMPLETE
    assert injected == []                              # author re-ping suppressed


def test_soft_handoff_incomplete_still_emits_author_trigger():
    # completion re-verify says NOT complete -> unchanged behavior (emit + gate).
    injected = []
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None),
        completion_fn=lambda successor: False)
    assert t["status"] == beat.SOFT_AUTHORING
    assert injected and "SOFT-HANDOFF" in injected[0][1]


# --- 2a: resolve the ACTUAL successor from the lineage chain (not -g{N+1}) ----

def test_resolve_successor_from_sessions_succeeded_by():
    # sessions is source of truth; succeeded_by names the successor directly.
    reg = {"agents": {"second-brain-dev-gen2": {"generation": 2,
                      "lineage_root": "second-brain-dev"}}}
    sess = {"second-brain-dev-gen2": {"succeeded_by": "second-brain-dev-3"}}
    assert beat.resolve_successor_name(
        "second-brain-dev-gen2", reg, sess) == "second-brain-dev-3"


def test_resolve_successor_sessions_first_over_registry():
    reg = {"agents": {"x-gen2": {"generation": 2, "succeeded_by": "x-REGVAL"}}}
    sess = {"x-gen2": {"succeeded_by": "x-SESSVAL"}}
    assert beat.resolve_successor_name("x-gen2", reg, sess) == "x-SESSVAL"


def test_resolve_successor_registry_fallback_when_sessions_absent():
    reg = {"agents": {"x-gen2": {"generation": 2, "succeeded_by": "x-gen3"}}}
    assert beat.resolve_successor_name("x-gen2", reg, {}) == "x-gen3"


def test_resolve_successor_reverse_lookup_pre_promote_two_head():
    # PRE-promote two-head: succeeded_by NOT yet set (promote sets it). Find the
    # live successor by parent== predecessor (second-brain-dev-3 shape).
    reg = {"agents": {
        "second-brain-dev-gen2": {"generation": 2, "lineage_root": "second-brain-dev"},
        "second-brain-dev-3": {"generation": 3, "lineage_root": "second-brain-dev",
                               "parent": "second-brain-dev-gen2"}}}
    sess = {"second-brain-dev-3": {"parent": "second-brain-dev-gen2"}}
    assert beat.resolve_successor_name(
        "second-brain-dev-gen2", reg, sess) == "second-brain-dev-3"


def test_resolve_successor_reverse_lookup_by_higher_generation():
    reg = {"agents": {
        "x-gen2": {"generation": 2, "lineage_root": "x"},
        "x-gen3": {"generation": 3, "lineage_root": "x"}}}
    assert beat.resolve_successor_name("x-gen2", reg, {}) == "x-gen3"


def test_resolve_successor_none_when_unresolvable():
    # No succeeded_by, no higher-gen lineage member -> None (fail-closed: caller
    # keeps the nudge rather than guessing the brittle -g{N+1} string).
    reg = {"agents": {"x-gen2": {"generation": 2, "lineage_root": "x"}}}
    assert beat.resolve_successor_name("x-gen2", reg, {}) is None


def test_resolve_successor_never_returns_self():
    reg = {"agents": {"x-gen2": {"generation": 2, "lineage_root": "x",
                                 "succeeded_by": "x-gen2"}}}
    # a row that names itself as successor is incoherent -> None, never self.
    assert beat.resolve_successor_name("x-gen2", reg, {}) is None


def test_soft_complete_uses_resolved_name_when_sessions_injected():
    # With sessions injected, completion_fn is called with the RESOLVED successor
    # name (second-brain-dev-3), NOT the brittle -g string.
    seen = []
    reg = {"agents": {"second-brain-dev-gen2": {"generation": 2,
                      "lineage_root": "second-brain-dev", "tier": "T2",
                      "system_prompt": "p.md", "cwd": "/w"}}}
    sess = {"second-brain-dev-gen2": {"succeeded_by": "second-brain-dev-3"}}

    def comp(name):
        seen.append(name)
        return name == "second-brain-dev-3"

    t = beat.rotation_beat(
        _agent(75, aid="second-brain-dev-gen2"), reg, canary="second-brain-dev-gen2",
        now=100, session_turns=50, sessions_meta=sess,
        author_inject=lambda a, txt: None,
        handoff_provider=lambda: (_RICH_HANDOFF, 100),
        completion_fn=comp)
    assert seen == ["second-brain-dev-3"]
    assert t["status"] == beat.SOFT_COMPLETE


def test_soft_complete_fail_closed_when_unresolvable_keeps_nudge():
    # sessions injected but successor unresolvable -> completion check is skipped
    # (fail-closed), the author-trigger still fires (nudge kept).
    injected = []
    reg = {"agents": {"lonely-gen2": {"generation": 2, "lineage_root": "lonely",
                      "tier": "T2", "system_prompt": "p.md", "cwd": "/w"}}}
    t = beat.rotation_beat(
        _agent(75, aid="lonely-gen2"), reg, canary="lonely-gen2", now=100,
        session_turns=50, sessions_meta={},
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None),
        completion_fn=lambda name: True)   # would suppress IF called — must NOT be
    assert t["status"] == beat.SOFT_AUTHORING
    assert injected                        # nudge kept (fail-closed)


def test_soft_handoff_no_completion_fn_defaults_to_emit():
    # backward-compat: absent completion_fn -> never suppresses (old behavior).
    injected = []
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None))
    assert t["status"] == beat.SOFT_AUTHORING
    assert injected and injected[0][0] == "cx-canary"


def test_soft_ready_suppresses_nudge():
    # If the handoff is ready (rich + FRESH: rev differs from the per-seat
    # baseline), suppress the nudge completely on all beats. (Provenance-bearing
    # handoff — the live provider always injects commit_sha/file_hash.)
    injected = []
    freshh = dict(_RICH_HANDOFF, handoff_commit_sha="shaNEW", file_hash="hashNEW")
    history = {"handoff_baseline": {"cx-canary": "shaOLD:hashOLD"}}
    t = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (freshh, 100),
        history=history)
    assert t["status"] == beat.SOFT_READY
    assert len(injected) == 0  # Suppressed!

    # Second beat with same handoff:
    t2 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=110, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (freshh, 100),
        history=history)
    assert t2["status"] == beat.SOFT_READY
    assert len(injected) == 0  # Still suppressed!


def test_soft_authoring_deduplicates_nudges():
    # If the handoff is not ready, we nudge on the first beat.
    # Subsequent beats with the same handoff revision/missing state must NOT nudge!
    injected = []
    history = {}
    
    # 1st beat: Handoff missing -> nudge emitted, history updated
    t1 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=100, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None),
        history=history)
    assert t1["status"] == beat.SOFT_AUTHORING
    assert len(injected) == 1
    assert history["soft_reminders"]["cx-canary"] == "missing"

    # 2nd beat: Still missing -> nudge suppressed!
    t2 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=110, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (None, None),
        history=history)
    assert t2["status"] == beat.SOFT_AUTHORING
    assert len(injected) == 1  # Still 1, no new nudge!

    # 3rd beat: Now has an uncommitted/invalid handoff file (new revision) -> nudge emitted!
    invalid_handoff = {"current_goal": "partial"}  # Not ready
    t3 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=120, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (invalid_handoff, 120),
        history=history)
    assert t3["status"] == beat.SOFT_AUTHORING
    assert len(injected) == 2  # New nudge emitted!

    # 4th beat: Same invalid handoff -> nudge suppressed!
    t4 = beat.rotation_beat(
        _agent(75), REG, canary="cx-canary", now=130, session_turns=50,
        author_inject=lambda a, txt: injected.append((a, txt)),
        handoff_provider=lambda: (invalid_handoff, 120),
        history=history)
    assert t4["status"] == beat.SOFT_AUTHORING
    assert len(injected) == 2  # Still 2, no new nudge!


# --- hard_rotate: confirm_fn wired + hold ledger (S3/H9) ----------------------

def _step_names(rot):
    return [s["step"] for s in rot["steps"]]


def test_hard_rotate_confirmed_proceeds_to_retire():
    fake = FakeExecutors()
    t = beat.rotation_beat(_agent(95), REG, canary="cx-canary", now=0,
                           executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
                           confirm_fn=lambda c, s: {"outcome": "confirmed", "rounds": 0})
    assert t["status"] == ex.DONE
    assert "confirm_correct" in _step_names(t["rotation"])
    assert "retire" in fake.calls and "repin_canonical" in fake.calls
    assert t["ledger"]["holds"] == []           # no hold on a clean rotation


def test_hard_rotate_held_records_hold_in_ledger_no_retire():
    fake = FakeExecutors()
    t = beat.rotation_beat(
        _agent(95), REG, canary="cx-canary", now=1234,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "held", "rounds": 3,
                                 "reasons": ["comprehension:not-absorbed"]})
    assert t["status"] == ex.HOLD_UNCONFIRMED
    assert "retire" not in fake.calls           # predecessor NOT killed
    holds = t["ledger"]["holds"]
    assert len(holds) == 1
    assert holds[0]["canary"] == "cx-canary"
    assert holds[0]["status"] == ex.HOLD_UNCONFIRMED
    assert holds[0]["created_at"] == 1234


def test_hard_rotate_unsafe_recheck_records_hold():
    fake = FakeExecutors()
    t = beat.rotation_beat(
        _agent(95), REG, canary="cx-canary", now=7,
        executors_impl=fake, safety_fn=lambda c: ("PENDING_WORK", "own dirty work"),
        approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"})
    assert t["status"] == ex.HOLD_UNSAFE
    assert "retire" not in fake.calls
    assert len(t["ledger"]["holds"]) == 1       # every two-heads HOLD is ledgered


# --- hard_rotate: grade_fn forwarded verbatim to execute_rotation -------------

def test_hard_rotate_grade_pass_proceeds_to_retire():
    fake = FakeExecutors()
    t = beat.rotation_beat(
        _agent(95), REG, canary="cx-canary", now=0,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "PASS"})
    assert t["status"] == ex.DONE
    assert "auto_grade" in _step_names(t["rotation"])
    assert "retire" in fake.calls


def test_hard_rotate_grade_fail_holds_and_ledgers_no_retire():
    fake = FakeExecutors()
    t = beat.rotation_beat(
        _agent(95), REG, canary="cx-canary", now=99,
        executors_impl=fake, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s: {"disposition": "FAIL"})
    assert t["status"] == ex.HOLD_GRADE
    assert "retire" not in fake.calls           # predecessor NOT killed
    holds = t["ledger"]["holds"]
    assert len(holds) == 1                       # HOLD_GRADE is a two-heads hold -> ledgered
    assert holds[0]["status"] == ex.HOLD_GRADE


# --- two_sample_confirm (H5) --------------------------------------------------

_GT = {
    "goal": "wire the content gate",
    "guards": ["single-trunk", "never-kill-services"],
    "open_loops": ["reconcile-edges-abc123", "deep-loop-xyz789"],
    "hazards": ["shared-dirty-tree", "gm-mid-rotation"],
    "canary": [{"id": "q1", "question": "why?", "answer": "shawexception"}],
}
_EXPECTED = {"focus_id": "focus:ws3", "next_actions": ["wire"],
             "file_roots": ["scripts/lineage_daemon"]}
_EFFECT = {"kind": "session", "target": "cx-canary-g2"}


def _observed(**over):
    o = {"successor": "cx-canary-g2", "works_on": "focus:ws3", "oriented": True,
         "state": "working", "touched_files": ["scripts/lineage_daemon/x.py"],
         "confirmed_focus": "focus:ws3"}
    o.update(over)
    return o


def _deep_evidence():
    return {"readback": {"goal": "wire the content gate",
                         "guards": ["single-trunk", "never-kill-services"],
                         "open_loops": ["reconcile-edges-abc123", "deep-loop-xyz789"],
                         "hazards": ["shared-dirty-tree", "gm-mid-rotation"]},
            "canary_answers": {"q1": "shawexception"}}


def _shallow_evidence():
    return {"readback": {"goal": "x", "guards": [], "open_loops": [], "hazards": []},
            "canary_answers": {}}


def _effect_ok(*a, **k):
    return 0


def test_two_sample_both_pass_confirms():
    settled = {"n": 0}
    deep = {"observed": _observed(), "evidence": _deep_evidence()}
    cf = beat.two_sample_confirm(
        expected=_EXPECTED, ground_truth=_GT, first_effect=_EFFECT,
        read_successor=lambda: deep, gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: "x", inject_correction=lambda s, t, n: None,
        settle_fn=lambda: settled.__setitem__("n", settled["n"] + 1),
        effect_runner=_effect_ok)
    r = cf("cx-canary", "cx-canary-g2")
    assert r["outcome"] == "confirmed"
    assert r["sample"] == 2
    assert settled["n"] == 1            # the settle window ran between samples


def test_two_sample_sample1_held_short_circuits():
    """A shallow-read successor fails sample 1 -> held; sample 2 never runs."""
    settled = {"n": 0}
    shallow = {"observed": _observed(), "evidence": _shallow_evidence()}
    cf = beat.two_sample_confirm(
        expected=_EXPECTED, ground_truth=_GT, first_effect=_EFFECT,
        read_successor=lambda: shallow, gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: f"cite [{n}]",
        inject_correction=lambda s, t, n: None, max_rounds=2,
        settle_fn=lambda: settled.__setitem__("n", settled["n"] + 1),
        effect_runner=_effect_ok)
    r = cf("cx-canary", "cx-canary-g2")
    assert r["outcome"] == "held"
    assert r["sample"] == 1
    assert settled["n"] == 0            # never settled — held before the second sample


def test_two_sample_drift_at_sample2_is_held():
    """Passes sample 1 (deep) but DRIFTS by sample 2 (shallow) -> held. This is the
    H5 core: a successor that passed at T and drifted at T+settle is caught before
    the predecessor (the safety net) is destroyed."""
    reads = iter([
        {"observed": _observed(), "evidence": _deep_evidence()},      # sample 1: pass
        {"observed": _observed(), "evidence": _shallow_evidence()},   # sample 2: drift
    ])
    cf = beat.two_sample_confirm(
        expected=_EXPECTED, ground_truth=_GT, first_effect=_EFFECT,
        read_successor=lambda: next(reads), gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: "x", inject_correction=lambda s, t, n: None,
        settle_fn=lambda: None, effect_runner=_effect_ok)
    r = cf("cx-canary", "cx-canary-g2")
    assert r["outcome"] == "held"
    assert r["sample"] == 2
    assert any("two-sample:drift" in x for x in r["reasons"])
