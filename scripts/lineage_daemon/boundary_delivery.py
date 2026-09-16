#!/usr/bin/env python3
"""boundary_delivery.py — §9.6 event-driven turn-boundary delivery of held rows.

s96 node boundary-delivery-core, DEC-1786921182 CONSENSUS (gm + agy). The ~90%
latency cut: a held §9.6 row (metadata.held_for_boundary, written by the gateway's
_hold_for_boundary at P0 accept-time) is delivered on the target's `turn_ended`
bus event — SECONDS, not the router's 0–60s poll. The router (message-router.py)
stays the BACKSTOP only (frozen contract); this delivers ALONGSIDE it.

SEPARATE consumer (council Q1): decoupled from bus_beat's promote/drain loop, so
pane capture + F11 + inject waits can't stall the drain or trip the watchdog.

`run_boundary_delivery` is PURE over injected seams (store / f11_ok / resolve_head
/ inject) — hermetically testable, no tmux/network. __main__ (a separate consumer,
NOT wired into the live bus_beat cron) supplies the real seams and ships DISABLED
behind BOUNDARY_DELIVER_ARMED + its own kill-switch.

BINDING CONDITIONS (non-negotiable, gm + agy):
  (A) SHARED store.claim() CAS = the once-only gate. Claim each row BEFORE bundling;
      claim-fail (router won the race) → drop from the bundle; inject-ok → deliver();
      inject-fail → fail() so the backstop retries. No double-delivery, ever.
  (B) F11 AT FIRE-TIME: re-read the pane immediately before inject; if it became a
      menu/permission prompt (no composer), ABORT + release the claim (→ pending) so
      the backstop bridges it. ZERO keystrokes into a live menu pane.
  (C) resolve_live_head(S) before inject; no live head → hold (never inject a bare
      session name). Verified inject; a delivered row never re-fires.
  (D) ships disabled → --shadow (log WOULD-deliver, zero injects/claims) → gm-arm.
"""
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
# The _real_* seams import SIBLING modules from scripts/ (lineage_resolve,
# agent-status). Guarantee scripts/ on sys.path in EVERY import context — the
# cron script-run had only repo-root + script-dir, so both inner imports raised
# ModuleNotFoundError and silently fail-closed (no_live_head / f11_blocked):
# the armed lane could never deliver (P0 pre-arm defect, drill 2026-08-18).
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _held_body(row):
    """RAW body — a held row IS a deferred user turn, delivered exactly as sent
    (no [MSG] envelope). Mirrors message-router.injection_text for deliver_raw."""
    return row.get("body") or ""


# §9.6-B.5 receiver-side standard preamble (DEC-1787032722, spec-verbatim from
# initiative-pm-driver-design.md). Applied ONLY when the caller passes it — the
# real consumer gates it on BOUNDARY_PREAMBLE_ENABLED (default OFF, gm's flag):
# OFF preserves the field-proven deliver_raw contract byte-for-byte; ON is an
# INTENTIONAL amendment to that contract (model-consumed text — needs a live
# observation window before default-on, the scrub-residue lesson).
# TODO(harmonization, v3 seam-sync msg_924b5b8d): when this flag AND the
# DEC-1787031444 digest gate both arm, converge this line with
# message-router.compose_digest's batched-inject format — one triage-preamble
# convention across both batched surfaces, not two dialects.
TRIAGE_PREAMBLE = ("[routed @turn-end] Triage vs your current mission: ACT-NOW "
                   "only if it unblocks/contributes or is a PM/gm directive; "
                   "else ACK+QUEUE and finish your current node first; DECLINE "
                   "with a reason if it isn't yours.")


def reportable_result(res: dict) -> bool:
    """#12 claim-3 (msg_66d1f98d): diagnostic per-session reasons must not
    collapse into an empty results list. Deliveries/would-deliveries always
    report; of the empty outcomes, no_held_rows/shadow are routine quiet, but
    no_live_head / f11_blocked / all_claimed_elsewhere / inject_failed are the
    states a starvation trace needs visible."""
    if res.get("delivered") or res.get("would_deliver"):
        return True
    return res.get("reason") in ("no_live_head", "f11_blocked",
                                 "all_claimed_elsewhere", "inject_failed")


def preamble_from_env(env) -> str | None:
    """Flag seam for the real consumer: BOUNDARY_PREAMBLE_ENABLED=1 -> the
    spec-verbatim preamble, anything else -> None (raw contract unchanged)."""
    return TRIAGE_PREAMBLE if (env or {}).get("BOUNDARY_PREAMBLE_ENABLED") == "1" else None


