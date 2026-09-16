"""green_wake — GOAL-FINDINGS #3 (confirm-started) + #4 (wake-green-to-ingest), v2.1
addendum. Provider-agnostic primitives (ZERO runtime-name literals): the caller injects
the provider adapter's inject_text / transcript-probe / Enter-resend, so the SAME logic
serves every runtime.

#3 confirm-started: an injected turn is not "done" when it is TYPED — the Ink composer can
swallow the Enter (a long inject) or need a 2nd Enter (a collapsed paste). Confirm the turn
ACTUALLY STARTED by effect (transcript has activity), with a BOUNDED Enter-resend, and a
LOUD failure if it never starts.

#4 wake-green: msg_store delivers the hydrate digest durably but NEVER wakes an idle green
([[feedback_msg_store_does_not_wake_idle_agent]]). So the beat must WAKE the green
(inject "ingest…") — reusing #3's confirm-started — and gate READY on ingestion PROVED by
effect (green_boot_probe through_seq >= delivered H), never on mere digest-delivery.
"""

# #3: bounded Enter-resends for the Ink submit-race before declaring the turn dead (LOUD).
CONFIRM_STARTED_MAX_RETRIES = 3


class GreenDidNotStart(Exception):
    """GOAL-FINDING #3: an injected turn (spawn init OR the #4 ingest wake) never started
    by effect after the bounded Enter-resends — the Ink submit-race lost the turn. Raise
    LOUD (never a silent typed-not-submitted composer sitting idle at SOLO)."""


def confirm_turn_started(probe_started_fn, reenter_fn, *,
                         max_retries=CONFIRM_STARTED_MAX_RETRIES, sleep_fn=None):
    """GOAL-FINDING #3. Confirm an injected turn ACTUALLY started by effect.

    ``probe_started_fn() -> bool`` is the by-effect check (transcript jsonl has >=1 new
    line / visible activity for the captured sid). If not started, ``reenter_fn()`` re-sends
    Enter (the Ink submit-race fix), up to ``max_retries`` times. Returns True once started,
    else False after the last resend (the caller raises GreenDidNotStart / alarms LOUD).
    A pane merely EXISTING is NOT proof — only a started turn is."""
    sleep_fn = sleep_fn or (lambda: None)
    for attempt in range(max_retries):
        if probe_started_fn():
            return True
        reenter_fn()          # Ink submit-race: re-send Enter
        sleep_fn()
    return bool(probe_started_fn())   # final by-effect check after the last resend


def wake_green_to_ingest(inject_fn, probe_started_fn, reenter_fn, *,
                         ingest_text="ingest your hydrate digest, then hold",
                         max_retries=CONFIRM_STARTED_MAX_RETRIES, sleep_fn=None,
                         probe_idle_fn=None, already_sent_fn=None, mark_sent_fn=None):
    """GOAL-FINDING #4 (wake half). WAKE the idle green to ingest its delivered hydrate
    digest (``inject_fn(text)`` via the provider adapter), then CONFIRM the wake turn
    actually started (#3). Returns True if the ingest turn started; False => caller alarms
    LOUD (the green never woke; READY must stay unreachable, never a silent stuck-PREWARM).

    (c) IDEMPOTENT ACROSS BEATS. ``_wake_and_verify_ingest`` runs EVERY PREWARMING beat, so a
    naive re-inject queues a DUPLICATE ingest prompt behind the green's boot turn each beat
    (the live re-fire queued 6). Guard it with injected cross-beat state:
      * ``already_sent_fn()`` True => the ingest text was delivered on a PRIOR beat, so NEVER
        re-inject it. If the turn already STARTED, return True (done). Else RESEND (a bounded
        Enter nudge) ONLY when ``probe_idle_fn()`` says the green is IDLE (boot turn ended /
        empty composer) — a busy green DEFERS silently (no nudge, no duplicate), retry next.
      * first beat (not already sent): inject ONCE, ``mark_sent_fn()``, confirm-started.
    ``probe_idle_fn`` None => idle-unknown => treated as idle (legacy: the single-shot waker,
    unchanged for existing callers/tests). ``already_sent_fn`` None => always the first-send
    path (legacy single call)."""
    already = bool(already_sent_fn()) if already_sent_fn is not None else False
    if not already:
        inject_fn(ingest_text)                 # the ONE wake send
        if mark_sent_fn is not None:
            mark_sent_fn()
        return confirm_turn_started(probe_started_fn, reenter_fn,
                                    max_retries=max_retries, sleep_fn=sleep_fn)
    # Already sent on a prior beat — never re-inject the text (no duplicate).
    if probe_started_fn():
        return True                            # the ingest turn is under way: done
    idle = probe_idle_fn() if probe_idle_fn is not None else True
    if not idle:
        return False                           # busy: wait for idle, no resend this beat
    # idle + not started: the Ink submit-race may have swallowed the send — a bounded Enter
    # nudge lands on the now-empty composer (never a queued duplicate text).
    return confirm_turn_started(probe_started_fn, reenter_fn,
                                max_retries=max_retries, sleep_fn=sleep_fn)


def ingestion_verified(ingested_through_fn, green_alias, required_h):
    """GOAL-FINDING #4 (verify half). READY only when the green PROVABLY ingested through
    the delivered hydrate H. FAIL-CLOSED: no H delivered yet, an unreadable probe, or
    through_seq < H => False (not ready). Never READY on digest-delivery alone."""
    if required_h is None:
        return False
    try:
        v = ingested_through_fn(green_alias)
    except Exception:  # noqa: BLE001 — fail-closed: an unreadable ingest is NOT verified
        return False
    return v is not None and v >= required_h
