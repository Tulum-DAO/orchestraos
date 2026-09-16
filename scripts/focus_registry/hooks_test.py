"""Tests for the WS3 arm-orchestration hook BODIES (platform-builder half).

These are the injected-callback bodies the lineage daemon fires (default None/no-op
on ob's side). Pure/injectable; no live agent driven. Consensus DEC-1786699478.
"""
import json

from scripts.focus_registry.hooks import (
    on_rotation_complete,
    on_graceful_shutdown,
    drift_flag,
)
from scripts.focus_registry.store import load_store, save_store


def _store_with(tmp_path):
    store = {
        "version": 1, "source": "t", "updated_at": None,
        "entities": {
            "focus:f1": {"id": "focus:f1", "type": "focus", "status": "active",
                         "owner": "agent:pred", "attrs": {"category": "ORCHESTRAOS",
                                                          "agents": ["agent:pred"]}},
            "focus:f2": {"id": "focus:f2", "type": "focus", "status": "active",
                         "owner": None, "attrs": {"category": "BUSINESS", "agents": ["agent:pred"]}},
        },
        "edges": [
            {"src": "focus:f1", "rel": "owned_by", "dst": "agent:pred"},
            {"src": "agent:pred", "rel": "works_on", "dst": "focus:f1"},
            {"src": "agent:pred", "rel": "works_on", "dst": "focus:f2"},
        ],
    }
    p = tmp_path / "focus-registry.json"
    save_store(store, str(p))
    return str(p)


# --- 2.2 on_rotation_complete: focus-edge inheritance ---

def test_rotation_transfers_owner_and_edges_pred_to_succ(tmp_path):
    p = _store_with(tmp_path)
    res = on_rotation_complete("agent:pred", "agent:succ", store_path=p)
    assert res["transferred"] is True
    store = load_store(p)
    assert store["entities"]["focus:f1"]["owner"] == "agent:succ"
    srcs_dsts = [(e["src"], e["dst"]) for e in store["edges"]]
    assert ("focus:f1", "agent:succ") in srcs_dsts          # owned_by repointed
    assert ("agent:succ", "focus:f1") in srcs_dsts          # works_on repointed
    assert ("agent:succ", "focus:f2") in srcs_dsts
    assert not any("agent:pred" in (s, d) for s, d in srcs_dsts)  # no dangling pred


def test_rotation_canonical_name_reuse_is_noop(tmp_path):
    # Gap 7 reuses the canonical name -> pred_id == succ_id -> nothing changes.
    p = _store_with(tmp_path)
    before = json.dumps(load_store(p), sort_keys=True)
    res = on_rotation_complete("agent:pred", "agent:pred", store_path=p)
    assert res["transferred"] is False
    assert json.dumps(load_store(p), sort_keys=True) == before


def test_rotation_no_edges_for_unknown_pred_is_safe(tmp_path):
    p = _store_with(tmp_path)
    res = on_rotation_complete("agent:ghost", "agent:succ", store_path=p)
    assert res["transferred"] is False


# --- 2.3 drift-guard: FLAG-ONLY (gm ruling, never a hold) ---

def test_drift_flag_fires_for_offfocus_agent():
    store = {"entities": {}, "edges": []}
    flag = drift_flag("agent:ghost", store, intended_action="hard_rotate")
    assert flag is not None
    assert flag["agent"] == "agent:ghost"
    assert flag["would_have"] == "hard_rotate"
    assert flag["route"] == "agent:gm"      # gm-first triage (Q4)
    assert flag["blocks"] is False          # FLAG-ONLY — never prevents the action


def test_drift_flag_none_for_agent_on_active_focus(tmp_path):
    store = load_store(_store_with(tmp_path))
    assert drift_flag("agent:pred", store, intended_action="hard_rotate") is None


# --- 2.1 on_graceful_shutdown: route_handoff delivery body (stubbed hook) ---

def test_graceful_shutdown_delivers_handoff_to_focus_owner(tmp_path):
    store = load_store(_store_with(tmp_path))
    sent = []
    deliver = lambda owner, payload: sent.append((owner, payload)) or {"ok": True}
    # agent:pred owns focus:f1 -> route_handoff resolves the owner-PM.
    res = on_graceful_shutdown("agent:adaptiv-industries-chart", store,
                               handoff={"open_loops": ["x"]},
                               deliver_fn=deliver,
                               reviewer_fn=lambda a: None)
    # adaptiv-industries-chart isn't in this store -> drift -> routes to gm.
    assert res["delivered_to"] == "agent:gm"
    assert len(sent) == 1
    assert sent[0][0] == "agent:gm"
    assert sent[0][1]["handoff"] == {"open_loops": ["x"]}


def test_graceful_shutdown_routes_to_owner_pm_when_present(tmp_path):
    store = load_store(_store_with(tmp_path))
    sent = []
    deliver = lambda owner, payload: sent.append(owner) or {"ok": True}
    # agent:pred works_on focus:f1 which is owned by agent:pred itself -> owner route.
    res = on_graceful_shutdown("agent:pred", store, handoff={},
                               deliver_fn=deliver, reviewer_fn=lambda a: None)
    assert res["delivered_to"] == "agent:pred"
    assert sent == ["agent:pred"]


def test_graceful_shutdown_is_fail_open_on_deliver_error(tmp_path):
    store = load_store(_store_with(tmp_path))
    def boom(owner, payload):
        raise RuntimeError("gateway down")
    # A delivery failure must NOT raise out of the hook (fail-open -> reap proceeds).
    res = on_graceful_shutdown("agent:pred", store, handoff={},
                               deliver_fn=boom, reviewer_fn=lambda a: None)
    assert res["delivered_to"] == "agent:pred"
    assert res["ok"] is False
    assert "error" in res
