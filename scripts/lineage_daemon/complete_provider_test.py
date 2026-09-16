"""build_completion_provider — the REAL-seam factory that assembles a
completion_provider(canary, hold_row, *, armed, budget, now) matching the fleet.py
seam, by wrapping the EXISTING live seams (promote_successor / S3 confirm / auto_grade
/ graduation / safety / retire / live-sid). Every sub-seam is injectable so these
tests drive a SANDBOX (no real promote, no real kill, no shell-out).

Proves: provider wired + a synthetic armed+ready lineage -> a beat COMPLETES
(rotated>0, promoted, retired, resolve_hold called); provider wired + NOT armed /
soft_only -> rotated=0 (the arm gate lives in plan_fleet, so a factory-built provider
is never even called).
"""
from scripts.lineage_daemon import fleet
from scripts.lineage_daemon import complete as C
from scripts.lineage_daemon import hold_ledger as hledger


L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"


def _ready_seams(**over):
    """An injected seam set describing a synthetic armed+READY successor that
    completes cleanly (all-predecessor stores -> proceed -> promote+retire)."""
    calls = {"promote": [], "retire": []}
    d = dict(
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
        promote_fn=lambda c, alias: calls["promote"].append((c, alias)) or {"ok": True},
        retire_fn=lambda c: calls["retire"].append(c) or {"retired": True},
    )
    d.update(over)
    return d, calls


REG = {"agents": {"pm-x": {"generation": 1, "tier": "T2", "lineage_root": "pm-x"}}}


def _agent(aid, pct=95, tier="T2"):
    return {"agent_id": aid, "tier_class": tier, "death": {},
            "ctx": {"status_bar_pct": pct}}


def _held(canary="pm-x", successor="pm-x-g2"):
    return {"holds": [{"canary": canary, "successor": successor,
                       "status": "hold:successor-unconfirmed", "reason": "same-beat",
                       "created_at": 0, "resolved": False}]}


class _ArmedAny(frozenset):
    def __contains__(self, item):
        return True


ARMED_ANY = _ArmedAny()


# --- the factory assembles a provider that COMPLETES a ready lineage -----------

def test_factory_provider_completes_ready_lineage_directly():
    seams, calls = _ready_seams()
    prov = C.build_completion_provider(**seams)
    hold = _held()["holds"][0]
    tr = prov("pm-x", hold, armed=True,
              budget={"per_beat_slot": True, "hourly_remaining": 4,
                      "cooldown_clear": True}, now=9)
    assert tr["status"] == C.COMPLETED
    assert calls["promote"] == [("pm-x", "pm-x-g2")]     # real promote seam invoked
    assert calls["retire"] == ["pm-x"]                    # predecessor retired AFTER promote
    assert tr["steps"].index("promote") < tr["steps"].index("retire")


# --- wired through plan_fleet: an armed+ready lineage -> a beat COMPLETES -------

def test_factory_provider_via_plan_fleet_completes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
    seams, calls = _ready_seams()
    prov = C.build_completion_provider(**seams)
    out = fleet.plan_fleet(
        [_agent("pm-x")], REG, now=5, armed_tiers={"T2"}, ledger=_held(),
        armed_lineages=ARMED_ANY, orchestra_dir=str(tmp_path),
        completion_provider=prov)
    assert out["summary"]["rotated"] == 1                 # a canonical identity swap
    assert out["beats"][0]["armed_status"] == C.COMPLETED
    assert hledger.open_holds(out["ledger"]) == []        # resolve_hold applied
    assert calls["promote"] and calls["retire"]           # promote + retire fired


# --- INERT-UNTIL-ARMED: not-armed / soft_only -> rotated=0, provider never called

def test_factory_provider_not_armed_rotated_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
    seams, calls = _ready_seams()
    prov = C.build_completion_provider(**seams)
    out = fleet.plan_fleet(
        [_agent("pm-x")], REG, now=5, armed_tiers={"T2"}, ledger=_held(),
        armed_lineages=frozenset({"someone-else"}), orchestra_dir=str(tmp_path),
        completion_provider=prov)
    assert out["summary"]["rotated"] == 0
    assert calls["promote"] == [] and calls["retire"] == []   # never fired
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD


def test_factory_provider_soft_only_rotated_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_RUNTIME_DIR", str(tmp_path / "rt"))
    seams, calls = _ready_seams()
    prov = C.build_completion_provider(**seams)
    out = fleet.plan_fleet(
        [_agent("pm-x")], REG, now=5, armed_tiers={"T2"}, ledger=_held(),
        armed_lineages=ARMED_ANY, soft_only=True, orchestra_dir=str(tmp_path),
        completion_provider=prov)
    assert out["summary"]["rotated"] == 0
    assert calls["promote"] == []
    assert out["beats"][0]["armed_status"] == fleet.SKIP_OPEN_HOLD


# --- a torn-store re-entry HOLDs through the factory (fail-closed, no kill) -----

def test_factory_provider_torn_store_holds_no_kill():
    seams, calls = _ready_seams(
        provenance_sid_fn=lambda a: L_SID,
        canonical_store_sids_fn=lambda c: (L_SID, PRED_SID, PRED_SID))  # TORN
    prov = C.build_completion_provider(**seams)
    tr = prov("pm-x", _held()["holds"][0], armed=True,
              budget={"per_beat_slot": True, "hourly_remaining": 4,
                      "cooldown_clear": True}, now=9)
    assert tr["status"] == C.HOLD_TORN
    assert calls["promote"] == [] and calls["retire"] == []


# --- the factory with NO injected seams still returns a callable (smoke) --------

def test_factory_default_seams_returns_callable(tmp_path):
    prov = C.build_completion_provider(orchestra_dir=str(tmp_path))
    assert callable(prov)
