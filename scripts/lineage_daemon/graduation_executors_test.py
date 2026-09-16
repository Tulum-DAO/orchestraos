"""RED-first tests for graduation_executors — the REAL executor adapters wired
into the graduation Stop-hook dispatch (task #6).

These tests NEVER let a real promote/retire/spawn/card touch live state. Every
real target (default_safety_recheck, promote_successor.promote, park-idle.retire,
ApprovalStore.create, approval_notify.notify, msg_store) is monkeypatched to a
recorder. They prove:
  * each adapter maps the graduation `seat` dict to the real function's signature
    and returns the shape dispatch_graduation/execute_graduation_retire expect;
  * kill_gate1_fn returns (ok:bool, reason:str), True ONLY on SUPERSEDED_SAFE;
  * wired into an ARMED strict-graduated dispatch: kill_gate1 -> promote -> retire
    run IN ORDER + skip_notify fires exactly once;
  * supervised (card) path: create_fn emits the card, retire NEVER runs;
  * kill_gate1 (False,..) aborts: nothing promoted/retired, no skip-notify.
"""
import types

import pytest

from scripts.lineage_daemon import graduation_dispatch as D
from scripts.lineage_daemon import graduation_executors as GE


def _seat(**over):
    seat = {
        "agent_id": "gm-gen2", "successor": "gm-gen3",
        "lineage_root": "gm", "generation": 2, "grade_commit": "abc",
        "beats_since_promotion": 3,
    }
    seat.update(over)
    return seat


# ---- kill_gate1_fn: (category,reason) -> (ok:bool, reason:str) -----------------

def test_kill_gate1_true_only_on_superseded_safe(monkeypatch):
    seen = {}
    def _recheck(canary, orchestra_dir=None):
        seen["canary"] = canary
        return ("SUPERSEDED_SAFE", "superseded -> live head gm-gen3")
    monkeypatch.setattr(GE, "_safety_recheck", _recheck)
    ex = GE.live_dispatch_executors()
    ok, reason = ex["kill_gate1_fn"](_seat())
    assert ok is True
    assert isinstance(reason, str) and "superseded" in reason
    assert seen["canary"] == "gm-gen2"          # maps seat.agent_id -> canary


def test_kill_gate1_false_on_non_superseded(monkeypatch):
    monkeypatch.setattr(GE, "_safety_recheck",
                        lambda c, orchestra_dir=None: ("PENDING_WORK", "typed text"))
    ex = GE.live_dispatch_executors()
    ok, reason = ex["kill_gate1_fn"](_seat())
    assert ok is False and "typed text" in reason


def test_kill_gate1_fails_closed_on_recheck_error(monkeypatch):
    def _boom(c, orchestra_dir=None):
        raise RuntimeError("classify exploded")
    monkeypatch.setattr(GE, "_safety_recheck", _boom)
    ex = GE.live_dispatch_executors()
    ok, reason = ex["kill_gate1_fn"](_seat())
    assert ok is False                          # fail CLOSED: any error -> hold
    assert "classify exploded" in reason


def test_kill_gate1_returns_bool_not_truthy(monkeypatch):
    # Contract: ok MUST be a real bool (execute_graduation_retire does `if not ok`).
    monkeypatch.setattr(GE, "_safety_recheck",
                        lambda c, orchestra_dir=None: ("SUPERSEDED_SAFE", "ok"))
    ex = GE.live_dispatch_executors()
    ok, _ = ex["kill_gate1_fn"](_seat())
    assert ok is True and isinstance(ok, bool)


# ---- promote_fn: seat -> promote_successor.promote(...) -----------------------

def test_promote_fn_maps_seat_to_promote_signature(monkeypatch):
    captured = {}
    def _promote(canonical_id, successor_session, **kw):
        captured["canonical_id"] = canonical_id
        captured["successor_session"] = successor_session
        captured["kw"] = kw
        return {"canonical_id": canonical_id, "written": ["registry.json"]}
    monkeypatch.setattr(GE, "_promote", _promote)
    ex = GE.live_dispatch_executors()
    out = ex["promote_fn"](_seat())
    assert captured["canonical_id"] == "gm"          # lineage_root = canonical name
    assert captured["successor_session"] == "gm-gen3"
    assert captured["kw"].get("generation") == 2     # threaded from seat
    assert out["written"] == ["registry.json"]       # returns promote's report dict


