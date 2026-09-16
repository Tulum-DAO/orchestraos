"""gm wake-on-delivery — the PURE CONTRACT (DEC-1786828407 CONSENSUS_REACHED,
gm+agy APPROVE; proposal .workspace/proposals/gm-wake-on-delivery.md, gm's binding
answers §7 baked in).

The gap (the operator-named): agents report done/blocker into gm's inbox, but gm only acts
on a the operator turn — delivered ≠ woken. This module is PB's lane: the wake DECISION +
digest + turn-start confirm, pure over injected snapshots (same discipline as
rotation_signal/classify). ob's lane wires these into bus.consume_beat + the
verified-inject gateway (inject/verify-visible/Enter/confirm) + cooldown
persistence. Auto-submitting Enter into the canonical T0 gm is a MOVED SURFACE:
DRY-RUN (no Enter, "would submit" log) first is MANDATORY; arming is a separate
gm+the operator step after the dry window proves the guard clean.

gm BINDING (§7): trigger = event-driven + beat fallback; needs_shaw = gm judgment
(digest only FLAGS candidates); cooldown 120s / batch cap 8; DRY-RUN first;
gm-idle = agent-status `idle` AND bus turn-boundary. Fail-open everywhere: any
malformed input -> {wake: False, reason} — a missed wake degrades to the old
behavior (gm acts on the next the operator turn), never worse.

THE LOAD-BEARING GUARD: never paste over a human-composing gm. Typed (non-ghost)
composer text ABORTS the wake; dim SGR-2 ghost/placeholder never blocks; an
unreadable composer is ambiguous -> fail-CLOSED abort. The style-walk is NOT
reimplemented here — it is the verified primitive `composer_typed_text`
(scripts/agent-status.py:210, gm verified-at-source).
"""
import importlib.util
import os

# gm binding answers (§7 Q3).
WAKE_COOLDOWN_S = 120
BATCH_CAP = 8
# Only these inbox priorities wake gm; normal/low wait for a natural the operator turn.
WAKE_PRIORITIES = ("high", "critical")
# Bus event types that count as gm sitting at a turn boundary (binding Q5 +
# proposal §2.1: turn_ended or session_end).
_TURN_BOUNDARY_EVENTS = ("turn_ended", "session_end")

