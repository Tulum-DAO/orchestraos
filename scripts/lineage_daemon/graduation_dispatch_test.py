"""Stage C trigger-wiring (task #13) — the completion dispatcher.

Ties the substrate (graduation_retire) to LIVE state + surfaces, but stays PURE
over injected readers/seams so it is hermetically testable. The Stop-hook entry
and the daemon backstop are thin live callers of dispatch_graduation().

Flow: build ctx+seat from injected readers -> plan_graduation_retire ->
  await_card  -> emit via create_fn (+notify_fn), return the row id (NO retire here)
  proceed_no_card -> run execute_graduation_retire directly (graduated mode)
  keep        -> no-op, return the reason.

ARMING gate: dispatch takes armed=False by default -> a DRY dispatch computes the
plan + would-emit/would-execute WITHOUT touching ApprovalStore/promote/park-idle.
armed=True is only ever passed by the separately-the operator-gated live caller.
"""
from scripts.lineage_daemon import graduation_dispatch as D


def _readers(*, grade="PASS", beats=2, succ_live=True, handoff=True, duty=None,
             succ="x-3"):
    """Injected live-state readers for one predecessor `x-gen2`."""
    return {
        "successor_of": lambda pred: succ,
        "grade_of": lambda successor: grade,          # "PASS"/"FAIL"/None
        "beats_since_promotion_of": lambda pred: beats,
        "successor_live": lambda successor: succ_live,
        "handoff_confirmed_of": lambda successor: handoff,
        "pending_duty_of": lambda pred: duty,
        "seat_meta_of": lambda pred: {"lineage_root": "x", "generation": 2,
                                      "grade_commit": "c"},
    }


# ---- ctx building: grade string -> may_retire's grade_agree tri-state ---------

def test_grade_pass_maps_to_true():
    ctx, _ = D.build_ctx_seat("x-gen2", _readers(grade="PASS"))
    assert ctx["grade_agree"] is True


def test_grade_fail_maps_to_false():
    ctx, _ = D.build_ctx_seat("x-gen2", _readers(grade="FAIL"))
    assert ctx["grade_agree"] is False


def test_grade_pending_maps_to_none():
    ctx, _ = D.build_ctx_seat("x-gen2", _readers(grade=None))
    assert ctx["grade_agree"] is None


def test_seat_carries_pred_succ_lineage():
    _, seat = D.build_ctx_seat("x-gen2", _readers())
    assert seat["agent_id"] == "x-gen2"
    assert seat["successor"] == "x-3"
    assert seat["lineage_root"] == "x"
    assert seat["generation"] == 2


# ---- dispatch: KEEP when not retire-ready -------------------------------------

def test_dispatch_keep_on_pending_grade():
    out = D.dispatch_graduation("x-gen2", _readers(grade=None), disabled_path="/none")
    assert out["action"] == "keep"
    assert out["emitted"] is None


# ---- dispatch: await_card path emits (armed) / would-emit (dry) ---------------

def test_dispatch_dry_would_emit_no_side_effect():
    emitted = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=False,
        create_fn=lambda **k: emitted.append(k) or "row1",
        notify_fn=lambda rid: emitted.append(("notify", rid)))
    assert out["action"] == "await_card"
    assert out["emitted"] == {"would_emit": True}
    assert emitted == []                       # dry: NOTHING created/notified


def test_dispatch_armed_emits_card_and_notifies():
    created = {}
    notified = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        create_fn=lambda **k: created.update(k) or "row-abc",
        notify_fn=lambda rid: notified.append(rid))
    assert out["action"] == "await_card"
    assert out["emitted"]["row_id"] == "row-abc"
    assert created["kind"] == "promote_graded"
    assert created["op_key"] == "promote_graded:x:2"
    assert notified == ["row-abc"]


def test_dispatch_armed_emit_failsafe_records_no_raise():
    def _boom(**k):
        raise RuntimeError("store down")
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        create_fn=_boom, notify_fn=lambda rid: None)
    assert out["action"] == "await_card"
    assert out["emitted"]["row_id"] is None
    assert "store down" in out["emitted"]["error"]


# ---- dispatch: proceed_no_card path (graduated mode) runs execute -------------

def test_dispatch_graduated_skip_runs_execute_armed():
    calls = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: calls.append("gate1") or (True, "ok"),
        promote_fn=lambda seat: calls.append("promote") or {},
        retire_fn=lambda seat: calls.append("retire") or {})
    assert out["action"] == "proceed_no_card"
    assert calls == ["gate1", "promote", "retire"]     # KILL GATE 1 first
    assert out["executed"]["action"] == "promoted_and_retired"


