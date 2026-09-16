"""Stage C — RED-first tests for the PRODUCTION live resolvers wired into the
graduation Stop-hook (graduation_stop_hook.main).

Two resolvers, both hermetic (every store path is an injected keyword arg defaulting
to the real live location), so these drive an isolated scratch world:

  resolve_pred(session_id) -> predecessor agent-id | None
      Maps a live session_id to a lineage predecessor that HAS a live graded
      successor (the retire candidate). None for the common case (not a lineage
      predecessor / no live-graded successor yet) so the hook no-ops.

  readers_for(pred) -> the 7-key readers dict graduation_dispatch.build_ctx_seat
      consumes: successor_of, seat_meta_of, beats_since_promotion_of,
      successor_live, handoff_confirmed_of, grade_of, pending_duty_of.

The HARD CONTRACT (main() always exits 0, never raises) is re-proven end-to-end at
the bottom by wiring these two live resolvers through main() with scratch stores.
"""
import json
import os

import pytest

from scripts.lineage_daemon import graduation_resolvers as R
from scripts.lineage_daemon import graduation_dispatch as GD
from scripts.lineage_daemon import graduation_stop_hook as H


# ---- fixtures: an isolated 3-store world -------------------------------------

@pytest.fixture
def world(tmp_path):
    """A scratch registry + agent-sessions + handoffs dir describing ONE lineage:
      pred `x-gen2` (session sid-pred) succeeded_by live-graded successor `x`.
    Returns a dict of the four store paths + a helper to rewrite each store."""
    registry = tmp_path / "registry.json"
    sessions = tmp_path / "agent-sessions.json"
    handoffs = tmp_path / "agent-handoffs"
    handoffs.mkdir()

    def write_registry(agents):
        registry.write_text(json.dumps({"agents": agents}))

    def write_sessions(d):
        sessions.write_text(json.dumps(d))

    def write_comprehension(successor, obj):
        (handoffs / f"{successor}.comprehension.json").write_text(json.dumps(obj))

    # default healthy world: pred is a lineage predecessor, successor live+graded.
    write_registry({
        "x-gen2": {"id": "x-gen2", "status": "quiescent", "generation": 2,
                   "lineage_root": "x", "tier": "T2",
                   "succeeded_by": "x", "session_id": "sid-pred"},
        "x": {"id": "x", "status": "online", "generation": 3,
              "lineage_root": "x", "tier": "T2", "predecessor": "x-gen2",
              "promoted_at": "2026-08-26T00:00:00+00:00",
              "beats_since_promotion": 2,
              "session_id": "sid-succ"},
    })
    write_sessions({
        "x-gen2": {"session_id": "sid-pred"},
        "x": {"session_id": "sid-succ"},
    })
    write_comprehension("x", {"successor": "x", "pass": True, "mode": "strict",
                              "verdict": "PASS", "score": "5/5"})

    return {
        "registry_path": str(registry),
        "sessions_path": str(sessions),
        "handoffs_dir": str(handoffs),
        "write_registry": write_registry,
        "write_sessions": write_sessions,
        "write_comprehension": write_comprehension,
        "paths": lambda: {"registry_path": str(registry),
                          "sessions_path": str(sessions),
                          "handoffs_dir": str(handoffs)},
    }


# ---- resolve_pred: the retire-candidate predecessor decision ------------------

def test_resolve_pred_maps_session_to_lineage_predecessor(world):
    pred = R.resolve_pred("sid-pred", **world["paths"]())
    assert pred == "x-gen2"


def test_resolve_pred_none_for_unknown_session(world):
    assert R.resolve_pred("sid-not-in-any-store", **world["paths"]()) is None


def test_resolve_pred_none_for_successor_session_not_a_predecessor(world):
    # The successor's own session is NOT a lineage predecessor (no succeeded_by).
    assert R.resolve_pred("sid-succ", **world["paths"]()) is None


def test_resolve_pred_none_when_no_succeeded_by(world):
    # A live agent with a session but NO successor is the common case -> noop.
    world["write_registry"]({
        "solo": {"id": "solo", "status": "online", "session_id": "sid-solo"},
    })
    world["write_sessions"]({"solo": {"session_id": "sid-solo"}})
    assert R.resolve_pred("sid-solo", **world["paths"]()) is None


def test_resolve_pred_none_when_successor_row_missing(world):
    # succeeded_by names a successor that isn't in the registry -> not a candidate.
    world["write_registry"]({
        "x-gen2": {"id": "x-gen2", "status": "quiescent",
                   "succeeded_by": "ghost", "session_id": "sid-pred"},
    })
    assert R.resolve_pred("sid-pred", **world["paths"]()) is None


def test_resolve_pred_none_when_successor_not_live(world):
    # Successor exists but is retired/killed -> not a live-graded candidate.
    world["write_registry"]({
        "x-gen2": {"id": "x-gen2", "status": "quiescent",
                   "succeeded_by": "x", "session_id": "sid-pred"},
        "x": {"id": "x", "status": "killed", "session_id": "sid-succ"},
    })
    assert R.resolve_pred("sid-pred", **world["paths"]()) is None


def test_resolve_pred_none_when_successor_ungraded(world):
    # Live successor but NO comprehension artifact yet -> not a retire candidate.
    os.remove(os.path.join(world["handoffs_dir"], "x.comprehension.json"))
    assert R.resolve_pred("sid-pred", **world["paths"]()) is None


def test_resolve_pred_none_on_unreadable_registry(world):
    assert R.resolve_pred("sid-pred", registry_path="/no/such/registry.json",
                          sessions_path=world["sessions_path"],
                          handoffs_dir=world["handoffs_dir"]) is None


