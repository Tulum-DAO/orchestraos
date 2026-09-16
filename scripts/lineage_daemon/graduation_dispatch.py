"""Stage C trigger-wiring (task #13) — the completion DISPATCHER.

Ties the pure substrate (graduation_retire) to LIVE state + surfaces, staying PURE
over injected readers/seams so it is hermetically testable. The Stop-hook entry
and the daemon backstop are thin live callers of `dispatch_graduation`.

Flow:
  build ctx+seat from injected readers -> plan_graduation_retire(...) ->
    "keep"            -> no-op (may_retire said not-yet); return the reason.
    "await_card"      -> emit the promote_graded card (create_fn + notify_fn);
                         NO retire here — Approve later runs the execute step via
                         resolve_promote_graded_cards. Returns the row id.
    "proceed_no_card" -> graduated-T2 mode: run execute_graduation_retire directly.

ARMING: `armed` defaults False. A DRY dispatch computes the full plan and reports
would-emit / would-execute WITHOUT touching ApprovalStore / promote / park-idle.
`armed=True` is passed ONLY by the separately-the operator-gated live caller — this build
ships dry; arming the card live is a separate future the operator go.
"""
from scripts.lineage_daemon import graduation_retire as GR


def _grade_agree(grade):
    """Map a grade signal to may_retire's grade_agree tri-state (KEEP-polarity):
    "PASS"->True, "FAIL"->False, anything else (None/pending/unknown)->None."""
    if grade is True or (isinstance(grade, str) and grade.strip().upper() == "PASS"):
        return True
    if grade is False or (isinstance(grade, str) and grade.strip().upper() == "FAIL"):
        return False
    return None


def build_ctx_seat(pred, readers):
    """Build (may_retire ctx, substrate seat) from injected live-state readers.
    Pure — every live read is a seam so this is hermetic in tests."""
    successor = readers["successor_of"](pred)
    meta = readers["seat_meta_of"](pred) or {}
    beats = readers["beats_since_promotion_of"](pred)
    ctx = {
        "successor_live": bool(readers["successor_live"](successor)),
        "handoff_confirmed": bool(readers["handoff_confirmed_of"](successor)),
        "grade_agree": _grade_agree(readers["grade_of"](successor)),
        "beats_since_promotion": beats,
        "pending_duty": readers["pending_duty_of"](pred),
    }
    seat = {
        "agent_id": pred, "successor": successor,
        "lineage_root": meta.get("lineage_root") or pred,
        "generation": meta.get("generation"),
        "grade_commit": meta.get("grade_commit"),
        "beats_since_promotion": beats,
    }
    return ctx, seat


def dispatch_graduation(pred, readers, *,
                        disabled_path=GR._DEFAULT_DISABLED_PATH,
                        graduated_fn=GR.is_graduated_autoretire,
                        armed=False,
                        create_fn=None, notify_fn=None,
                        kill_gate1_fn=None, promote_fn=None, retire_fn=None,
                        skip_notify_fn=None, used_pct=None):
    """Run ONE completion dispatch for predecessor `pred`. Returns a trace dict
    {action, reason, plan, emitted, executed, notified}. NEVER raises on a seam failure —
    a live-READER failure (build_ctx_seat) or plan/may_retire error is swallowed
    to a safe "keep" trace (fail toward not-acting), and an emit/execute error is
    recorded. gen20 finding: the daemon BACKSTOP caller cannot wrap this itself
    per-call, so the swallow lives HERE — the reader-raise can't crash the beat,
    and every caller (Stop-hook primary AND backstop) gets the guarantee for free.

    D2 (gen20 wire): on the proceed_no_card (graduated-T2) branch, AFTER a
    successful retire (execute -> promoted_and_retired), fire the notify-ONLY-AFTER
    ping (skip_notify_fn) — the operator's Q2 replaced the per-retire TAP with a
    notify-after, so a silent auto-retire is wrong. NOT fired on aborted_kill_gate1
    (no retire happened) and never on the carded path (that path taps the operator)."""
    try:
        ctx, seat = build_ctx_seat(pred, readers)
        plan = GR.plan_graduation_retire(ctx, seat, disabled_path=disabled_path,
                                         graduated_fn=graduated_fn)
    except Exception as e:  # noqa: BLE001 -- reader/plan failure -> KEEP (never raise)
        return {"action": "keep",
                "reason": f"dispatch reader/plan error -> KEEP (fail-safe): "
                          f"{type(e).__name__}: {e}",
                "plan": None, "emitted": None, "executed": None, "notified": None}
    trace = {"action": plan["action"], "reason": plan["reason"], "plan": plan,
             "emitted": None, "executed": None, "notified": None}

    if plan["action"] == "keep":
        return trace

    if plan["action"] == "await_card":
        card = plan["card"]
        if not armed:
            trace["emitted"] = {"would_emit": True}
            return trace
        # ARMED: create the row via the sanctioned seam, then fire the notify push
        # (mirrors approval.py request: store.create -> notify). Fail-safe: any
        # error records + returns row_id None (NEVER a fall-through to a retire).
        try:
            rid = create_fn(
                from_agent=card["from_agent"], question=card["question"],
                worker_kind=card["worker_kind"], op_key=card["op_key"],
                options=card["options"], kind=card["kind"], summary=card["summary"],
                risk_level=card["risk_level"], reversibility=card["reversibility"],
                feature=card["feature"], evidence=card["evidence"])
            if notify_fn is not None:
                try:
                    notify_fn(rid)
                except Exception:  # noqa: BLE001 -- notify is best-effort (cron backstop re-notifies)
                    pass
            trace["emitted"] = {"row_id": rid}
        except Exception as e:  # noqa: BLE001 -- fail-safe: no row, no retire
            trace["emitted"] = {"row_id": None,
                                "error": f"{type(e).__name__}: {e}"}
        return trace

    # plan["action"] == "proceed_no_card" (graduated-T2 mode)
    if not armed:
        trace["executed"] = {"would_execute": True}
        return trace
    executed = GR.execute_graduation_retire(
        seat, kill_gate1_fn=kill_gate1_fn, promote_fn=promote_fn,
        retire_fn=retire_fn)
    trace["executed"] = executed
    # D2: notify-ONLY-AFTER — fire the per-retire ping ONLY if the retire actually
    # happened (promoted_and_retired). A KILL GATE 1 abort (aborted_kill_gate1) did
    # NOT retire, so pinging the operator would be a false ping. notify_card_skipped is
    # itself fail-swallowed, but guard the CALL too so a missing seam is a no-op.
    if executed.get("action") == "promoted_and_retired" and skip_notify_fn is not None:
        from scripts.lineage_daemon.self_retire_gate import notify_card_skipped
        trace["notified"] = notify_card_skipped(
            seat, reason=plan.get("reason"), used_pct=used_pct,
            send_fn=skip_notify_fn)
    return trace