_ORCH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_agent_status():
    spec = importlib.util.spec_from_file_location(
        "agent_status", os.path.join(_ORCH, "scripts", "agent-status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_agent_status = _load_agent_status()
composer_typed_text = _agent_status.composer_typed_text
_typed_chars = _agent_status._typed_chars
_PLACEHOLDER_RE = _agent_status.PLACEHOLDER_RE


def _composer_verdict(composer):
    """('ok'|'typed'|'unreadable') for a raw ANSI composer capture (str or list of
    lines, first ❯ line + wrapped continuations). Fail-CLOSED: no visible ❯
    composer = 'unreadable' (never inject into a pane we can't read)."""
    if composer is None:
        return "unreadable"
    lines = [composer] if isinstance(composer, str) else list(composer)
    if not lines or not any("\u276f" in (l or "") for l in lines):
        return "unreadable"
    typed = []
    seen_prompt = False
    for line in lines:
        line = line or ""
        if not seen_prompt:
            if "\u276f" not in line:
                continue
            seen_prompt = True
            typed.append(composer_typed_text(line))
        else:
            # wrapped continuation lines below the ❯ line (same style-walk)
            typed.append(_typed_chars(line).strip())
    text = " ".join(t for t in typed if t).strip()
    if _PLACEHOLDER_RE.match(text):
        text = ""
    return "typed" if text else "ok"


def _no_wake(reason):
    return {"wake": False, "batch": [], "total_pending": 0, "reason": reason}


def _actionable(inbox, woken_ids):
    """Unacked, unarchived HIGH/CRITICAL msgs not already woken-on. Malformed
    rows are skipped, not fatal (fail-open)."""
    out = []
    for m in inbox:
        if not isinstance(m, dict):
            continue
        if m.get("priority") not in WAKE_PRIORITIES:
            continue
        if m.get("acknowledged_at") or m.get("archived_at"):
            continue
        if m.get("id") in woken_ids or not m.get("id"):
            continue
        out.append(m)
    # critical ahead of high; stable within a tier (inbox order = arrival order)
    out.sort(key=lambda m: 0 if m.get("priority") == "critical" else 1)
    return out


def should_wake(inbox, gm_state, composer, cooldown_state, now=None):
    """The wake DECISION (pure). Returns {wake, batch, total_pending, reason}.

    Order: filter inbox -> gm-idle (BOTH signals) -> human-composing guard
    (fail-closed) -> cooldown (CRITICAL bypasses). batch is capped at BATCH_CAP;
    total_pending carries the uncapped count for the "+N more" digest line.
    Fail-open: ANY malformed input or internal error -> no wake with a reason —
    never raises, never wedges the beat."""
    try:
        if not isinstance(inbox, list):
            return _no_wake("bad_inbox")
        if not isinstance(gm_state, dict):
            return _no_wake("bad_gm_state")
        if not isinstance(cooldown_state, dict):
            # an unreadable cooldown file could mask a just-fired wake -> a storm;
            # fail toward the missed wake, never the storm. Transport passes {} +
            # last_wake_ts=None explicitly for a true cold start.
            return _no_wake("bad_cooldown_state")
        woken_ids = set(cooldown_state.get("woken_ids") or ())

        batch = _actionable(inbox, woken_ids)
        if not batch:
            return _no_wake("no_actionable")

        # gm-idle = BOTH agent-status idle AND bus turn-boundary (binding Q5)
        if gm_state.get("status") != "idle" or \
                gm_state.get("last_event_type") not in _TURN_BOUNDARY_EVENTS:
            return _no_wake("gm_not_idle")

        # THE load-bearing guard — never paste over typed input; ambiguous = abort
        verdict = _composer_verdict(composer)
        if verdict == "unreadable":
            return _no_wake("composer_unreadable")
        if verdict == "typed":
            return _no_wake("human_composing")

        # cooldown 120s; a CRITICAL in the batch bypasses (severity escalation)
        last = cooldown_state.get("last_wake_ts")
        has_critical = any(m.get("priority") == "critical" for m in batch)
        if last is not None and not has_critical:
            try:
                in_cooldown = (float(now) - float(last)) < WAKE_COOLDOWN_S
            except (TypeError, ValueError):
                return _no_wake("bad_cooldown_state")
            if in_cooldown:
                return _no_wake("cooldown")

        return {"wake": True, "batch": batch[:BATCH_CAP],
                "total_pending": len(batch), "reason": "actionable"}
    except Exception as e:  # fail-open: a broken wake is a missed wake, never a crash
        return _no_wake(f"error:{type(e).__name__}")


def build_wake_digest(items, total_pending=None, nonce=None):
    """One batched digest text for a wake (proposal §3): numbered items with
    agent/subject/priority, a "+N more" tail past the cap, and — per binding Q2 —
    a the operator-worthy CANDIDATE flag only (critical items). The push decision + any
    tg-notify call is gm's judgment, never this tool's."""
    items = [m for m in (items or []) if isinstance(m, dict)]
    if not items:
        return ""
    lines = []
    head = f"[wake] {len(items)} items need you"
    if nonce:
        head += f" (nonce {nonce})"
    lines.append(head + ":")
    for i, m in enumerate(items, 1):
        pri = (m.get("priority") or "").upper()
        lines.append(f"{i}) {m.get('from_agent', '?')} — {m.get('subject', '(no subject)')}"
                     + (f" [{pri}]" if pri else ""))
    if total_pending and total_pending > len(items):
        lines.append(f"(+{total_pending - len(items)} more pending)")
    crit = [m.get("from_agent", "?") for m in items if m.get("priority") == "critical"]
    if crit:
        lines.append("the operator-worthy candidate" + ("s" if len(crit) > 1 else "")
                     + f" (your call): {', '.join(crit)}")
    lines.append("Triage + act on these now.")
    return "\n".join(lines)


def confirm_turn_started(before, after):
    """Did the wake actually start a gm turn? (proposal §2.5 — delivered ≠ woken.)
    True on ANY of: status flipped idle->working/busy; jsonl turn count grew; bus
    emitted prompt_submit. Unverifiable = False (NOT confirmed) — the transport
    retries once, then marks wake_failed with a durable artifact; never loops."""
    try:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return False
        if before.get("status") == "idle" and \
                after.get("status") in ("working", "busy"):
            return True
        b_turns, a_turns = before.get("jsonl_turns"), after.get("jsonl_turns")
        if isinstance(b_turns, int) and isinstance(a_turns, int) and a_turns > b_turns:
            return True
        if after.get("last_event_type") == "prompt_submit" and \
                before.get("last_event_type") != "prompt_submit":
            return True
        return False
    except Exception:
        return False