def test_promote_fn_falls_back_to_agent_id_when_no_lineage_root(monkeypatch):
    captured = {}
    monkeypatch.setattr(GE, "_promote",
                        lambda canonical_id, successor_session, **kw:
                        captured.update(canonical_id=canonical_id) or {})
    ex = GE.live_dispatch_executors()
    ex["promote_fn"](_seat(lineage_root=None))
    assert captured["canonical_id"] == "gm-gen2"     # falls back to agent_id


# ---- retire_fn: seat -> park-idle.retire(name, reg, meta, roster, reason) ------

def test_retire_fn_maps_seat_to_park_idle_retire(monkeypatch):
    calls = {}
    fake_pk = types.SimpleNamespace(
        REGISTRY="/r", AGENT_SESSIONS="/s", LIVE_ROSTER="/l",
        _load=lambda p: {"loaded": p},
        live_sessions=lambda: {},
        retire=lambda name, reg, meta, roster, reason:
            calls.update(name=name, reg=reg, meta=meta, roster=roster,
                         reason=reason) or True,
    )
    monkeypatch.setattr(GE, "_load_park_idle", lambda: fake_pk)
    ex = GE.live_dispatch_executors()
    out = ex["retire_fn"](_seat())
    assert calls["name"] == "gm-gen2"                # retires the PREDECESSOR
    assert out is True                              # returns park-idle.retire's bool
    assert "graduation" in calls["reason"].lower()  # reason names the graduation


# ---- create_fn / notify_fn: approval store + notify ---------------------------

def test_create_fn_maps_card_kwargs_to_approval_store(monkeypatch):
    captured = {}
    class _Store:
        def migrate(self):
            captured["migrated"] = True
        def create(self, **kw):
            captured["kw"] = kw
            return "row-123"
    monkeypatch.setattr(GE, "_approval_store", lambda: _Store())
    ex = GE.live_dispatch_executors()
    rid = ex["create_fn"](
        from_agent="gm-gen2", question="Promote?", worker_kind="pane",
        op_key="promote_graded:gm:2", options=["approve", "respond"],
        kind="promote_graded", summary="s", risk_level="medium",
        reversibility="hard", feature="lineage", evidence={"x": 1})
    assert rid == "row-123"
    assert captured["kw"]["op_key"] == "promote_graded:gm:2"
    assert captured["kw"]["kind"] == "promote_graded"
    assert captured.get("migrated") is True


def test_notify_fn_calls_approval_notify(monkeypatch):
    seen = {}
    monkeypatch.setattr(GE, "_approval_notify",
                        lambda rid: seen.update(rid=rid))
    ex = GE.live_dispatch_executors()
    ex["notify_fn"]("row-abc")
    assert seen["rid"] == "row-abc"


def test_skip_notify_fn_sends_durable_ping(monkeypatch):
    sent = {}
    monkeypatch.setattr(GE, "_send_ping", lambda msg: sent.update(msg=msg))
    ex = GE.live_dispatch_executors()
    ex["skip_notify_fn"]("[self-retire] card SKIPPED (graduated-T2): retiring gm-gen2")
    assert "gm-gen2" in sent["msg"]


# ---- end-to-end via dispatch_graduation (STUBBED real targets) ---------------
# The executor dict is exercised THROUGH the real dispatch to prove the shapes
# line up and ordering holds. No real target runs (all monkeypatched).

def _wire_stubs(monkeypatch, order):
    monkeypatch.setattr(GE, "_safety_recheck",
                        lambda c, orchestra_dir=None:
                        order.append("gate1") or ("SUPERSEDED_SAFE", "ok"))
    monkeypatch.setattr(GE, "_promote",
                        lambda canonical_id, successor_session, **kw:
                        order.append("promote") or {"written": ["r"]})
    fake_pk = types.SimpleNamespace(
        REGISTRY="/r", AGENT_SESSIONS="/s", LIVE_ROSTER="/l",
        _load=lambda p: {}, live_sessions=lambda: {},
        retire=lambda *a, **k: order.append("retire") or True)
    monkeypatch.setattr(GE, "_load_park_idle", lambda: fake_pk)


