"""WS3 arm-orchestration hook BODIES (platform-builder half). Consensus DEC-1786699478.

These are the injected-callback bodies the lineage daemon fires; on ob's side each
is an injected fn defaulting to None/no-op (mirroring execute_rotation's
approval_fn/safety_fn). This keeps platform routing/focus logic OUT of the daemon's
kill/spawn/retire internals. All bodies are FAIL-OPEN: a raise here must never
unwind a rotation nor block a reap (the daemon catches+logs → flagged reconcile).

Seams (ob-aligned, source-grounded):
  - on_rotation_complete(pred, succ) -> execute.py post-repin_canonical (~L149), before DONE.
  - on_graceful_shutdown(agent) -> park-idle reap pre-plan_retire (executors.py:189),
    CO-OWNED with ob's Gap 6 (lands when Gap 6 congruence passes + builds).
  - drift_flag -> emitted where the daemon decides an action (flag-only, gm ruling).

The arm-ladder EXECUTE step (live canary rotation) stays the operator-gated; these bodies
are build+test only until wired.
"""
from typing import Callable, Optional

from scripts.focus_registry.store import DEFAULT_STORE, load_store, save_store, _dedupe_edges
from scripts.focus_registry.resolve import is_drift
from scripts.focus_registry.route import route_handoff, default_reviewer_fn

GM = "agent:gm"


# --- 2.2 focus-edge inheritance on rotation -------------------------------------

def on_rotation_complete(pred_id: str, succ_id: str, store_path: str = DEFAULT_STORE) -> dict:
    """Transfer the predecessor's focus ownership + edges to the successor.

    Fires post-repin. In the common Gap-7 canonical-name-reuse case pred_id ==
    succ_id -> no-op. Idempotent; only writes when something changed. Every active
    focus that had an owner keeps a live owner (Decision-6 invariant).
    """
    if pred_id == succ_id:
        return {"transferred": False, "pred": pred_id, "succ": succ_id}

    store = load_store(store_path)
    changed = False

    for ent in store.get("entities", {}).values():
        if ent.get("owner") == pred_id:
            ent["owner"] = succ_id
            changed = True

    new_edges = []
    for e in store.get("edges", []):
        ne = dict(e)
        if ne.get("src") == pred_id:
            ne["src"] = succ_id
            changed = True
        if ne.get("dst") == pred_id:
            ne["dst"] = succ_id
            changed = True
        new_edges.append(ne)

    if changed:
        store["edges"] = _dedupe_edges(new_edges)
        save_store(store, store_path)

    return {"transferred": changed, "pred": pred_id, "succ": succ_id}


# --- 2.3 drift-guard (FLAG-ONLY, gm ruling: never a hold) -----------------------

def drift_flag(agent_id: str, store: dict, intended_action: str) -> Optional[dict]:
    """If the agent is attached to no active focus, return a gm-routed flag; else
    None. FLAG-ONLY — `blocks` is always False; it NEVER prevents the daemon's
    action (never-prevent-resurrections). Surfaces the off-focus cull for one gm
    triage beat (Q4 gm-first)."""
    if not is_drift(agent_id, store):
        return None
    return {
        "kind": "drift_flag",
        "agent": agent_id,
        "would_have": intended_action,
        "route": GM,
        "blocks": False,
    }


# --- 2.1 route_handoff delivery body (graceful shutdown, no successor) -----------

def _default_deliver(owner: str, payload: dict) -> dict:
    """Default delivery: durable msg_store send to the backlog owner (Q3).

    Injected in tests. Real path used only once ob's Gap-6 reap hook wires this in.
    """
    import subprocess
    import json as _json
    body = _json.dumps(payload.get("handoff", {}))
    subprocess.run(
        ["python3", "msg_store.py", "send", "--from", payload.get("from", "platform-builder"),
         "--to", _bare(owner), "--type", "handoff",
         "--subject", f"inherited backlog from {payload.get('from')}", "--body", body],
        check=True,
    )
    return {"ok": True}


def _bare(entity_id: str) -> str:
    return entity_id[len("agent:"):] if entity_id.startswith("agent:") else entity_id


def on_graceful_shutdown(
    agent_id: str,
    store: dict,
    handoff: dict,
    deliver_fn: Callable[[str, dict], dict] = _default_deliver,
    reviewer_fn: Callable[[str], Optional[str]] = default_reviewer_fn,
) -> dict:
    """Deliver a finishing (no-successor) agent's handoff+open_loops to its focus
    owner-PM BEFORE park-idle retires it. Resolves the owner via WS2 route_handoff
    (owner-PM -> reviewer_of -> category fallback; never null).

    FAIL-OPEN: a delivery error is captured (ok=False) and NEVER raised — the reap
    must proceed even if routing fails (park-idle still retires; degrade to a
    flagged reconcile). Exactly-once is guaranteed by the caller (reap fires once).
    """
    owner = route_handoff(agent_id, store, reviewer_fn)
    payload = {"kind": "handoff_delivery", "from": agent_id, "to": owner, "handoff": handoff}
    try:
        result = deliver_fn(owner, payload)
        return {"delivered_to": owner, "ok": True, "result": result}
    except Exception as e:  # fail-open: never block the reap
        return {"delivered_to": owner, "ok": False, "error": str(e)}
