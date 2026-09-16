"""D3 beat wiring for the multi-beat COMPLETION path (DEC-1787789209).

plan_fleet gains ONE injectable seam `completion_provider(canary, hold_row, *, armed,
budget, now) -> trace`. Default None => the completion branch is INERT (a held agent
ages exactly as today via SKIP_OPEN_HOLD — the 631 baseline is unchanged). When a
provider is wired AND the lineage is armed (soft_only False + in the allowlist), the
completion branch runs BEFORE the not-actionable short-circuit + before SKIP_OPEN_HOLD,
draws from the SAME per-beat + hourly budget as spawns (D10), and applies the ledger
effects (mark_promoted after promote, resolve_hold after retire).
"""
import pytest

from scripts.lineage_daemon import fleet
from scripts.lineage_daemon import execute as ex
from scripts.lineage_daemon import hold_ledger as hledger
from scripts.lineage_daemon import complete as C


@pytest.fixture(autouse=True)
def _isolate_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "orch"))
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "runtime"))


REG = {"agents": {
    "t2-hot": {"generation": 1, "tier": "T2", "lineage_root": "t2-hot"},
    "t2-calm": {"generation": 1, "tier": "T2", "lineage_root": "t2-calm"},
}}
SAFE = lambda c: ("SUPERSEDED_SAFE", "ok")
APPROVE = lambda c, s: "approve"
CONFIRM = lambda c, s: {"outcome": "confirmed"}


class _ArmedAny(frozenset):
    def __contains__(self, item):
        return True


ARMED_ANY = _ArmedAny()


def _agent(aid, pct, tier="T2"):
    return {"agent_id": aid, "tier_class": tier, "death": {},
            "ctx": {"status_bar_pct": pct}}


def _held_ledger(canary="t2-hot", successor="t2-hot-g2", **extra):
    row = {"canary": canary, "successor": successor,
           "status": "hold:successor-unconfirmed", "reason": "same-beat",
           "created_at": 0, "resolved": False}
    row.update(extra)
    return {"holds": [row]}


def _provider(trace):
    """A fake completion provider recording every call; returns `trace` verbatim
    (with canary/successor filled in)."""
    calls = []

    def prov(canary, hold, *, armed, budget, now):
        calls.append({"canary": canary, "armed": armed, "budget": dict(budget)})
        return {**trace, "canary": canary, "successor": hold["successor"]}

    prov.calls = calls
    return prov


_COMPLETED = {"status": C.COMPLETED, "promoted": True, "retired": True,
              "budget_consumed": True, "recount": False, "steps": []}
_RESIDUAL = {"status": C.RESIDUAL_RETIRED, "promoted": False, "retired": True,
             "budget_consumed": True, "recount": True, "steps": []}
_HOLD = {"status": C.HOLD_NOT_READY, "promoted": False, "retired": False,
         "budget_consumed": False, "recount": False, "steps": []}


# --- INERT: no provider => a held agent behaves exactly as today ---------------

def test_completion_provider_none_is_inert(tmp_path):
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=1, armed_tiers={"T2"},
        ledger=_held_ledger(), armed_lineages=ARMED_ANY, orchestra_dir=str(tmp_path),
        safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD
    assert "completion" not in out["beats"][0]


# --- COMPLETED: promote+retire -> hold resolved + marked + budget consumed ------

def test_completion_completed_resolves_and_marks_hold(tmp_path):
    prov = _provider(_COMPLETED)
    led = _held_ledger()
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=7, armed_tiers={"T2"}, ledger=led,
        armed_lineages=ARMED_ANY, orchestra_dir=str(tmp_path),
        completion_provider=prov, safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=CONFIRM)
    assert out["beats"][0]["armed_status"] == C.COMPLETED
    # the provider was invoked, armed=True
    assert prov.calls and prov.calls[0]["armed"] is True
    # the hold is RESOLVED (retire completed) and stamped promoted_at (mark_promoted)
    assert hledger.open_holds(out["ledger"]) == []
    row = out["ledger"]["holds"][0]
    assert row["resolved"] is True and row.get("promoted_at") == 7
    # a retire was recorded (hourly cap accounting) + counted as a swap
    assert out["history"]["retires"] and out["summary"]["rotated"] == 1


# --- D10: completions + spawns share ONE per-beat budget -----------------------

def test_completion_consumes_shared_per_beat_slot_blocking_a_spawn(tmp_path):
    """max_rotations=1: a completion (t2-hot, held) consumes the single per-beat
    identity-swap slot, so a concurrent spawn candidate (t2-calm, HARD, no hold) is
    cap-deferred — a beat never exceeds the cap across spawns + completions."""
    prov = _provider(_COMPLETED)
    led = _held_ledger(canary="t2-hot", successor="t2-hot-g2")
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95), _agent("t2-calm", 99)], REG, now=0,
        armed_tiers={"T2"}, max_rotations=1, ledger=led, armed_lineages=ARMED_ANY,
        orchestra_dir=str(tmp_path), completion_provider=prov,
        safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    by = {b["agent_id"]: b for b in out["beats"]}
    assert by["t2-hot"]["armed_status"] == C.COMPLETED
    assert by["t2-calm"]["armed_status"] == fleet.SKIP_CAP_DEFERRED
    assert out["summary"]["rotated"] == 1          # exactly ONE identity swap this beat


