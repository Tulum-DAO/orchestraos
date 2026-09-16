"""Piece-1 S1/pre-promote WIRING (DEC-1787861488): the trigger snapshot rides
the hold row from hold-open to promote.

  * hold_ledger.add_hold accepts trigger_evidence and stores it on the row
    (omitted -> the row is byte-identical to today: no key at all).
  * beat.rotation_beat gains an injectable `trigger_snapshot_fn(canary)` seam:
    at S1 hold-open (a hard_rotate that HOLDs) the snapshot is recorded into
    the hold row. Seam absent / returning None / raising -> the hold is still
    recorded exactly as today, with NO evidence key (fail-safe: a snapshot
    failure must never fabricate evidence NOR wedge the hold bookkeeping).
  * build_completion_provider's DEFAULT promote seam threads the hold row's
    trigger_evidence into promote_successor.promote(trigger_evidence=...) —
    the pre-promote half of C1's store-anchored evidence path. An INJECTED
    promote_fn (tests / future wiring) keeps its 2-arg contract untouched.
"""
from scripts.lineage_daemon import beat
from scripts.lineage_daemon import complete as C
from scripts.lineage_daemon import execute as ex
from scripts.lineage_daemon import hold_ledger as hledger


SNAP = {"used_pct": 86, "detector_ts": 1000.0, "detector_path": "/tmp/x.json",
        "snapshot_at": 1060.0, "predecessor_sid": "sess-predecessor"}

REG = {"agents": {"cx-canary": {"generation": 1, "system_prompt": "prompts/x.md",
                                "cwd": "/w", "tier": "T2"}}}
SAFE = lambda c: ("SUPERSEDED_SAFE", "ok")
APPROVE = lambda c, s: "approve"
HELD_CONFIRM = lambda c, s: {"outcome": "held", "rounds": 3,
                             "reasons": ["comprehension:not-absorbed"]}


class FakeExecutors:
    def __init__(self):
        self.calls = []

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
        return self._rec("verify_successor", armed, alive=True)

    def plan_inject_init(self, s, p, armed=False, orchestra_dir=None):
        return self._rec("inject_init", armed)

    def plan_wire_edge(self, p, s, g, armed=False, orchestra_dir=None):
        return self._rec("wire_edge", armed)

    def plan_verify_edge(self, p, s, armed=False, orchestra_dir=None):
        return self._rec("verify_edge", armed, ok=True, succeeded_by=s)

    def plan_retire(self, a, armed=False, orchestra_dir=None):
        return self._rec("retire", armed)

    def plan_repin_canonical(self, s, c, g, armed=False, orchestra_dir=None):
        return self._rec("repin_canonical", armed)


def _agent(pct=95):
    return {"agent_id": "cx-canary", "tier_class": "T2",
            "death": {}, "ctx": {"status_bar_pct": pct}}


# --------------------------------------------------------- hold_ledger.add_hold
def test_add_hold_stores_trigger_evidence():
    led = hledger.add_hold({"holds": []}, canary="cx-canary",
                           successor="cx-canary-g2",
                           status="hold:successor-unconfirmed", reason="r",
                           now=5, trigger_evidence=SNAP)
    assert led["holds"][0]["trigger_evidence"] == SNAP


def test_add_hold_without_evidence_is_byte_identical():
    led = hledger.add_hold({"holds": []}, canary="cx-canary",
                           successor="cx-canary-g2",
                           status="hold:successor-unconfirmed", reason="r", now=5)
    assert "trigger_evidence" not in led["holds"][0]


# ------------------------------------------------------ beat.rotation_beat (S1)
def test_beat_hold_open_records_snapshot_on_hold_row():
    t = beat.rotation_beat(
        _agent(), REG, canary="cx-canary", now=1234,
        executors_impl=FakeExecutors(), safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=HELD_CONFIRM, trigger_snapshot_fn=lambda c: dict(SNAP))
    assert t["status"] == ex.HOLD_UNCONFIRMED
    holds = t["ledger"]["holds"]
    assert len(holds) == 1
    assert holds[0]["trigger_evidence"] == SNAP


def test_beat_without_seam_is_byte_identical():
    t = beat.rotation_beat(
        _agent(), REG, canary="cx-canary", now=1234,
        executors_impl=FakeExecutors(), safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=HELD_CONFIRM)
    assert "trigger_evidence" not in t["ledger"]["holds"][0]


def test_beat_snapshot_none_or_raising_never_fabricates_or_wedges():
    t = beat.rotation_beat(
        _agent(), REG, canary="cx-canary", now=1234,
        executors_impl=FakeExecutors(), safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=HELD_CONFIRM, trigger_snapshot_fn=lambda c: None)
    assert "trigger_evidence" not in t["ledger"]["holds"][0]

    def boom(c):
        raise RuntimeError("detector exploded")
    t2 = beat.rotation_beat(
        _agent(), REG, canary="cx-canary", now=1234,
        executors_impl=FakeExecutors(), safety_fn=SAFE, approval_fn=APPROVE,
        confirm_fn=HELD_CONFIRM, trigger_snapshot_fn=boom)
    assert t2["status"] == ex.HOLD_UNCONFIRMED       # hold still recorded
    assert "trigger_evidence" not in t2["ledger"]["holds"][0]


# ------------------------------- provider default promote seam threads evidence
L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"


def _seams():
    return dict(
        live_sid_fn=lambda a: L_SID,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS", "strict": True,
                                    "artifact_written": True},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        retire_fn=lambda c: {"retired": True},
    )


def _hold(ev=None):
    h = {"canary": "pm-x", "successor": "pm-x-g2",
         "status": "hold:successor-unconfirmed", "reason": "same-beat",
         "created_at": 0, "resolved": False}
    if ev is not None:
        h["trigger_evidence"] = ev
    return h


BUDGET = {"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True}


def test_default_promote_seam_threads_hold_evidence(monkeypatch):
    import scripts.promote_successor as PS
    got = {}

    def rec(canary, alias, **kw):
        got.update(kw, canary=canary, alias=alias)
        return {"ok": True}
    monkeypatch.setattr(PS, "promote", rec)
    prov = C.build_completion_provider(**_seams())        # promote_fn=None -> default
    tr = prov("pm-x", _hold(ev=dict(SNAP)), armed=True, budget=BUDGET, now=9)
    assert tr["status"] == C.COMPLETED
    assert got["trigger_evidence"] == SNAP                # threaded from the HOLD row


def test_default_promote_seam_none_evidence_passes_none(monkeypatch):
    import scripts.promote_successor as PS
    got = {}

    def rec(canary, alias, **kw):
        got.update(kw)
        return {"ok": True}
    monkeypatch.setattr(PS, "promote", rec)
    prov = C.build_completion_provider(**_seams())
    tr = prov("pm-x", _hold(), armed=True, budget=BUDGET, now=9)
    assert tr["status"] == C.COMPLETED
    assert got.get("trigger_evidence") is None            # hand-path byte-identical
