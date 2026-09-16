"""decide_bg — the Blue-Green transition decision (stage-3). PURE, no effects.

Separate from the live ``decide.py`` (unchanged, so the existing rotation path is
untouched while stage-3 lands INERT). This maps one lineage's WAL-derived
observations to a Blue-Green action + REASON TOKEN. All downstream gating keys on
the reason token, never the action (the PLUG2 lesson).

Ladder (spec §3.2):
  * ``death:*``     -> swap, DOMINATES ctx, NEVER gated (a successor + the WAL
    beats a dead canonical; a dying seat never waits on verification/approval);
  * ``ctx ≥ 0.80``  -> swap;
  * ``ctx ≥ 0.70``  -> prewarm;
  * else            -> noop.

Calibration fail-closed (v2, data-driven, no runtime names): an UNCALIBRATED ctx
ceiling — a seat whose adapter cannot present a fresh, in-range normalized ctx —
pins the lineage to SOLO + alarm on the ctx trigger; never guess a ceiling and swap
early/late. Death still dominates (a dead agent is rescued regardless of calibration).
"""

import os

PREWARM_AT = 0.70
SWAP_AT = 0.80


def _effective_thresholds():
    """B5 REACHABLE-TRIGGER (test-only, gm PLAN GATE): env BG_TEST_PREWARM_AT /
    BG_TEST_SWAP_AT let a SUPERVISED autonomous beat fire the REAL decision path on a
    low-ctx demo seat WITHOUT touching the core PREWARM_AT/SWAP_AT constants. ABSENT or
    malformed env => the core constants (INERT in production — nothing sets these in the
    fleet cron). Returns (prewarm_at, swap_at)."""
    def _f(name, default):
        v = os.environ.get(name)
        if v is None:
            return default
        try:
            return float(v)
        except ValueError:
            return default
    return _f("BG_TEST_PREWARM_AT", PREWARM_AT), _f("BG_TEST_SWAP_AT", SWAP_AT)
# P1.5 lull band: within [PREWARM_AT, SWAP_AT) a swap may fire EARLY once Blue has been
# idle at least this long (seconds), so the cutover lands at a lull instead of the 0.80
# wall (which can fire mid-turn and lose Blue's in-flight WAL-uncommitted work).
LULL_MIN_IDLE_S = 300
# DEC-1789517918317482 L3 (finished-and-waiting): Blue idle with turn complete for one full
# beat AND >= 1 PENDING approval card it authored => swap (no ctx floor). Age is OBSERVED
# idle (bg_beat persists idle_since; never inferred backwards).
CARD_WAIT_MIN_IDLE_S = 60


def _l2l3_guard(obs):
    """Fail-closed guards for the two idle-driven swap legs (L2 lull, L3 card-wait); NEVER
    applied to L1 (ctx ceiling) or death. Returns a suppress reason token or None.
    G3 attached: the operator is attached to Blue's pane (unknown => attached).
    G1 composer: unsubmitted text in Blue's composer, or the composer is unreadable
    (None) => a swap could lose work => suppress.
    A guard keys on a PRESENT obs field: the live build_obs always supplies both (fail-closed
    defaults: attached=True, composer=None); a legacy obs without the keys is a pre-DEC
    caller/fixture and keeps the pre-DEC lull behaviour (unguarded)."""
    if "blue_attached" in obs and obs["blue_attached"]:
        return "suppress:attached"
    if "blue_composer_text" in obs and (obs["blue_composer_text"] or "") != "":
        return "suppress:composer"
    if "blue_composer_text" in obs and obs["blue_composer_text"] is None:
        return "suppress:composer"
    return None