# --- ARM GATE: unarmed / soft_only never even calls the provider ----------------

def test_completion_not_called_when_lineage_unarmed(tmp_path):
    prov = _provider(_COMPLETED)
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=1, armed_tiers={"T2"}, ledger=_held_ledger(),
        armed_lineages=frozenset({"someone-else"}), orchestra_dir=str(tmp_path),
        completion_provider=prov, safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    assert prov.calls == []                              # arm gate: never invoked
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD


def test_completion_not_called_when_soft_only(tmp_path):
    prov = _provider(_COMPLETED)
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=1, armed_tiers={"T2"}, ledger=_held_ledger(),
        armed_lineages=ARMED_ANY, soft_only=True, orchestra_dir=str(tmp_path),
        completion_provider=prov, safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    assert prov.calls == []
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD


# --- a HOLD outcome leaves the hold OPEN (ages as today) ------------------------

def test_completion_hold_keeps_hold_open(tmp_path):
    prov = _provider(_HOLD)
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=1, armed_tiers={"T2"}, ledger=_held_ledger(),
        armed_lineages=ARMED_ANY, orchestra_dir=str(tmp_path), completion_provider=prov,
        safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    assert out["beats"][0]["armed_status"] == C.HOLD_NOT_READY
    assert len(hledger.open_holds(out["ledger"])) == 1     # still open
    assert out["history"]["retires"] == []                 # no swap counted
    assert out["summary"]["held"] == 1


# --- RESIDUAL: retire-only finishes a prior-beat promote (conditional re-count) --

def test_completion_residual_resolves_and_recounts(tmp_path):
    prov = _provider(_RESIDUAL)
    out = fleet.plan_fleet(
        [_agent("t2-hot", 95)], REG, now=3, armed_tiers={"T2"}, ledger=_held_ledger(),
        armed_lineages=ARMED_ANY, orchestra_dir=str(tmp_path), completion_provider=prov,
        safety_fn=SAFE, approval_fn=APPROVE, confirm_fn=CONFIRM)
    assert out["beats"][0]["armed_status"] == C.RESIDUAL_RETIRED
    assert hledger.open_holds(out["ledger"]) == []         # resolved
    assert out["history"]["retires"]                       # re-counted (promoted_at absent)
    assert out["summary"]["rotated"] == 1


# --- A.3 rework: multi-beat completion flow through plan_fleet (retired != resolved) ---

def _rework_provider():
    """A fake completion provider driving the A.3-rework 3-beat flow by hold status."""
    def prov(canary, hold, *, armed, budget, now):
        base = {"canary": canary, "successor": hold["successor"], "steps": []}
        st = hold.get("status")
        if hold.get("promoted_at") is None:
            return {**base, "status": C.AWAITING_PROGRESS, "promoted": True,
                    "retired": False, "budget_consumed": True}
        if st == C.AWAITING_PROGRESS:                 # progressing-watch: retire (not resolve)
            return {**base, "status": C.RETIRED_AWAITING_EFFECT, "promoted": False,
                    "retired": True, "budget_consumed": False}
        return {**base, "status": C.COMPLETED, "promoted": False, "retired": False,
                "budget_consumed": False}             # effect-watch: resolve
    return prov


def test_rework_multibeat_retired_is_not_resolved_until_terminal():
    prov = _rework_provider()
    state = _held_ledger()
    common = dict(armed_tiers={"T2"}, armed_lineages=ARMED_ANY, completion_provider=prov)

    # Beat 1: promote -> AWAITING_PROGRESS. Hold OPEN, status advanced, rotated.
    o1 = fleet.plan_fleet([_agent("t2-hot", 95)], REG, now=100, ledger=state, **common)
    h1 = hledger.open_holds(o1["ledger"])
    assert len(h1) == 1 and h1[0]["status"] == C.AWAITING_PROGRESS
    assert h1[0].get("promoted_at") == 100
    assert o1["summary"]["rotated"] == 1

    # Beat 2: progressing-watch RETIRES the predecessor but the hold STAYS OPEN
    # (retired != resolved) and advances to the effect-watch status.
    o2 = fleet.plan_fleet([_agent("t2-hot", 95)], REG, now=200,
                          ledger=o1["ledger"], **common)
    h2 = hledger.open_holds(o2["ledger"])
    assert len(h2) == 1, "retire must NOT resolve the hold in the rework"
    assert h2[0]["status"] == C.RETIRED_AWAITING_EFFECT

    # Beat 3: effect-watch sees the effect -> COMPLETED -> hold RESOLVED.
    o3 = fleet.plan_fleet([_agent("t2-hot", 95)], REG, now=300,
                          ledger=o2["ledger"], **common)
    assert hledger.open_holds(o3["ledger"]) == []
    assert o3["beats"][0]["armed_status"] == C.COMPLETED
