"""Tests for the online-callback CLI wrapper (ob seam: init-task runs this on
orientation as the R1 online+confirmed-focus callback). Deterministic + testable
so the successor never hand-constructs the JSON."""
from scripts.focus_registry.online_callback import emit
from scripts.focus_registry.store import load_store, save_store


def _store(tmp_path):
    store = {
        "version": 1, "entities": {
            "focus:custom-intent-audiences": {
                "id": "focus:custom-intent-audiences", "type": "focus", "status": "active",
                "canonical": "Custom-intent audiences", "owner": "agent:opus-11",
                "attrs": {"category": "ACME-APP", "pct_done": 80, "agents": ["agent:opus-11"]},
            },
        }, "edges": [],
    }
    p = tmp_path / "focus-registry.json"
    save_store(store, str(p))
    return load_store(str(p))


def test_emit_sends_claim_with_evidence_to_notify_target(tmp_path):
    store = _store(tmp_path)
    sent = []
    send = lambda **kw: sent.append(kw) or {"sent": True}
    res = emit("agent:opus-11-g2", "focus:custom-intent-audiences", "agent:opus-11",
               handoff={"phase_state": {"next_gate": "chart renders 421e3319"}},
               store=store, readback={"goal": "chart"}, canary_answers={"q1": "77eeb56"},
               send_fn=send)
    assert res["sent"] is True
    assert len(sent) == 1
    kw = sent[0]
    assert kw["from_agent"] == "agent:opus-11-g2"
    assert kw["to_agent"] == "agent:opus-11"
    assert "claim" in kw["body"].lower()             # unverified claim, not confirmation
    assert "confirmed" not in kw["body"].lower()


def test_emit_carries_evidence_as_claim(tmp_path):
    store = _store(tmp_path)
    res = emit("agent:x-g2", "focus:custom-intent-audiences", "agent:gm",
               handoff={}, store=store, readback={"goal": "g"}, canary_answers={"q1": "a"},
               send_fn=lambda **kw: {"sent": True})
    assert res["callback"]["claim_focus"] == "focus:custom-intent-audiences"
    assert res["callback"]["evidence"]["readback"] == {"goal": "g"}
    assert res["callback"]["evidence"]["canary_answers"] == {"q1": "a"}
    assert res["callback"]["to"] == "agent:gm"
