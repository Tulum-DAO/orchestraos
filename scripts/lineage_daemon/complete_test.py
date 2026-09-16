"""Tests for complete.py — the multi-beat COMPLETION path (DEC-1787789209).

`complete_held_rotation` runs the POST-spawn steps on an ALREADY-live successor:
S3 re-confirm -> strict grade -> KILL GATE 1 -> KILL GATE 2 (graduation) -> CAP ->
PROMOTE -> RETIRE -> resolve. It NEVER spawns. Every seam is a FAKE so these tests
exercise the full ordering + fail-closed gates WITHOUT shelling out, promoting, or
killing. All safety invariants (D1/D4/D6/D8/D9/D10 + round-11 TOCTOU) are asserted
here in code.
"""
from scripts.lineage_daemon import complete as C


# --- fixtures -----------------------------------------------------------------

# L = the live successor session id captured at ENTRY; P = the provenance sid
# stamped into <succ>.comprehension.json (== L on a coherent artifact).
L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"

HOLD = {"canary": "pm-x", "successor": "pm-x-g2", "status": "hold:successor-unconfirmed",
        "reason": "same-beat", "created_at": 1000, "resolved": False}


def _seams(**over):
    """Default HAPPY-PATH seams for a first-entry (all-predecessor stores) proceed.
    Each test overrides the one seam it exercises."""
    d = dict(
        now=5000,
        live_sid_fn=lambda a: L_SID,                 # successor live, sid L
        l_start_time_fn=lambda sid: 1500.0,          # L.start_time (after hold-created)
        readback_mtime_fn=lambda a: 2000.0,          # readback committed AFTER hold + L.start
        comprehension_mtime_fn=lambda a: 2100.0,     # produced by the grade (> hold-created)
        provenance_sid_fn=lambda a: None,            # first entry: no comprehension.json yet
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),  # all-predecessor
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS", "strict": True,
                                    "artifact_written": True},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, alias: {"ok": True},
        retire_fn=lambda c: {"retired": True},
        armed=True,
        budget={"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True},
    )
    d.update(over)
    return d


def _run(**over):
    return C.complete_held_rotation("pm-x", "pm-x-g2", HOLD, **_seams(**over))


# ======================================================================
# HAPPY PATH — promote-then-retire, one-shot, budget consumed
# ======================================================================

def test_full_completion_promotes_then_retires():
    tr = _run()
    assert tr["status"] == C.COMPLETED
    assert tr["promoted"] is True and tr["retired"] is True
    assert tr["budget_consumed"] is True
    # D5: promote strictly BEFORE retire
    assert tr["steps"].index("promote") < tr["steps"].index("retire")
    # NEVER spawns
    assert "spawn" not in tr["steps"]


def test_completion_never_promotes_twice_on_reentry_all_successor():
    """D8: a re-entry whose 3 stores ALL resolve to the successor provenance sid
    runs the RESIDUAL path (retire+resolve only) and NEVER re-promotes."""
    promotes = []
    tr = _run(
        provenance_sid_fn=lambda a: L_SID,                       # artifact exists, P==L
        canonical_store_sids_fn=lambda c: (L_SID, L_SID, L_SID),  # promote already complete
        promote_fn=lambda c, alias: promotes.append(1) or {"ok": True},
    )
    assert tr["status"] == C.RESIDUAL_RETIRED
    assert promotes == []                       # promote_successor NOT re-called
    assert tr["promoted"] is False and tr["retired"] is True


# ======================================================================
# D8 — idempotency tri-state
# ======================================================================

def test_d8_torn_stores_fail_closed_hold_no_retire_no_promote():
    """MIXED stores (registry->successor, others->predecessor) = torn promote ->
    FAIL-CLOSED HOLD-escalate: neither retire nor re-promote."""
    promotes, retires = [], []
    tr = _run(
        provenance_sid_fn=lambda a: L_SID,
        canonical_store_sids_fn=lambda c: (L_SID, PRED_SID, PRED_SID),   # TORN
        promote_fn=lambda c, alias: promotes.append(1) or {"ok": True},
        retire_fn=lambda c: retires.append(1) or {"retired": True},
    )
    assert tr["status"] == C.HOLD_TORN
    assert promotes == [] and retires == []
    assert tr["promoted"] is False and tr["retired"] is False


def test_d8_missing_comprehension_but_store_resolves_to_live_holds():
    """Round-10: comprehension.json MISSING (no provenance sid) but a store already
    resolves canonical->live L -> a promote already happened -> FAIL-CLOSED HOLD
    (a blind proceed would re-run promote_successor + mis-archive the successor)."""
    promotes = []
    tr = _run(
        provenance_sid_fn=lambda a: None,                         # no artifact
        canonical_store_sids_fn=lambda c: (L_SID, PRED_SID, PRED_SID),  # a store == L
        promote_fn=lambda c, alias: promotes.append(1) or {"ok": True},
    )
    assert tr["status"] == C.HOLD_LIVE_MISMATCH
    assert promotes == []