def test_resolve_pred_never_raises_on_garbage_session(world):
    # Non-string / None session must not raise.
    assert R.resolve_pred(None, **world["paths"]()) is None
    assert R.resolve_pred(12345, **world["paths"]()) is None


# ---- readers_for: the 7-key dict build_ctx_seat consumes ----------------------

def test_readers_for_has_all_seven_keys(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    for k in ("successor_of", "seat_meta_of", "beats_since_promotion_of",
              "successor_live", "handoff_confirmed_of", "grade_of",
              "pending_duty_of"):
        assert k in rd and callable(rd[k]), f"missing/uncallable reader: {k}"


def test_readers_for_successor_of_reads_succeeded_by(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["successor_of"]("x-gen2") == "x"


def test_readers_for_seat_meta_carries_lineage_and_generation(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    meta = rd["seat_meta_of"]("x-gen2")
    assert meta["lineage_root"] == "x"
    assert meta["generation"] == 2


def test_readers_for_successor_live_true_for_online(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["successor_live"]("x") is True


def test_readers_for_successor_live_false_for_retired(world):
    world["write_registry"]({
        "x-gen2": {"id": "x-gen2", "succeeded_by": "x", "generation": 2,
                   "lineage_root": "x"},
        "x": {"id": "x", "status": "retired"},
    })
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["successor_live"]("x") is False


def test_readers_for_grade_of_pass_from_strict_comprehension(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["grade_of"]("x") == "PASS"


def test_readers_for_grade_of_none_when_no_artifact(world):
    os.remove(os.path.join(world["handoffs_dir"], "x.comprehension.json"))
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["grade_of"]("x") is None


def test_readers_for_grade_of_fail_on_failed_verdict(world):
    world["write_comprehension"]("x", {"successor": "x", "pass": False,
                                        "mode": "strict", "verdict": "FAIL"})
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["grade_of"]("x") == "FAIL"


def test_readers_for_grade_of_none_for_supervised_mode(world):
    # A supervised grade is NOT an unattended-consumable PASS -> None (no data).
    world["write_comprehension"]("x", {"successor": "x", "pass": True,
                                        "mode": "supervised", "verdict": "PASS"})
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["grade_of"]("x") is None


def test_readers_for_handoff_confirmed_true_when_comprehension_present(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["handoff_confirmed_of"]("x") is True


def test_readers_for_handoff_confirmed_false_when_absent(world):
    os.remove(os.path.join(world["handoffs_dir"], "x.comprehension.json"))
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert rd["handoff_confirmed_of"]("x") is False


def test_readers_for_beats_since_promotion_is_int_or_none(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    beats = rd["beats_since_promotion_of"]("x-gen2")
    assert beats is None or isinstance(beats, int)


def test_readers_for_pending_duty_defaults_empty(world):
    rd = R.readers_for("x-gen2", **world["paths"]())
    assert not rd["pending_duty_of"]("x-gen2")


def test_readers_for_readers_never_raise_on_missing_stores():
    # Every reader must degrade to a safe default, not raise, on unreadable stores.
    rd = R.readers_for("x-gen2", registry_path="/no/reg.json",
                       sessions_path="/no/sess.json", handoffs_dir="/no/dir")
    assert rd["successor_of"]("x-gen2") is None
    assert rd["seat_meta_of"]("x-gen2") == {} or isinstance(rd["seat_meta_of"]("x-gen2"), dict)
    assert rd["grade_of"]("x") is None
    assert rd["successor_live"]("x") is False
    assert rd["handoff_confirmed_of"]("x") is False
    assert not rd["pending_duty_of"]("x-gen2")


# ---- end-to-end through the Stop-hook (HARD CONTRACT: always exit 0) ----------

def test_e2e_main_dry_dispatch_on_graded_pass(world, capsys):
    rp = lambda sid: R.resolve_pred(sid, **world["paths"]())
    rf = lambda pred: R.readers_for(pred, **world["paths"]())
    rc = H.main(stdin_text=json.dumps({"session_id": "sid-pred"}),
                resolve_pred=rp, readers_for=rf)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "dispatched"
    assert out["pred"] == "x-gen2"
    # DRY by default (armed=False): plan computed, no live effect.
    assert out["dispatch"]["action"] == "await_card"
    assert out["dispatch"]["emitted"] == {"would_emit": True}


def test_e2e_main_noop_when_not_a_predecessor(world, capsys):
    rp = lambda sid: R.resolve_pred(sid, **world["paths"]())
    rf = lambda pred: R.readers_for(pred, **world["paths"]())
    rc = H.main(stdin_text=json.dumps({"session_id": "sid-succ"}),
                resolve_pred=rp, readers_for=rf)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "noop:not-a-lineage-predecessor"


def test_e2e_main_exits_zero_even_on_reader_explosion(world, capsys):
    rp = lambda sid: R.resolve_pred(sid, **world["paths"]())

    def _boom(pred):
        raise RuntimeError("live reader exploded")
    rc = H.main(stdin_text=json.dumps({"session_id": "sid-pred"}),
                resolve_pred=rp, readers_for=_boom)
    assert rc == 0                                   # never wedge a turn
    out = json.loads(capsys.readouterr().out)
    assert out["status"].startswith("error")


def test_live_resolvers_wired_helper_returns_callables():
    # The convenience wiring used by production main() returns two callables that
    # default to the real live stores (smoke: shape only, no live side effects).
    rp, rf = R.live_resolvers()
    assert callable(rp) and callable(rf)