def test_dispatch_graduated_skip_dry_does_not_execute():
    calls = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=False,
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: calls.append("gate1") or (True, "ok"),
        promote_fn=lambda seat: calls.append("promote"),
        retire_fn=lambda seat: calls.append("retire"))
    assert out["action"] == "proceed_no_card"
    assert calls == []                                 # dry: nothing executed
    assert out["executed"] == {"would_execute": True}


def test_dispatch_never_raises_on_reader_failure():
    # gen20 finding: build_ctx_seat/plan (may_retire) must NOT propagate a reader
    # raise OUT of dispatch — else the daemon backstop caller crashes the beat.
    # A reader failure -> safe "keep" trace (fail toward not-acting), never raised.
    bad = _readers()
    def _boom(pred):
        raise RuntimeError("live reader exploded")
    bad["successor_of"] = _boom
    out = D.dispatch_graduation("x-gen2", bad, disabled_path="/none")
    assert out["action"] == "keep"
    assert "error" in out["reason"].lower()
    assert out["emitted"] is None and out["executed"] is None


def test_dispatch_graduated_but_kill_switch_forces_card(tmp_path):
    sentinel = tmp_path / "SELF_RETIRE_DISABLED"
    sentinel.write_text("x")
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path=str(sentinel), armed=True,
        graduated_fn=lambda s: True,
        create_fn=lambda **k: "row-z", notify_fn=lambda rid: None)
    assert out["action"] == "await_card"               # brake forces the tap


# ---- D2 (gen20 wire): notify-only ping AFTER a graduated (card-skipped) retire -
# the operator Q2: the per-retire TAP is REPLACED by a notify-ONLY-AFTER. Contract:
#   proceed_no_card + promoted_and_retired -> exactly ONE ping;
#   aborted_kill_gate1 -> ZERO (retire didn't happen -> a ping would be false);
#   carded (await_card) path -> ZERO (that path taps the operator, never auto-skips);
#   dry (armed=False) -> ZERO (no retire happened).

def test_dispatch_notifies_after_graduated_retire():
    pings = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        skip_notify_fn=lambda msg: pings.append(msg), used_pct=87,
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: (True, "ok"),
        promote_fn=lambda seat: {}, retire_fn=lambda seat: {})
    assert out["action"] == "proceed_no_card"
    assert out["executed"]["action"] == "promoted_and_retired"
    assert len(pings) == 1                              # exactly ONE ping
    assert "x-gen2" in pings[0] and "87" in pings[0]    # names the seat + ctx%


def test_dispatch_no_notify_on_kill_gate1_abort():
    pings = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        skip_notify_fn=lambda msg: pings.append(msg), used_pct=87,
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: (False, "stale"),   # KILL GATE 1 aborts
        promote_fn=lambda seat: {}, retire_fn=lambda seat: {})
    assert out["executed"]["action"] == "aborted_kill_gate1"
    assert pings == []                                 # NO ping: no retire happened


def test_dispatch_no_notify_on_carded_path():
    pings = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        skip_notify_fn=lambda msg: pings.append(msg),
        graduated_fn=lambda s: False,                  # not graduated -> card
        create_fn=lambda **k: "row-1", notify_fn=lambda rid: None)
    assert out["action"] == "await_card"
    assert pings == []                                 # carded path never D2-notifies


def test_dispatch_no_notify_when_dry():
    pings = []
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=False,
        skip_notify_fn=lambda msg: pings.append(msg),
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: (True, "ok"),
        promote_fn=lambda seat: {}, retire_fn=lambda seat: {})
    assert out["executed"] == {"would_execute": True}
    assert pings == []                                 # dry: no retire, no ping


def test_dispatch_notify_failure_never_aborts_retire():
    # D2 is notify-only: a send that raises must NOT bubble out / abort the retire.
    def _boom(msg):
        raise RuntimeError("telegram down")
    out = D.dispatch_graduation(
        "x-gen2", _readers(), disabled_path="/none", armed=True,
        skip_notify_fn=_boom, used_pct=50,
        graduated_fn=lambda s: True,
        kill_gate1_fn=lambda seat: (True, "ok"),
        promote_fn=lambda seat: {}, retire_fn=lambda seat: {})
    assert out["executed"]["action"] == "promoted_and_retired"   # retire still happened
