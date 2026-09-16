"""conformance — the provider-adapter conformance gate (DEC v2 §4, fail-closed arm).

the operator's rule: a provider is ARMABLE for ctx-fire ONLY if its adapter passes a fixed
suite. Adding a future provider = one adapter + PASS the suite, ZERO core changes.

Two tiers:
  * ARM-TIME gate (checkable synchronously, enforced at arm) — the adapter must return a
    FRESH, in-range, correctly-NORMALIZED ctx for the seat. An unknown runtime (no adapter
    in the registry) or a read that comes back absent/stale/out-of-range REFUSES the arm.
    This is the gap the demo-gemini-pred case exposes (the gemini reader does not surface
    that seat) — the fail-closed arm must catch it (hazard #3(ii)).
  * EFFECT-tier suite (validated by the 3 real demo fires, not a synchronous unit gate):
    spawn lands past the trust/onboarding dialog, sid captured, transcript readable,
    handoff inject works, death detected. CONFORMANCE_SUITE names them so the fires and
    verify_bg_fire.py can check each by effect per provider.

This module carries runtime-name PINS (codex 118899/258400 etc.) by design — it is
provider CONFIG, not the rotation core, so it is OUTSIDE the grep-enforced core files.
"""
import os as _os
import time as _time

from . import ctx_adapters as _ca

# v2.4 (DEC-1789456544274196): the ARM-TIME gate accepts a RETAINED last-valid in-range ctx for an
# IDLE seat, bounded by this max age (the arm gate is stricter than the mid-flight beat's finding #2,
# which is age-unbounded — enabling ctx-fire on a very stale ctx is unsafe). Env-overridable for
# tuning; the core constant is untouched by the override (same pattern as BG_TEST_*).
MAX_ARM_IDLE_CTX_AGE_S = 21600   # 6h


def _effective_max_arm_idle_age():
    raw = _os.environ.get("BG_ARM_IDLE_CTX_MAX_AGE_S")
    if raw is None:
        return MAX_ARM_IDLE_CTX_AGE_S
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MAX_ARM_IDLE_CTX_AGE_S
    return v if v >= 1 else MAX_ARM_IDLE_CTX_AGE_S


def default_seat_idle(seat):
    """Live default idle probe: the seat's state via agent-status (the SAME path collect/the beat
    use — deriver oracle with a screen-scrape fallback). True iff state=='idle'. Fail-soft to False
    (idle unconfirmed => the retained read is NOT accepted => fail-closed)."""
    try:
        import importlib.util as _ilu
        _p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.dirname(__file__)))), "scripts", "agent-status.py")
        _spec = _ilu.spec_from_file_location("agent_status_probe", _p)
        _m = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        return (_m.get_agent_status(seat) or {}).get("state") == "idle"
    except Exception:  # noqa: BLE001 — fail-soft to not-idle (fail-closed)
        return False

# gm PIN: one known raw (tokens, window) per provider whose adapter reads tokens, with the
# normalized fraction the CORE must see. The arm gate cross-checks the adapter's
# normalization against this so a provider cannot arm with a mis-scaled ctx (a correctness
# bug, not tuning). Providers whose reader is already a percent (the detector) need no pin.
CONFORMANCE_PINS = {
    "codex": {"tokens": 118899, "window": 258400},
    "gemini": {"tokens": 730000, "window": _ca.GEMINI_USABLE_WINDOW},
}

# The EFFECT-tier checks the demo fires + verify_bg_fire.py validate per provider (OQ-3).
CONFORMANCE_SUITE = (
    "read_ctx_fresh_in_range",      # arm-time (this module)
    "normalization_matches_pin",    # arm-time (this module, for token-based readers)
    "spawn_past_onboarding",        # effect (fire)
    "sid_captured",                 # effect (fire)
    "transcript_readable",          # effect (fire)
    "handoff_inject_submitted",     # effect (fire) — GOAL-FINDING #3 typed-not-submitted
    "death_detected",               # effect (fire)
)


def normalization_matches_pin(runtime):
    """For a token-based reader, verify the adapter's normalization reproduces the pinned
    fraction. Providers with no pin (percent readers) trivially pass. Returns (ok, reason)."""
    pin = CONFORMANCE_PINS.get((runtime or "").strip().lower())
    if pin is None:
        return (True, "no-pin-required")
    got = _ca.normalize_from_tokens(pin["tokens"], pin["window"])
    want = pin["tokens"] / pin["window"]
    if got is None or abs(got - want) > 1e-9:
        return (False, f"normalization mismatch: got {got} want {want}")
    return (True, "pin-ok")


def is_arm_conformant(runtime, seat, *, idle=False, retained_ctx_fn=None, arm_now=None,
                      max_idle_age_s=None, read_ctx_fn=None, **kw):
    """The ARM-TIME fail-closed gate. Returns (bool, reasons list).

    Conformant when read_ctx is FRESH + in-range (unchanged), OR — v2.4 — when the seat is IDLE and
    a RETAINED last-valid in-range ctx (``retained_ctx_fn`` -> (pct, ts), the bg_state meta the
    beat's finding #2 uses) is within ``max_idle_age_s`` (default MAX_ARM_IDLE_CTX_AGE_S, 6h); that
    arm carries reason ``retained-idle``. Refuses (False) when: unknown runtime; normalization
    mis-scaled; busy + not-fresh; idle but no retained value / out-of-range / older than the bound.
    ``read_ctx_fn`` (default the live registry read) is injected in tests.
    """
    rt = (runtime or "").strip().lower()
    if rt not in _ca.CTX_ADAPTER_REGISTRY:
        return (False, [f"unknown-runtime:{rt or '<empty>'}:no-adapter"])
    # normalization is a CORRECTNESS gate (a mis-scaled adapter must never arm) — refuse regardless
    # of freshness / the retained path.
    ok_norm, why_norm = normalization_matches_pin(rt)
    if not ok_norm:
        return (False, [why_norm])
    read = read_ctx_fn or _ca.read_ctx
    pct, fresh = read(rt, seat, **kw)
    if fresh and pct is not None and 0.0 <= pct <= 1.0:
        return (True, ["read_ctx_fresh_in_range", "normalization_matches_pin"])
    if fresh and pct is not None and not (0.0 <= pct <= 1.0):
        return (False, [f"read_ctx-out-of-range:{pct}"])
    # v2.4 IDLE-RETAINED acceptance: the live read is not fresh. For an IDLE seat whose ctx cannot
    # have moved since its last render, accept the RETAINED last-valid in-range value (bg_state meta),
    # bounded by max age. A BUSY seat (ctx moving) or one without a retained value still refuses.
    if idle and retained_ctx_fn is not None:
        now_ = _time.time() if arm_now is None else arm_now
        max_age = _effective_max_arm_idle_age() if max_idle_age_s is None else max_idle_age_s
        try:
            rpct, rts = retained_ctx_fn()
        except Exception:  # noqa: BLE001
            rpct, rts = None, None
        if rpct is None or rts is None:
            return (False, [f"idle-no-retained:{seat}"])
        if not (0.0 <= rpct <= 1.0):
            return (False, [f"retained-out-of-range:{rpct}"])
        age = now_ - rts
        if age <= max_age:
            return (True, [f"retained-idle(age={int(age)}s<={int(max_age)}s)",
                           "normalization_matches_pin"])
        return (False, [f"retained-idle-too-old:{int(age)}s>{int(max_age)}s"])
    return (False, [f"read_ctx-not-fresh:{seat}"])