def _readers_for_gm(**over):
    """Live-shaped readers for predecessor gm-gen2 -> successor gm-gen3."""
    base = dict(grade="PASS", succ="gm-gen3", beats=3, succ_live=True,
                handoff=True, duty=None)
    base.update(over)
    return {
        "successor_of": lambda p: base["succ"],
        "grade_of": lambda s: base["grade"],
        "beats_since_promotion_of": lambda p: base["beats"],
        "successor_live": lambda s: base["succ_live"],
        "handoff_confirmed_of": lambda s: base["handoff"],
        "pending_duty_of": lambda p: base["duty"],
        "seat_meta_of": lambda p: {"lineage_root": "gm", "generation": 2,
                                   "grade_commit": "c"},
    }


def test_e2e_graduated_armed_runs_gate1_promote_retire_in_order(monkeypatch):
    order = []
    _wire_stubs(monkeypatch, order)
    pings = []
    monkeypatch.setattr(GE, "_send_ping", lambda msg: pings.append(msg))
    ex = GE.live_dispatch_executors()
    out = D.dispatch_graduation(
        "gm-gen2", _readers_for_gm(), disabled_path="/none", armed=True,
        graduated_fn=lambda s: True, used_pct=88, **ex)
    assert out["action"] == "proceed_no_card"
    assert out["executed"]["action"] == "promoted_and_retired"
    assert order == ["gate1", "promote", "retire"]      # STRICT ordering
    assert len(pings) == 1                              # skip_notify fires ONCE
    assert "gm-gen2" in pings[0] and "88" in pings[0]


def test_e2e_supervised_cards_and_never_retires(monkeypatch):
    order = []
    _wire_stubs(monkeypatch, order)
    created = {}
    class _Store:
        def migrate(self): pass
        def create(self, **kw):
            created.update(kw)
            return "row-1"
    monkeypatch.setattr(GE, "_approval_store", lambda: _Store())
    monkeypatch.setattr(GE, "_approval_notify", lambda rid: None)
    pings = []
    monkeypatch.setattr(GE, "_send_ping", lambda msg: pings.append(msg))
    ex = GE.live_dispatch_executors()
    out = D.dispatch_graduation(
        "gm-gen2", _readers_for_gm(), disabled_path="/none", armed=True,
        graduated_fn=lambda s: False, **ex)               # NOT graduated -> card
    assert out["action"] == "await_card"
    assert out["emitted"]["row_id"] == "row-1"
    assert created["kind"] == "promote_graded"
    assert "promote" not in order and "retire" not in order   # NEVER retired
    assert pings == []                                        # no D2 ping on card path


def test_e2e_kill_gate1_abort_promotes_nothing(monkeypatch):
    order = []
    monkeypatch.setattr(GE, "_safety_recheck",
                        lambda c, orchestra_dir=None:
                        order.append("gate1") or ("PENDING_WORK", "typed text"))
    monkeypatch.setattr(GE, "_promote",
                        lambda **k: order.append("promote") or {})
    fake_pk = types.SimpleNamespace(
        REGISTRY="/r", AGENT_SESSIONS="/s", LIVE_ROSTER="/l",
        _load=lambda p: {}, live_sessions=lambda: {},
        retire=lambda *a, **k: order.append("retire") or True)
    monkeypatch.setattr(GE, "_load_park_idle", lambda: fake_pk)
    pings = []
    monkeypatch.setattr(GE, "_send_ping", lambda msg: pings.append(msg))
    ex = GE.live_dispatch_executors()
    out = D.dispatch_graduation(
        "gm-gen2", _readers_for_gm(), disabled_path="/none", armed=True,
        graduated_fn=lambda s: True, **ex)
    assert out["executed"]["action"] == "aborted_kill_gate1"
    assert order == ["gate1"]                       # promote/retire NEVER reached
    assert pings == []                              # no ping: nothing retired


def test_e2e_dry_default_executes_nothing(monkeypatch):
    # armed=False (the SHIPPED default) with real executors present: dispatch must
    # compute would-execute and touch NONE of the real targets.
    order = []
    _wire_stubs(monkeypatch, order)
    pings = []
    monkeypatch.setattr(GE, "_send_ping", lambda msg: pings.append(msg))
    ex = GE.live_dispatch_executors()
    out = D.dispatch_graduation(
        "gm-gen2", _readers_for_gm(), disabled_path="/none", armed=False,
        graduated_fn=lambda s: True, **ex)
    assert out["executed"] == {"would_execute": True}
    assert order == [] and pings == []              # dry: nothing ran