def run_boundary_delivery(session, *, store, f11_ok, resolve_head, inject,
                          armed=False, now_fired=None, preamble=None):
    """Deliver all pending held rows for `session` on its turn_ended, BUNDLED into
    one verified inject (created_at asc). Pure over seams:
      store        — .held_pending_for(session), .claim(id)->bool, .deliver(id), .fail(id,err),
                     .stamp_latency(id, created_at, fired_at) [optional]
      f11_ok(sess) — True iff the pane is safe to inject (NOT a menu/permission prompt)
      resolve_head(session) -> live-head session id | None
      inject(head, text) -> (ok, info)
      now_fired    — ISO timestamp of this boundary fire (per-delivery latency stamp,
                     §9.6 B6; created_at→fired_at delta makes the scorecard per-row)
    Returns a result dict. Shadow (armed False): compute would_deliver, write NOTHING.
    """
    rows = store.held_pending_for(session)
    if not rows:
        return {"session": session, "armed": armed, "delivered": [],
                "would_deliver": [], "reason": "no_held_rows"}

    if not armed:
        # SHADOW: report what WOULD deliver, zero claims/injects.
        return {"session": session, "armed": False, "delivered": [],
                "would_deliver": [r["id"] for r in rows], "reason": "shadow"}

    # (C) resolve the live head BEFORE anything; no head → hold for the backstop.
    head = resolve_head(session)
    if not head:
        return {"session": session, "armed": True, "delivered": [],
                "would_deliver": [], "reason": "no_live_head"}

    # (B) F11 at fire-time: a turn_ended racing a new menu/permission prompt must
    # NOT be injected — leave every row pending so the backstop bridges/surfaces.
    if not f11_ok(session):
        return {"session": session, "armed": True, "delivered": [],
                "would_deliver": [r["id"] for r in rows], "reason": "f11_blocked"}

    # (A) CAS-claim each row BEFORE composing the bundle; a claim-fail = the router
    # backstop already took it → drop from this bundle (once-only via shared claim).
    claimed = [r for r in rows if store.claim(r["id"])]
    if not claimed:
        return {"session": session, "armed": True, "delivered": [],
                "would_deliver": [], "reason": "all_claimed_elsewhere"}

    # BUNDLE (council Q2): one combined inject, created_at asc (claimed preserves it).
    if len(claimed) == 1:
        text = _held_body(claimed[0])
    else:
        text = "\n".join(_held_body(r) for r in claimed)
    # §9.6-B.5: one-line triage preamble, only when the caller supplies it
    # (flag-gated in the real consumer; None = raw contract, byte-identical).
    if preamble:
        text = preamble + "\n" + text

    ok, info = inject(head, text)
    if ok:
        _stamp = getattr(store, "stamp_latency", None)
        for r in claimed:
            store.deliver(r["id"])                 # answered→delivered; never re-fires
            # §9.6 B6 per-delivery latency stamp (created_at→fired_at) at fire-time —
            # makes the scorecard per-row, not just window-aggregate. Best-effort:
            # only if the store supports it AND a fire timestamp was supplied.
            if callable(_stamp) and now_fired is not None:
                try:
                    _stamp(r["id"], r.get("created_at"), now_fired)
                except Exception:  # noqa: BLE001 — latency stamp never fails delivery
                    pass
        return {"session": session, "armed": True,
                "delivered": [r["id"] for r in claimed], "head": head,
                "reason": "delivered", "fired_at": now_fired}
    # inject failed → release claims via fail() so the router backstop retries.
    for r in claimed:
        store.fail(r["id"], error=f"boundary inject failed: {info.get('reason', 'unknown')}")
    return {"session": session, "armed": True, "delivered": [],
            "would_deliver": [r["id"] for r in claimed],
            "reason": "inject_failed", "info": info}


# ---------------------------------------------------------------------------
# Real-seams consumer (SEPARATE from bus_beat's drain — council Q1). Ships
# DISABLED: BOUNDARY_DELIVER_ARMED unset -> shadow (log would-deliver, zero
# injects/claims). Own kill-switch ~/runtime/BOUNDARY_DELIVER_DISABLED (an
# e-stop distinct from the bus drain's, per council Q3 independent blast radius).
# Sentinel gates the boundary beat ONLY; held rows remain deliverable via the
# router backstop by design (freeze != strand) — gm ruling msg_c06db775,
# live-proven in the E2 edge-campaign leg: worst case = today's-latency behavior.
# NOT wired into the live bus_beat crontab by mere presence — a separate crontab
# line installs it, and only after gm's arm.
# ---------------------------------------------------------------------------
BOUNDARY_DISABLED_SENTINEL = os.path.expanduser("~/runtime/BOUNDARY_DELIVER_DISABLED")
_TURN_ENDED = "turn_ended"


def _armed() -> bool:
    return os.environ.get("BOUNDARY_DELIVER_ARMED") == "1"


def _kill_switched() -> bool:
    return os.path.exists(os.environ.get("BOUNDARY_DELIVER_DISABLED_FILE",
                                         BOUNDARY_DISABLED_SENTINEL))


def _real_store():
    from msg_store import MessageStore
    return MessageStore()


