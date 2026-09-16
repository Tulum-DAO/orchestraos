"""Tests for the proper-self-rotation content seam (WS3 acceptance Req 2+3).

platform-builder's half: generate the FOCUS-BEARING lineage-init the successor
receives (so it's focused on the RIGHT work, not 'read your handoff'), and the
online+oriented callback it fires to gm/owner. ob's daemon injects (1) and the
successor emits (2); these are the content generators (pure/testable).
"""
from scripts.focus_registry.lineage_init import (
    inherited_focus,
    build_lineage_init,
    build_online_callback,
)
from scripts.focus_registry.store import load_store, save_store

_HANDOFF = {
    "current_goal": "ship the audience staging chart",
    "phase_state": {"plan_ref": "docs/spec.md", "phase": "3 of 5",
                    "next_gate": "chart renders on audience 421e3319"},
    "open_loops": ["merge-9 holding land of 77eeb56", "filter-scaling fix owed"],
    # real handoff_schema shape: decisions are {"text", "rationale"} (NOT "decision")
    "decisions": [{"text": "never co-edit agents.ts", "rationale": "chatmode owns it"}],
    "next_3_actions": ["fix the staging chart query", "re-run importer", "verify render"],
    "file_roots_touched": ["dashboard/src/audience"],
}


def _store(tmp_path):
    store = {
        "version": 1, "source": "t", "updated_at": None,
        "entities": {
            "focus:custom-intent-audiences": {
                "id": "focus:custom-intent-audiences", "type": "focus", "status": "active",
                "canonical": "Custom-intent audiences",
                "owner": "agent:opus-11",
                "attrs": {"category": "ACME-APP", "pct_done": 80,
                          "agents": ["agent:opus-11"], "notes": "Live feature."},
            },
        },
        "edges": [{"src": "agent:opus-11", "rel": "works_on", "dst": "focus:custom-intent-audiences"}],
    }
    p = tmp_path / "focus-registry.json"
    save_store(store, str(p))
    return load_store(str(p))


def test_inherited_focus_resolves_predecessors_focus(tmp_path):
    store = _store(tmp_path)
    f = inherited_focus("agent:opus-11", store)
    assert f["id"] == "focus:custom-intent-audiences"


def test_lineage_init_carries_focus_and_requires_confirmation(tmp_path):
    store = _store(tmp_path)
    init = build_lineage_init("agent:opus-11", "agent:opus-11-g2", _HANDOFF, store,
                              reviewer_fn=lambda a: None)
    assert init["successor"] == "agent:opus-11-g2"
    assert init["focus"]["id"] == "focus:custom-intent-audiences"
    assert init["focus"]["canonical"] == "Custom-intent audiences"
    assert init["focus_confirm_required"] is True   # Req 2: must confirm focus on orientation


def test_lineage_init_points_at_specific_work_not_generic(tmp_path):
    store = _store(tmp_path)
    init = build_lineage_init("agent:opus-11", "agent:opus-11-g2", _HANDOFF, store,
                              reviewer_fn=lambda a: None)
    # specific work from the authored handoff — not a generic "read your handoff"
    assert init["specific_work"]["next_actions"] == _HANDOFF["next_3_actions"]
    assert init["specific_work"]["next_gate"] == "chart renders on audience 421e3319"
    assert init["specific_work"]["plan_ref"] == "docs/spec.md"


def test_lineage_init_includes_standing_and_authored_guards(tmp_path):
    store = _store(tmp_path)
    init = build_lineage_init("agent:opus-11", "agent:opus-11-g2", _HANDOFF, store,
                              reviewer_fn=lambda a: None)
    guards = " ".join(init["guards"]).lower()
    assert "single-trunk" in guards          # standing guard
    assert "never kill" in guards or "never-kill" in guards
    # authored decision TEXT carried through (schema field is 'text', not 'decision')
    assert any("never co-edit agents.ts" in g for g in init["guards"])


def test_lineage_init_notify_target_is_route_handoff_owner(tmp_path):
    store = _store(tmp_path)
    init = build_lineage_init("agent:opus-11", "agent:opus-11-g2", _HANDOFF, store,
                              reviewer_fn=lambda a: None)
    # opus-11 owns its focus -> notify the owner (itself as the lineage owner-PM)
    assert init["notify"] == "agent:opus-11"
    assert init["online_callback_required"] is True   # Req 3


def test_lineage_init_drift_pred_notifies_gm(tmp_path):
    store = _store(tmp_path)
    init = build_lineage_init("agent:ghost", "agent:ghost-g2", _HANDOFF, store,
                              reviewer_fn=lambda a: None)
    assert init["focus"] is None            # no focus -> drift
    assert init["notify"] == "agent:gm"     # Q4 gm-first


def test_online_callback_carries_evidence_as_a_CLAIM_not_a_confirmation(tmp_path):
    # RED-TEAM H4: the callback is self-attestation wearing a uniform. It must carry
    # EVIDENCE (read-back + canary answers) the daemon grades — NOT a formatted
    # 'CONFIRMED' claim. 'oriented/confirmed' is decided by the daemon's gate, not here.
    store = _store(tmp_path)
    readback = {"goal": "staging chart", "open_loops": ["merge-9 77eeb56"], "hazards": ["dddddddd"]}
    canary = {"q1": "77eeb56"}
    cb = build_online_callback("agent:opus-11-g2", "focus:custom-intent-audiences",
                               "agent:opus-11", _HANDOFF, store,
                               readback=readback, canary_answers=canary)
    assert cb["to"] == "agent:opus-11"
    assert cb["from"] == "agent:opus-11-g2"
    assert cb["claim_focus"] == "focus:custom-intent-audiences"   # a CLAIM, not confirmed
    assert cb["evidence"]["readback"] == readback
    assert cb["evidence"]["canary_answers"] == canary
    body = cb["body"].lower()
    assert "claim" in body and "pending" in body     # framed as unverified until graded
    assert "confirmed" not in body                   # never self-assert confirmation


def test_online_callback_correction_ack_defaults_none(tmp_path):
    # C2: initial R1 online callback carries correction_ack=None (not responding to a correction).
    store = _store(tmp_path)
    cb = build_online_callback("agent:opus-11-g2", "focus:custom-intent-audiences",
                               "agent:opus-11", _HANDOFF, store)
    assert cb["correction_ack"] is None


def test_online_callback_carries_correction_ack_nonce(tmp_path):
    # C2 (ob §2.6): when the successor responds to a correction, it echoes the nonce
    # so ob's re-verify keys on the ACK, not renewed activity (H8).
    store = _store(tmp_path)
    cb = build_online_callback("agent:opus-11-g2", "focus:custom-intent-audiences",
                               "agent:opus-11", _HANDOFF, store, nonce="corr-a1b2c3d4")
    assert cb["correction_ack"] == "corr-a1b2c3d4"


def test_online_callback_no_focus_still_valid(tmp_path):
    store = _store(tmp_path)
    cb = build_online_callback("agent:x-g2", None, "agent:gm", _HANDOFF, store)
    assert cb["to"] == "agent:gm"
    assert "online" in cb["body"].lower()