def test_d8_live_sid_not_provenance_sid_holds():
    """Round-9: comprehension.json exists but the currently-live sid != the stamped
    provenance sid (a killed+name-reused pane between beats) -> HOLD-escalate."""
    tr = _run(
        provenance_sid_fn=lambda a: "sess-OLD-dead",     # artifact authored by a dead sid
        live_sid_fn=lambda a: L_SID,                     # live pane is a different sid
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
    )
    assert tr["status"] == C.HOLD_LIVE_MISMATCH


def test_d8_residual_conditional_recount_absent_promoted_at():
    """D10/round-7: residual retire re-consumes ONE budget unit IFF the hold row
    LACKS promoted_at (the promote-beat ledger was lost in a crash)."""
    hold = {**HOLD}                                      # no promoted_at
    tr = C.complete_held_rotation(
        "pm-x", "pm-x-g2", hold,
        **_seams(provenance_sid_fn=lambda a: L_SID,
                 canonical_store_sids_fn=lambda c: (L_SID, L_SID, L_SID)))
    assert tr["status"] == C.RESIDUAL_RETIRED
    assert tr["recount"] is True and tr["budget_consumed"] is True


def test_d8_residual_no_recount_when_promoted_at_present():
    """The swap was already counted (promoted_at present) -> residual re-counts ZERO."""
    hold = {**HOLD, "promoted_at": 4000}
    tr = C.complete_held_rotation(
        "pm-x", "pm-x-g2", hold,
        **_seams(provenance_sid_fn=lambda a: L_SID,
                 canonical_store_sids_fn=lambda c: (L_SID, L_SID, L_SID)))
    assert tr["status"] == C.RESIDUAL_RETIRED
    assert tr["recount"] is False and tr["budget_consumed"] is False


def test_d8_residual_requires_armed_and_kill_gate_1():
    """Round-6: the residual retire is a REAL kill -> fires ONLY IF still armed AND a
    fresh KILL GATE 1 passes. Disarmed OR attached/dirty -> FAIL-CLOSED HOLD."""
    retires = []
    # disarmed since the promote
    tr = C.complete_held_rotation(
        "pm-x", "pm-x-g2", HOLD,
        **_seams(provenance_sid_fn=lambda a: L_SID,
                 canonical_store_sids_fn=lambda c: (L_SID, L_SID, L_SID),
                 armed=False,
                 retire_fn=lambda c: retires.append(1) or {"retired": True}))
    assert tr["status"] == C.HOLD_RESIDUAL_UNSAFE
    assert retires == []
    # a human attached to the predecessor -> KILL GATE 1 fails -> HOLD
    tr2 = C.complete_held_rotation(
        "pm-x", "pm-x-g2", HOLD,
        **_seams(provenance_sid_fn=lambda a: L_SID,
                 canonical_store_sids_fn=lambda c: (L_SID, L_SID, L_SID),
                 safety_fn=lambda c: ("ATTACHED", "human debugging"),
                 retire_fn=lambda c: retires.append(1) or {"retired": True}))
    assert tr2["status"] == C.HOLD_RESIDUAL_UNSAFE
    assert retires == []


# ======================================================================
# D1 — completion_ready predicate (fail-closed)
# ======================================================================

def _ready(**over):
    base = dict(now=5000, live_sid_fn=lambda a: L_SID, l_start_time_fn=lambda sid: 1500.0,
                readback_mtime_fn=lambda a: 2000.0, provenance_sid_fn=lambda a: None,
                clock_skew_s=2.0)
    base.update(over)
    return C.completion_ready("pm-x", "pm-x-g2", HOLD, **base)


def test_completion_ready_happy():
    assert _ready() is True


def test_completion_ready_false_when_not_live():
    assert _ready(live_sid_fn=lambda a: None) is False


def test_completion_ready_false_readback_before_hold_created():
    # readback mtime <= hold-created (900 < created_at 1000)
    assert _ready(readback_mtime_fn=lambda a: 900.0) is False


def test_completion_ready_false_readback_before_L_start_time():
    """Round-9: readback predates the live session start (a dead-pane readback the
    reused pane rides) -> NOT ready."""
    assert _ready(readback_mtime_fn=lambda a: 1450.0,
                  l_start_time_fn=lambda sid: 1500.0) is False


def test_completion_ready_false_when_transcript_absent():
    """Round-10: L.start_time unresolved (no transcript) -> fail-closed NOT ready."""
    assert _ready(l_start_time_fn=lambda sid: None) is False


def test_completion_ready_false_provenance_sid_mismatch():
    """D9: an EXISTING comprehension.json whose provenance sid != the live sid."""
    assert _ready(provenance_sid_fn=lambda a: "sess-OTHER") is False


