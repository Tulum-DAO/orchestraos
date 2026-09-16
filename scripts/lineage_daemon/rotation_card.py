"""Tier-3 hard-rotation approval-emit — the WEDGED-class kill card (DEC-1786835700).

the operator ruling (msg_8860a706): a hard rotation NEVER auto-kills. A wedged agent —
idle past the HARD band with NO self-trigger mark, so it can't self-rotate
(jarvis-v4's exact class) — gets a per-kill APPROVAL CARD posted to the operator's
surface; the kill+respawn runs ONLY on the operator's Approve. This module is ob's lane:
the pure card builder + the dry-run-gated emit + the answer resolver. It NEVER
calls a kill inline — the only kill path is resolve() on an approve row, and the
live emit/kill is a SEPARATE the operator arm-go (dry_run defaults True).

Verb contract (ratified, gm msg_cd11cfed): Approve = kill+respawn; Respond =
free-text routed to gm, NO kill (Deny/Defer fold into Respond). No Deny/Defer/
Reject verb is produced.

Fail-safe (NOT fail-open-to-kill): any emit error leaves the agent DEFERRED
(today's safe no-op); a broken card path can never fall through to a kill.
"""

# only these self_trigger verdicts are cardable (the wedged class)
_CARDABLE_VERDICTS = ("wedged",)


def is_cardable(agent):
    """True iff the agent is the WEDGED class: crossed HARD but its self-trigger
    verdict is 'wedged' (not 'self_triggered' = handled its own rotation, and not
    None/'ok' = below band). Only a wedged agent gets a kill card."""
    if not isinstance(agent, dict):
        return False
    return agent.get("self_trigger") in _CARDABLE_VERDICTS


def build_rotation_card(agent):
    """The rotation_kill card payload (pure). op_key = rotation_kill:<lineage>:<gen>
    so a persistently-wedged agent gets ONE card per wedge (create() dedups a
    pending row on from_agent+op_key), and a fresh card only after an actual
    rotation bumps the generation."""
    aid = agent.get("agent_id")
    ctx = agent.get("ctx_pct")
    age_s = agent.get("state_age_s") or 0
    why = agent.get("self_trigger") or "wedged"
    root = agent.get("lineage_root") or aid
    gen = agent.get("generation")
    evidence = {
        "ctx_pct": ctx,
        "why_wedged": why,
        "last_activity_age_s": age_s,
        "open_tasks": agent.get("open_tasks") or [],
        "jsonl_turns": agent.get("jsonl_turns"),
        "lineage_root": root,
        "generation": gen,
        "tier": agent.get("tier_class"),
    }
    question = (f"Rotate (kill+respawn) {aid}? ctx {ctx}%, wedged "
                f"{int(age_s // 60)}m — {why}")
    return {
        "kind": "rotation_kill",
        "from_agent": aid,
        "worker_kind": "fleet-rotation",
        "op_key": f"rotation_kill:{root}:{gen}",
        "question": question,
        "summary": (f"{aid} is wedged (idle past HARD, no self-trigger) and can't "
                    f"self-rotate. Approve = kill + respawn from its committed "
                    f"handoff (reversible). Respond = tell gm what to do instead "
                    f"(no kill)."),
        "options": [
            {"label": "Approve", "value": "approve"},      # = kill + respawn
            {"label": "Respond", "value": "respond"},      # = free-text, gm-routed
        ],
        "risk_level": "high",
        "reversibility": "reversible",     # respawns from handoff — recovery, not loss
        "feature": "Tier-3 hard-rotation",
        "evidence": evidence,
    }


def emit_rotation_card(card, *, create_fn, dry_run=True, log_fn=None):
    """Emit ONE rotation_kill card. DRY-RUN (default): log 'would emit', create
    NOTHING. Armed: create_fn(**card_kwargs) -> row_id (the ApprovalStore.create
    seam; op_key dedup makes re-emit a no-op). Fail-safe: any error -> no row, the
    agent stays deferred (never a fall-through to a kill)."""
    if dry_run:
        if log_fn:
            log_fn({"would_emit": True, "from_agent": card["from_agent"],
                    "op_key": card["op_key"]})
        return {"would_emit": True, "dry_run": True, "row_id": None}
    try:
        row_id = create_fn(
            from_agent=card["from_agent"], question=card["question"],
            worker_kind=card["worker_kind"], op_key=card["op_key"],
            options=card["options"], kind=card["kind"], summary=card["summary"],
            risk_level=card["risk_level"], reversibility=card["reversibility"],
            feature=card["feature"], evidence=card["evidence"])
        return {"would_emit": False, "dry_run": False, "row_id": row_id}
    except Exception as e:  # noqa: BLE001 -- fail-SAFE: no row, agent stays deferred
        return {"would_emit": False, "dry_run": False, "row_id": None,
                "error": f"{type(e).__name__}: {e}"}


def resolve_rotation_cards(rows, *, execute_fn, route_fn, dry_run=True):
    """Resolve answered rotation_kill rows. The ONLY kill path in the system.

    approve -> execute_fn(from_agent) = kill+respawn, EXACTLY once (a row with
      resumed_at already acted -> no-op). armed only; dry-run logs would_kill.
    respond -> route_fn(from_agent, text) to gm, NO kill (Deny/Defer fold here).
    unanswered/other -> skipped.

    Returns a per-row action list. Fail-safe: an execute error is recorded, never
    raised, and does NOT mark the row acted (so it isn't silently dropped)."""
    out = []
    for row in rows or []:
        rid = row.get("id")
        aid = row.get("from_agent")
        if row.get("kind") != "rotation_kill":
            out.append({"id": rid, "action": "skip_not_rotation_kill"})
            continue
        if row.get("status") != "answered" or not row.get("answer"):
            out.append({"id": rid, "action": "skip_unanswered"})
            continue
        if row.get("resumed_at"):
            out.append({"id": rid, "action": "already_acted"})     # idempotent
            continue
        ans = str(row.get("answer")).lower()
        if ans == "approve":
            if dry_run:
                out.append({"id": rid, "action": "would_kill", "agent": aid})
                continue
            try:
                execute_fn(aid)
                out.append({"id": rid, "action": "killed", "agent": aid})
            except Exception as e:  # noqa: BLE001 -- record, never raise/silently drop
                out.append({"id": rid, "action": "kill_error",
                            "agent": aid, "error": f"{type(e).__name__}: {e}"})
        elif ans == "respond":
            route_fn(from_agent=aid, text=row.get("answer_text") or "",
                     op_key=row.get("op_key"))
            out.append({"id": rid, "action": "routed_no_kill", "agent": aid})
        else:
            out.append({"id": rid, "action": f"skip_unknown_answer:{ans}"})
    return out