def decide_bg(obs: dict) -> dict:
    root = obs.get("root")
    death = obs.get("death")
    state = obs.get("state")
    ctx_unknown = bool(obs.get("ctx_unknown"))
    ctx_pct = obs.get("ctx_pct", 0.0) or 0.0
    calibrated = obs.get("ceiling_calibrated", True)

    # leg (i) — SUPPRESS-ON-LIVE≠CANONICAL (gm ruling, adopts blocker_surface_watchdog
    # R2 skip-on-mismatch). build_obs sets blue_sid_live_mismatch when blue's LIVE pane
    # occupant sid != the canonical seat sid: a FRESH successor already took blue's seat
    # out-of-band. ANY swap now — ctx OR death — would promote our prewarmed green OVER
    # that live successor = a clobber (promote-races-auto-respawner class). Safe-direction:
    # a MISSED swap is harmless, a WRONG swap clobbers a live seat. So skip the swap
    # entirely; this is checked BEFORE death because a dying blue whose seat is already
    # succeeded has nothing left to rescue here (R2 does not exempt death). alarm=True so
    # the race surfaces; downstream (bg_arm) records a durable breadcrumb of the skip.
    if obs.get("blue_sid_live_mismatch"):
        return {"root": root, "action": "noop",
                "reason": "suppress:live-sid-mismatch", "never_gated": False,
                "alarm": True}

    # death dominates everything and is NEVER gated.
    if death:
        return {"root": root, "action": "swap", "reason": f"death:{death}",
                "never_gated": True, "alarm": False}

    # fail-closed on the ctx trigger for an uncalibrated runtime ceiling.
    if not calibrated:
        return {"root": root, "action": "noop",
                "reason": "uncalibrated:solo-alarm", "never_gated": False,
                "alarm": True}

    # P0.6 blind-telemetry: a wholly-UNKNOWN ctx (no live read, no retained value) is a
    # noop+ALARM, NEVER a silent ctx:0.00 noop that hides the blind seat. A retained
    # (stale) last-valid IS actionable but the alarm is raised so it surfaces.
    if ctx_unknown and obs.get("ctx_pct") is None:
        return {"root": root, "action": "noop", "reason": "ctx:unknown-alarm",
                "never_gated": False, "alarm": True}

    prewarm_at, swap_at = _effective_thresholds()  # core constants unless a test override
    guard = _l2l3_guard(obs)
    idle_age = obs.get("state_age_s") or 0
    if ctx_pct >= swap_at:
        action, reason = "swap", "ctx:swap"        # hard backstop (never waits on a lull)
    elif (state == "idle" and obs.get("blue_turn_complete", state == "idle")
          and idle_age >= CARD_WAIT_MIN_IDLE_S
          and (obs.get("blue_pending_cards") or 0) >= 1):
        # DEC-1789517918317482 L3: Blue finished its turn and is waiting on a published
        # card it authored — the promoted Green will process the operator's answer. No ctx floor;
        # never_gated stays False so P0.5's READY-gate still requires a verified-READY Green
        # (L3 never boots one). Guards G1/G3 => noop with the suppress token.
        if guard:
            return {"root": root, "action": "noop", "reason": guard,
                    "never_gated": False, "alarm": False}
        action, reason = "swap", "card:wait-swap"
    elif ctx_pct >= prewarm_at:
        # P1.5 lull band: swap EARLY at an idle lull (the operator contract #4) instead of the
        # 0.80 wall. never_gated stays False so P0.5's READY-gate still requires the Green
        # to be verified-READY before this actually fires; otherwise it defers to prewarm.
        # A tripped G1/G3 guard suppresses the lull SWAP only — prewarm cannot lose work.
        if state == "idle" and idle_age >= LULL_MIN_IDLE_S and guard is None:
            action, reason = "swap", "ctx:lull-swap"
        else:
            action, reason = "prewarm", "ctx:prewarm"
    else:
        action, reason = "noop", f"ctx:{ctx_pct:.2f}"

    # GOAL-FINDING #2 idle-ceiling: a retained (stale) read on an IDLE seat is TRUSTED
    # (its ctx cannot have moved) — do not raise the blind-telemetry alarm for it. A busy
    # seat's stale read never reaches here (build_obs leaves it uncalibrated -> solo-alarm
    # above), and a wholly-unknown ctx already alarmed via the ctx:unknown-alarm branch.
    return {"root": root, "action": action, "reason": reason,
            "never_gated": False, "alarm": ctx_unknown and state != "idle"}