def test_completion_ready_does_not_require_comprehension_json():
    """Round-8 liveness: first entry (comprehension.json absent) is still READY on a
    fresh readback — else the gate deadlocks on an artifact only Step 3 produces."""
    assert _ready(provenance_sid_fn=lambda a: None) is True


# ======================================================================
# D6 — grade-check on the pre-promote ALIAS, strictly BEFORE promote
# ======================================================================

def test_d6_graduation_evaluated_on_alias_before_promote():
    seen = {}
    order = []

    def graduation(alias):
        seen["alias"] = alias
        order.append("graduation")
        return True

    def promote(c, alias):
        order.append("promote")
        return {"ok": True}

    tr = _run(graduation_fn=graduation, promote_fn=promote)
    assert tr["status"] == C.COMPLETED
    assert seen["alias"] == "pm-x-g2"                  # the pre-promote alias name
    assert order == ["graduation", "promote"]          # graduation strictly BEFORE promote


def test_d6_not_graduated_holds_before_promote():
    promotes = []
    tr = _run(graduation_fn=lambda alias: False,
              promote_fn=lambda c, alias: promotes.append(1) or {"ok": True})
    assert tr["status"] == C.HOLD_NOT_GRADUATED
    assert promotes == []
    assert tr["promoted"] is False and tr["retired"] is False


# ======================================================================
# D10 — CAP at the PROMOTE point
# ======================================================================

def test_d10_cap_exhausted_holds_before_promote():
    promotes, retires = [], []
    tr = _run(budget={"per_beat_slot": True, "hourly_remaining": 0,
                      "cooldown_clear": True},
              promote_fn=lambda c, alias: promotes.append(1) or {"ok": True},
              retire_fn=lambda c: retires.append(1) or {"retired": True})
    assert tr["status"] == C.HOLD_CAP
    assert promotes == [] and retires == []            # neither promote nor retire


def test_d10_no_per_beat_slot_holds_before_promote():
    promotes = []
    tr = _run(budget={"per_beat_slot": False, "hourly_remaining": 4,
                      "cooldown_clear": True},
              promote_fn=lambda c, alias: promotes.append(1) or {"ok": True})
    assert tr["status"] == C.HOLD_CAP
    assert promotes == []


# ======================================================================
# Gates that HOLD (S3 / grade / safety) — both live, nothing retired
# ======================================================================

def test_s3_unconfirmed_holds():
    tr = _run(confirm_fn=lambda c, s: {"outcome": "held"})
    assert tr["status"] == C.HOLD_UNCONFIRMED
    assert tr["retired"] is False and tr["promoted"] is False


def test_grade_fail_holds_before_kill_gates():
    tr = _run(grade_fn=lambda c, s, sid: {"disposition": "FAIL"})
    assert tr["status"] == C.HOLD_GRADE
    assert tr["promoted"] is False


def test_safety_recheck_fail_holds():
    tr = _run(safety_fn=lambda c: ("PENDING_WORK", "uncommitted work"))
    assert tr["status"] == C.HOLD_UNSAFE
    assert tr["promoted"] is False


# ======================================================================
# round-11 TOCTOU — capture L at entry, re-assert at point-of-use
# ======================================================================

def test_toctou_respawn_between_confirm_and_grade_holds():
    """The successor crashes+respawns (L -> L2) after entry; the pre-GRADE step
    re-asserts live-sid == entry-captured L and HOLDS on mismatch.
    live_sid_fn call order: #1 entry-capture, #2 pre-grade, #3 pre-promote."""
    calls = {"n": 0}

    def live_sid(alias):
        calls["n"] += 1
        return L_SID if calls["n"] <= 1 else "sess-RESPAWNED-L2"  # entry=L, pre-grade=L2

    tr = _run(live_sid_fn=live_sid)
    assert tr["status"] == C.HOLD_TOCTOU
    assert tr["promoted"] is False


def test_toctou_respawn_before_promote_holds():
    """A respawn AFTER the grade but BEFORE promote is caught by the Step-6 re-assert
    LIVE(A)==L -> HOLD, never promoting a respawned empty pane.
    live_sid_fn: #1 entry=L, #2 pre-grade=L, #3 pre-promote=L2."""
    calls = {"n": 0}

    def live_sid(alias):
        calls["n"] += 1
        return L_SID if calls["n"] <= 2 else "sess-RESPAWNED-late"

    tr = _run(live_sid_fn=live_sid)
    assert tr["status"] == C.HOLD_TOCTOU
    assert tr["promoted"] is False


def test_grade_receives_entry_captured_L_sid():
    """Step 3 passes EXACTLY the entry-captured L to the grader (never a fresh
    re-resolve that could stamp a respawned empty pane onto the prior readback)."""
    graded_with = {}

    def grade(c, s, sid):
        graded_with["sid"] = sid
        return {"disposition": "PASS", "strict": True, "artifact_written": True}

    tr = _run(grade_fn=grade)
    assert tr["status"] == C.COMPLETED
    assert graded_with["sid"] == L_SID