def _real_f11_ok(session):
    """F11 at fire-time: the pane is safe to inject ONLY if it is NOT a pending
    menu/permission prompt. Reuses the gateway's live detector via agent-status.
    Fail-CLOSED: any error -> False (don't inject on uncertainty; the backstop
    still delivers)."""
    try:
        import importlib
        A = importlib.import_module("agent-status")
        st = A.get_agent_status(session)
        menu = st.get("pending_menu") if isinstance(st, dict) else None
        return not (isinstance(menu, dict) and menu.get("kind")
                    in ("permission", "options", "menu"))
    except Exception:
        return False


def _real_resolve_head(session):
    """resolve_live_head (guard C) — reuse the lineage resolver.

    REAL contract (lineage_resolve): resolve_live_head -> (session|None, reason);
    session=None means do NOT inject. Unpack the tuple and fail-CLOSED: any
    error / None / empty session -> None (the router backstop still delivers)."""
    try:
        from lineage_resolve import resolve_live_head
        head, _reason = resolve_live_head(session)
        return head if isinstance(head, str) and head else None
    except Exception:
        return None


def _real_inject(head, text):
    """Verified RAW delivery via the gateway (POST /agent-message — inject ->
    verify-visible -> Enter -> verify-cleared). NEVER raw send-keys for a submit.
    Returns (ok, info)."""
    import urllib.request
    gateway = os.environ.get("WATCH_GATEWAY_URL", "http://127.0.0.1:9091")
    token = ""
    try:
        with open(os.path.expanduser("~/.config/jarvis/watch-gateway-token")) as fh:
            token = fh.read().strip()
    except OSError:
        pass
    body = json.dumps({"session": head, "text": text}).encode()
    req = urllib.request.Request(
        f"{gateway}/agent-message", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}",
                 "X-Actor": "automation:boundary-delivery"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode())
        return bool(r.get("ok") and r.get("delivered")), r
    except Exception as e:  # noqa: BLE001
        return False, {"reason": f"gateway_error:{type(e).__name__}"}


def _recent_turn_ended_sessions(now=None, window_s=180):
    """Sessions with a turn_ended event in the recent window (read-only over the
    hot event stream the bus already maintains). Resolved to canonical heads."""
    import time as _time
    from scripts.lineage_daemon import bus
    from scripts.lineage_daemon.bus_beat import hot_days
    now = now if now is not None else _time.time()
    stream_dir = os.environ.get(
        "ORCH_EVENT_STREAM_DIR",
        os.path.expanduser("~/scripts/agent-orchestra/state/event-stream"))
    resolve = _fallback_resolve()
    read = bus.read_events(stream_dir, now=now, days=hot_days(now, 1))
    out, seen = [], set()
    for ev in read.get("events", []):
        if ev.get("type") != _TURN_ENDED:
            continue
        ts = ev.get("ts") or ev.get("timestamp") or 0
        try:
            if now - float(ts) > window_s:
                continue
        except (TypeError, ValueError):
            pass
        agent = resolve(ev.get("pane"), ev.get("session_id"), ev.get("cwd"))
        if agent and agent not in seen:
            seen.add(agent); out.append(agent)
    return out


def _fallback_resolve():
    # bus_beat.default_resolve is the real one; import lazily to avoid a cycle.
    from scripts.lineage_daemon.bus_beat import default_resolve as _dr
    return _dr()


def main(argv=None) -> int:
    """One boundary-delivery beat. SEPARATE consumer; ships DISABLED (shadow
    unless BOUNDARY_DELIVER_ARMED=1). --shadow forces shadow even if armed."""
    import time as _time
    argv = list(sys.argv[1:] if argv is None else argv)
    force_shadow = "--shadow" in argv
    if _kill_switched():
        print(json.dumps({"ts": _time.time(), "BOUNDARY_DELIVER_DISABLED": True,
                          "sentinel": BOUNDARY_DISABLED_SENTINEL,
                          "msg": "boundary delivery e-stopped; no-op"}))
        return 0
    armed = _armed() and not force_shadow
    store = _real_store()
    from datetime import datetime, timezone
    fired_at = datetime.now(timezone.utc).isoformat()
    results = []
    for session in _recent_turn_ended_sessions(now=_time.time()):
        try:
            res = run_boundary_delivery(
                session, store=store, f11_ok=_real_f11_ok,
                resolve_head=_real_resolve_head, inject=_real_inject, armed=armed,
                now_fired=fired_at,
                # §9.6-B.5 flag (DEC-1787032722): default OFF = raw contract
                # unchanged; gm arms via BOUNDARY_PREAMBLE_ENABLED=1 in crontab.
                preamble=preamble_from_env(os.environ))
            if reportable_result(res):
                results.append(res)
        except Exception as e:  # noqa: BLE001 — one bad session never sinks the beat
            print(json.dumps({"session": session, "error": type(e).__name__}))
    print(json.dumps({"ts": _time.time(), "armed": armed,
                      "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
