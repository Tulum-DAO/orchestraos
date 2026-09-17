"""Fleet-beat driver — the tier-scoped loop over the fleet (WS3 arming, wave 1).

execute_rotation + rotation_beat are single-agent by construction. This driver is
the loop that runs the beat across the fleet on a schedule — the LIVE-REACHABLE
step. Its SCHEDULING (cron/@reboot/persistent proc) is a MOVED SURFACE and is
cleared with gm BEFORE it is enabled (gm's gate precedes ob's sequencing on
live-reachable steps). This module is build+test only; nothing here installs cron
or arms by itself — `armed_tiers` defaults to EMPTY (pure dry-run).

Wave-1 policy (the operator-approved apr_034667cb, red-team scope):
  * T2 lineages ONLY are armed (armed_tiers={"T2"}): their beat runs the full
    decide -> author-gate -> execute_rotation(confirm_fn=S3 two-sample) ->
    hold-ledger under the rotation lock, with the DOUBLE-gated kill intact.
  * T0/T1 stay OBSERVE-ONLY: the driver computes their decision + logs it but takes
    NO armed action; a T0/T1 predecessor kill is a per-rotation the operator one-tap via the
    surface-decision lane (NOT wired here — a later wave).
  * EVERY agent gets a dry-run log line every beat (observability); only armed-tier
    agents additionally get armed execution.

Pure over injected seams (decisions come from the same decide(); the per-agent beat
is rotation_beat; locks/ledger are threaded + returned for the daemon to persist).
"""

import os

from scripts.lineage_daemon import beat as _beat
from scripts.lineage_daemon.collect import beat_skip_reason
from scripts.lineage_daemon.ctxstate import context_pct
from scripts.lineage_daemon import hold_ledger as _hledger
from scripts.lineage_daemon import complete as _complete

# The observe/armed disposition for one agent, before any action.
OBSERVE_TIER = "observe:tier-not-armed"     # T0/T1 in wave 1 (or any non-armed tier)
OBSERVE_NOOP = "observe:noop"               # healthy ctx — nothing to do
ARMED = "armed"                             # tier is armed AND the agent is actionable

# --- gm-required guards (bake in BEFORE the cron is even proposed) -------------
# The disabled-sentinel: if this file exists, plan_fleet is an INSTANT pure no-op —
# gm's + the operator's emergency brake (guard 3). DEFENSE-IN-DEPTH (gm SAFETY-CRITICAL ruling
# 2026-08-15, msg_6211db73): a file-based e-brake in the SYNCED tree is unsafe — a
# stale Mac copy can resurrect a rm'd sentinel OR delete a freshly-touched one via
# Syncthing (silently un-braking in an emergency). Fix = BOTH:
#   (A) the in-tree path is now .stignore'd (VPS-local, sync won't touch it), AND
#   (B) the CANONICAL e-brake is a NON-SYNCED runtime path (~/runtime/), structurally
#       immune to Syncthing regardless of .stignore correctness/propagation.
# kill_switch_engaged is disabled if EITHER exists (fail-toward-braked). Operators
# touch the runtime path for a guaranteed-sticky brake.
KILL_SWITCH_FILE = os.path.join("state", "FLEET_BEAT_DISABLED")   # in-tree (.stignore'd)
KILL_SWITCH_BASENAME = "FLEET_BEAT_DISABLED"


def _runtime_dir():
    """The NON-SYNCED runtime dir (CLAUDE.md: ~/runtime lives OUTSIDE Syncthing).
    Overridable via ORCH_RUNTIME_DIR (tests point it at a tmp dir)."""
    return os.environ.get("ORCH_RUNTIME_DIR", os.path.expanduser("~/runtime"))
# Guard 4: agents that must NEVER be rotated by the beat, checked EVERY beat (not
# just wave-1) — the operator-P1 / live-fixture agents. Extendable via the exclude= arg.
DEFAULT_EXCLUDE = frozenset({"orchestraos-app-dev-v8", "handoff-red-team",
                             # F17: alive-quiescent predecessor (the operator-directed
                             # no-retire); soft-handoff trigger re-pings forever
                             # post-completion — excluded until should_ping checks
                             # the handoff/succeeded_by/comprehension artifacts.
                             "initiative-pm-architect"})
# Guard 1: at most this many ARMED rotations execute per beat; the rest are logged
# and deferred to the next beat (never mass-rotate the fleet in one beat).
MAX_ROTATIONS_PER_BEAT = 1

# --- gm(gen-8) COUNTER_PROPOSE conditions on DEC-1786779462 (accepted) ---------
# Condition 2a: an HOURLY cap across beats — per-beat=1 still allows ~12 kills/hr at
# a 5-min cadence; this bounds unattended kills per rolling hour regardless of beat.
MAX_ROTATIONS_PER_HOUR = 4
ROTATIONS_HOUR_WINDOW_S = 3600
# Condition 2b: after a lineage is retired, no rotation of THAT lineage for a
# cooldown window (flap protection — a just-rotated lineage can't immediately churn).
POST_RETIRE_COOLDOWN_S = 1800
# Condition 3: the FIRST live cron cadence (the crontab interval, surfaced in the
# proposal + honored by the cron entry — not enforced inside plan_fleet).
FIRST_CADENCE_MIN = 15

# beat-level skip/disposition reasons (effects, not prose).
SKIP_KILL_SWITCH = "skip:kill-switch-engaged"      # guard 3
SKIP_EXCLUDED = "skip:excluded"                     # guard 4
SKIP_OPEN_HOLD = "skip:open-hold"                   # guard 2
SKIP_CAP_DEFERRED = "skip:rotation-cap-deferred"    # guard 1 (per-beat)
SKIP_SOFT_ONLY = "skip:soft-only-window"            # cond 1: hard_rotate deferred to human
SKIP_HOURLY_CAP = "skip:hourly-rotation-cap"        # cond 2a
SKIP_LINEAGE_COOLDOWN = "skip:post-retire-cooldown"  # cond 2b
SKIP_SELF_TRIGGERED = "skip:self-triggered"         # backstop: primary self-rotation handled it
# Piece 2 (DEC-1787687601): the "which seats does the beat evaluate at all" gate.
SKIP_NON_CLAUDE_RUNTIME = "skip:non-claude-runtime"  # 2c: codex/agy — no rotation adapter
SKIP_SUPERSEDED_SEAT = "skip:superseded-seat"        # 2b: retired/quiescent/succeeded_by predecessor
# Per-lineage arm gate (self-retire arming allowlist): when soft_only=False, a
# hard_rotate proceeds ONLY if the seat's lineage_root is in self_retire_armed.
# A seat whose lineage is NOT armed keeps deferring (as if soft_only). This scopes
# the auto-ROTATE/spawn half the same way is_graduated_autoretire (self_retire_gate)
# already scopes the auto-RETIRE half — arming = adding one lineage to the allowlist.
# FAIL-CLOSED: an empty/missing armed set => NO lineage armed => every hard_rotate
# defers (matches self_retire_gate._lineage_armed's absent-file => not-armed default).
SKIP_NOT_ARMED = "skip:lineage-not-armed"


def kill_switch_engaged(orchestra_dir=None) -> bool:
    """True iff the AUTHORITATIVE e-brake sentinel exists — an instant, dependency-free
    brake (guard 3).

    HARDENED (gm msg_b8f1c614): the ONLY authoritative brake is the NON-SYNCED runtime
    path (~/runtime/FLEET_BEAT_DISABLED). The in-tree state/ path is ADVISORY ONLY — it
    is a synced file, so a stale Mac copy can spuriously TOGGLE it in EITHER direction
    (a VPS .stignore does not stop the Mac announcing the file). Hard-braking on it let
    a stale sync spuriously STOP the live beat; conversely a stale delete could
    spuriously un-brake. So the beat brakes ONLY on the runtime sentinel; the in-tree
    path is surfaced via kill_switch_advisory() for a logged warning, never a hard stop.
    Operators use the runtime path as the real e-brake."""
    runtime = os.path.join(_runtime_dir(), KILL_SWITCH_BASENAME)
    return os.path.exists(runtime)


def kill_switch_advisory(orchestra_dir=None) -> bool:
    """True iff the in-tree (synced, advisory) sentinel is present. NOT a brake — the
    caller logs a warning (a stale synced sentinel is present; the authoritative brake
    is ~/runtime). Lets an operator SEE a spurious synced file without it stopping the
    beat."""
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    return os.path.exists(os.path.join(od, KILL_SWITCH_FILE))


def _agent_ctx_pct(agent):
    """Context fraction (0..1) for ranking actionable agents; None -> -1 (last)."""
    p = context_pct(agent.get("ctx", {}) or {})
    return p if p is not None else -1.0


def _has_open_hold(ledger, aid):
    """True iff `aid` is the canary of an unresolved HOLD (guard 2 — never re-act on
    a hold-ledgered agent; it is already in a two-heads state awaiting resolution)."""
    return any(h.get("canary") == aid for h in _hledger.open_holds(ledger or {}))


def _open_hold_for(ledger, aid):
    """The first unresolved HOLD row whose canary is `aid` (or None) — the completion
    branch (D3) resumes THIS held rotation on a later beat."""
    for h in _hledger.open_holds(ledger or {}):
        if h.get("canary") == aid:
            return h
    return None


# --- self-trigger seam (PB-locked): mark dir + classifier factory + clearer ---
SELF_TRIGGER_DIR = os.path.join("state", "rotation-self-triggers")
# max_age_s co-set with platform-builder (msg co-sign 2026-08-15): must exceed a full
# author->spawn->orient->cite-back->confirm->cooldown->two-sample rotation envelope so
# a slow-but-ALIVE self-rotating agent isn't misread as wedged; < 3600 so a genuinely
# wedged agent is still caught within ~45min.
SELF_TRIGGER_MAX_AGE_S = 2700


def _mark_path(session_id, orchestra_dir=None):
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    return os.path.join(od, SELF_TRIGGER_DIR, f"{session_id}.json")


def self_trigger_classifier(sid_of, *, now, orchestra_dir=None,
                            max_age_s=SELF_TRIGGER_MAX_AGE_S,
                            crossed_of=None):
    """Build the self_trigger_fn(agent_id) -> verdict the beat consumes. Reads PB's
    durable mark state/rotation-self-triggers/<sid>.json and CALLS PB's
    rotation_signal.classify (never re-derives — the two halves can't drift).

    Seams: sid_of(agent_id)->session_id (resolve the beat's canonical agent back to
    the session_id the hook keyed the mark by); crossed_of(agent_id)->bool (did this
    agent cross the rotation threshold — defaults True since the beat only calls this
    for agents decide() already found actionable). Fail-toward-nudge: an unreadable/
    absent mark classifies WEDGED (never silently skips a possibly-wedged agent)."""
    import json as _json
    from scripts.focus_registry.rotation_signal import classify

    def _fn(agent_id):
        crossed = crossed_of(agent_id) if crossed_of else True
        sid = sid_of(agent_id)
        mark = None
        if sid:
            try:
                with open(_mark_path(sid, orchestra_dir)) as fh:
                    mark = _json.load(fh)
            except Exception:  # noqa: BLE001 -- absent/unreadable -> classify WEDGED
                mark = None
        return classify(crossed, mark, now, max_age_s=max_age_s)

    return _fn


def clear_self_trigger_mark(session_id, orchestra_dir=None) -> bool:
    """Delete the self-trigger mark for a session (ob owns this at retire, seam Q4).
    Returns True if a mark was removed. Idempotent: absent mark -> False, no error."""
    try:
        os.remove(_mark_path(session_id, orchestra_dir))
        return True
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        return False


def _recent_retires(history, now, window_s=ROTATIONS_HOUR_WINDOW_S):
    """The retire records within the rolling window (cond 2a hourly cap)."""
    return [r for r in (history or {}).get("retires", [])
            if (now - r.get("at", 0)) < window_s]


def _lineage_in_cooldown(history, lineage_root, now, cooldown_s=POST_RETIRE_COOLDOWN_S):
    """True iff THIS lineage was retired within the post-retire cooldown (cond 2b)."""
    for r in (history or {}).get("retires", []):
        if r.get("lineage_root") == lineage_root and (now - r.get("at", 0)) < cooldown_s:
            return True
    return False


def _record_retire(history, lineage_root, now):
    """Append a retire record. Returns a NEW history dict (pure)."""
    rows = list((history or {}).get("retires", []))
    rows.append({"lineage_root": lineage_root, "at": now})
    return {**(history or {}), "retires": rows}


def _default_armed_lineages():
    """The self-retire arming allowlist as a set of lineage roots, read via
    self_retire_gate's canonical reader (~/runtime/self_retire_armed; one lineage
    root per line, `#` comments + blanks ignored). REUSES the SAME source + parse
    that is_graduated_autoretire gates the auto-RETIRE half on — so a single
    greppable file scopes BOTH halves. Absent/unreadable file => EMPTY set =>
    fail-CLOSED (no lineage armed => every hard_rotate defers). Never reimplement the
    parse: we call the gate's own _lineage_armed per-lineage semantics by reading the
    file through the same path constant."""
    from scripts.lineage_daemon import self_retire_gate as _srg
    try:
        with open(_srg._DEFAULT_ARMED_PATH) as f:
            lines = f.read().splitlines()
    except OSError:
        return frozenset()
    return frozenset(
        s for s in (ln.strip() for ln in lines) if s and not s.startswith("#"))


def _tier_class(agent, registry):
    """The agent's registry tier_class (T0/T1/T2). Prefer the live agent dict's
    tier_class, else the registry entry, else 'T2' (a missing tier is treated as the
    least-privileged armed tier only if it is in armed_tiers — otherwise observed)."""
    if agent.get("tier_class"):
        return agent["tier_class"]
    entry = (registry or {}).get("agents", {}).get(agent.get("agent_id"), {})
    return entry.get("tier", "T2")


def plan_fleet(agents, registry, *, armed_tiers=frozenset(), now,
               locks=None, ledger=None, history=None, beat_fn=None,
               exclude=DEFAULT_EXCLUDE, max_rotations=MAX_ROTATIONS_PER_BEAT,
               soft_only=False, max_rotations_per_hour=MAX_ROTATIONS_PER_HOUR,
               post_retire_cooldown_s=POST_RETIRE_COOLDOWN_S,
               self_trigger_fn=None, armed_lineages=None,
               completion_provider=None,
               orchestra_dir=None, **beat_kwargs):
    """Run ONE fleet beat. Returns {beats, locks, ledger, history, summary, disabled}.

    GUARDS (gm-required, enforced HERE before any armed action):
      3. KILL-SWITCH: state/FLEET_BEAT_DISABLED present -> INSTANT pure no-op.
      4. EXCLUDE: any agent in `exclude` skipped every beat, never armed.
      2. OPEN-HOLD: agent with an unresolved HOLD is skipped.
      1. MAX-ROTATIONS/BEAT: <= `max_rotations` hard_rotate per beat (highest-ctx first).

    gm(gen-8) COUNTER_PROPOSE conditions on DEC-1786779462 (accepted, all TIGHTEN):
      C1. `soft_only`=True -> EVERY hard_rotate is deferred (SKIP_SOFT_ONLY) to a
          human 2nd go; reversible SOFT author-triggers still fire. The first live
          cron window runs soft_only; hard_rotate arms only on a 2nd explicit the operator go.
      C2a. HOURLY cap: <= `max_rotations_per_hour` retires per rolling hour ACROSS
          beats (per-beat=1 still allows ~12/hr at 5-min; this bounds the real rate).
      C2b. POST-RETIRE COOLDOWN: a lineage retired within `post_retire_cooldown_s`
          is skipped (flap protection — no immediate re-churn of a just-rotated line).
      C3. first cadence = FIRST_CADENCE_MIN (crontab interval; honored by the cron
          entry, surfaced in the proposal — not enforced inside plan_fleet).

    PER-LINEAGE ARM GATE (self-retire arming allowlist): when `soft_only`=False, a
    hard_rotate proceeds ONLY if the seat's lineage_root is in `armed_lineages`; a
    seat whose lineage is NOT armed defers (SKIP_NOT_ARMED, no spawn) — same effect
    as soft_only, but per-lineage. This scopes the auto-ROTATE/spawn half the way
    self_retire_gate.is_graduated_autoretire already scopes the auto-RETIRE half:
    arming = adding one lineage root to ~/runtime/self_retire_armed. `armed_lineages`
    is injectable (a set/frozenset of lineage roots); None => the default reader
    (self_retire_gate's allowlist file). FAIL-CLOSED: an EMPTY/missing armed set =>
    NO lineage armed => EVERY hard_rotate defers (the safe default that must hold).

    `history` ({"retires":[{lineage_root,at}]}) THREADS the hourly cap + cooldown
    across beats; the daemon persists it. `armed_tiers` defaults EMPTY -> pure dry-run.
    """
    beat_fn = beat_fn or _beat.rotation_beat
    locks = locks or {"rotations": []}
    ledger = ledger or {"holds": []}
    history = history or {"retires": []}
    exclude = set(exclude or ())
    beats = []
    summary = {"total": 0, "armed_run": 0, "observed": 0, "actionable": 0,
               "held": 0, "rotated": 0, "skipped_excluded": 0,
               "skipped_open_hold": 0, "deferred_cap": 0, "skipped_soft_only": 0,
               "skipped_hourly_cap": 0, "skipped_cooldown": 0,
               "skipped_self_triggered": 0, "skipped_non_claude_runtime": 0,
               "skipped_superseded_seat": 0, "skipped_not_armed": 0}

    # Per-lineage arm gate source (seam): None => read the canonical self-retire
    # arming allowlist (~/runtime/self_retire_armed) via self_retire_gate. An
    # absent/unreadable file => EMPTY set => FAIL-CLOSED (every hard_rotate defers).
    # Resolved once per beat so the file is read at most once. A caller can inject an
    # explicit set (tests / future wiring) to bypass the file.
    armed_lineages = (armed_lineages if armed_lineages is not None
                      else _default_armed_lineages())

    # GUARD 3 — kill-switch: instant pure no-op, before ANY agent is read/acted.
    # Authoritative = the non-synced runtime sentinel ONLY (gm msg_b8f1c614). The
    # in-tree synced path is ADVISORY: surfaced in the result for a logged warning, but
    # it does NOT stop the beat (a stale sync must not spuriously start/stop it).
    advisory = kill_switch_advisory(orchestra_dir)
    if kill_switch_engaged(orchestra_dir):
        return {"beats": [], "locks": locks, "ledger": ledger, "history": history,
                "summary": {**summary, "total": len(agents)},
                "disabled": True, "disabled_reason": SKIP_KILL_SWITCH,
                "advisory_sentinel": advisory}

    # Pass 1: PURE classification (decide() only) + a log line. Must take NO action —
    # earlier this called beat_fn, which for a hard_rotate agent executes a REAL
    # rotation (register+spawn+...); that side-effect belongs in pass-2 (under the
    # guards), never in the dry classification. decide() is the pure classifier.
    plans = []
    for agent in agents:
        aid = agent.get("agent_id")
        tier = _tier_class(agent, registry)
        summary["total"] += 1
        armed_this = tier in armed_tiers
        # Runtime gate FIRST (operator ruling 2026-09-17: blue-green default ON for Claude;
        # Gemini/Codex experimental, never armed unless [rotation] experimental_runtimes
        # opts in). Applied before actionability so an experimental seat is logged as
        # skip:non-claude-runtime on EVERY beat, ctx known or not, and is never "ARMED".
        runtime_skip = beat_skip_reason(agent) in ("non-claude-runtime", "unsupported-runtime")
        if runtime_skip:
            armed_this = False
        d = _beat.decide(agent)
        action = d["action"]
        if action == "noop":
            disposition = _beat.NOOP
        elif action == "soft_handoff":
            disposition = _beat.SOFT_AUTHORING     # pass-2 runs the real author gate
        else:                                       # hard_rotate
            disposition = "beat:hard-pending"       # pass-2 executes under guards
        actionable = action != "noop"
        if actionable:
            summary["actionable"] += 1
        dry = {"status": disposition, "action": action, "reason": d["reason"]}
        entry = {"agent_id": aid, "tier": tier, "dry_status": disposition,
                 "action": action, "reason": d["reason"],
                 "armed": armed_this, "ctx_pct": _agent_ctx_pct(agent),
                 "lineage_root": (registry or {}).get("agents", {}).get(aid, {})
                     .get("lineage_root") or aid,
                 "log": _log_line(aid, tier, dry, armed_this)}
        if runtime_skip:
            entry["armed_status"] = SKIP_NON_CLAUDE_RUNTIME
            summary["skipped_non_claude_runtime"] += 1
            actionable = False
        plans.append((agent, entry, actionable))

    # GUARD 1 — rank armed+actionable hard_rotate by ctx desc; top `max_rotations`
    # per beat may execute the kill-capable path (excludes/holds pre-filtered).
    hard_ranked = sorted(
        [e for (_a, e, act) in plans
         if act and e["armed"] and e["action"] == "hard_rotate"
         and e["agent_id"] not in exclude
         and not _has_open_hold(ledger, e["agent_id"])],
        key=lambda e: e["ctx_pct"], reverse=True)
    allowed_hard = {e["agent_id"] for e in hard_ranked[:max_rotations]}

    # C2a hourly budget: how many more retires are allowed this rolling hour.
    hourly_remaining = max_rotations_per_hour - len(
        _recent_retires(history, now, ROTATIONS_HOUR_WINDOW_S))

    # D10 shared per-beat identity-swap budget: completions AND spawns draw from ONE
    # per-beat allowance so a beat never exceeds `max_rotations` canonical identity
    # swaps ACROSS both (completions are evaluated first). Starts at `max_rotations`;
    # a completion promote (or a residual re-count) + a spawn rotate each consume one.
    beat_swaps_remaining = max_rotations

    # Pass 2: act, honoring every guard + the 3 conditions.
    for agent, entry, actionable in plans:
        aid = entry["agent_id"]
        if aid in exclude:                                   # guard 4
            entry["armed_status"] = SKIP_EXCLUDED
            summary["skipped_excluded"] += 1
            beats.append(entry); continue

        # --- COMPLETION BRANCH (D3): resume a held rotation on the ALREADY-live
        # successor. Reachable for EVERY open hold INDEPENDENT of decide()-actionability
        # (a post-promote-crash predecessor may no longer classify actionable), so it is
        # placed BEFORE the not-actionable short-circuit AND before SKIP_OPEN_HOLD. Runs
        # ONLY for an ARMED lineage (tier armed + soft_only False + in the allowlist)
        # with a provider wired — INERT otherwise (completion_provider defaults None =>
        # the hold ages exactly as today). Draws from the SHARED per-beat + hourly budget
        # (D10). The provider bundles the executor seams + returns a trace; this applies
        # the ledger effects (mark_promoted after promote, resolve_hold after retire).
        if (completion_provider is not None and entry["armed"] and not soft_only
                and entry["lineage_root"] in armed_lineages
                and _has_open_hold(ledger, aid)):
            hold = _open_hold_for(ledger, aid)
            cooldown_clear = not _lineage_in_cooldown(
                history, entry["lineage_root"], now, post_retire_cooldown_s)
            budget = {"per_beat_slot": beat_swaps_remaining > 0,
                      "hourly_remaining": hourly_remaining,
                      "cooldown_clear": cooldown_clear}
            result = completion_provider(aid, hold, armed=True, budget=budget, now=now)
            status = result["status"]
            entry["armed_status"] = status
            entry["completion"] = result
            summary["armed_run"] += 1
            if result.get("promoted"):
                ledger = _hledger.mark_promoted(
                    ledger, aid, hold["successor"], now=now,
                    promoted_by=result.get("promoted_by") or "")
                # C3 reset-at-promote (DEC-1788165818): gated on ANY successful
                # promote — NOT the narrow completion_mode+AWAITING_PROGRESS
                # condition that gates promote_baseline_sha (complete.py:1042).
                # Inheriting that condition would SKIP every mechanical rotation,
                # leaving gen N+1 with gen N-1's baseline while the on-disk handoff
                # is gen N's consumed rev -> handoff_ready sees FRESH -> premature
                # SOFT_READY before gen N+1 authors anything (the exact false-
                # confirm we protect). Set the baseline to the CONSUMED predecessor
                # rev via the SAME provider the beat uses (path-identity). DISTINCT
                # from promote_baseline_sha.
                history = _beat.set_promote_baseline(
                    history, aid, beat_kwargs.get("handoff_provider"),
                    lineage_root=entry["lineage_root"])
            # A.3 rework: retired != resolved. A WATCH status keeps the hold OPEN and
            # ADVANCES its status for the next beat's dispatch (AWAITING_PROGRESS pre-
            # retire -> RETIRED_AWAITING_EFFECT post-retire); only a TERMINAL status
            # (COMPLETED / ESCALATED / RETIRED_ESCALATED / RESIDUAL_RETIRED) resolves.
            if status in _complete.TERMINAL_STATUSES:
                ledger = _hledger.resolve_hold(ledger, aid, hold["successor"])
            elif status in _complete.WATCH_STATUSES:
                extra = ({"promote_baseline_sha": result["promote_baseline_sha"]}
                         if result.get("promote_baseline_sha") else None)
                ledger = _hledger.set_hold_status(
                    ledger, aid, hold["successor"], status, extra=extra)
            if result.get("budget_consumed"):
                history = _record_retire(history, entry["lineage_root"], now)  # C2a/C2b
                hourly_remaining -= 1
                beat_swaps_remaining -= 1
                summary["rotated"] += 1                       # a canonical identity swap
            elif status in _complete.WATCH_STATUSES or str(status).startswith("hold:"):
                summary["held"] += 1
            beats.append(entry); continue

        if not actionable:
            beats.append(entry); continue
        if not entry["armed"]:                               # T0/T1 observe-only
            entry["armed_status"] = OBSERVE_TIER
            summary["observed"] += 1
            beats.append(entry); continue
        if _has_open_hold(ledger, aid):                      # guard 2
            entry["armed_status"] = SKIP_OPEN_HOLD
            summary["skipped_open_hold"] += 1
            beats.append(entry); continue

        # Piece 2 (DEC-1787687601) — the single-source "which seats does the beat
        # evaluate at all" gate: skip non-claude runtimes (2c: codex/agy have no
        # rotation adapter — codex-dev-1's 136x false-fire) and superseded rows
        # (2b: retired/quiescent/succeeded_by predecessors — the quiescent-lingerer
        # false-fire). Single-sourced with the CORE seat predicate in
        # runtime_signatures (via collect.beat_skip_reason).
        skip = beat_skip_reason(agent)
        if skip in ("non-claude-runtime", "unsupported-runtime"):
            entry["armed_status"] = SKIP_NON_CLAUDE_RUNTIME
            summary["skipped_non_claude_runtime"] += 1
            beats.append(entry); continue
        if skip is not None:                                 # retired/quiescent/succeeded-by
            entry["armed_status"] = SKIP_SUPERSEDED_SEAT
            entry["skip_reason"] = skip
            summary["skipped_superseded_seat"] += 1
            beats.append(entry); continue

        # SAFETY-NET BACKSTOP (gm/the operator role refinement + PB seam): the beat only acts
        # on agents the PRIMARY self-trigger did NOT handle. self_trigger_fn(aid) calls
        # PB's rotation_signal.classify() -> "self_triggered" (agent handled it -> SKIP)
        # / "wedged" (crossed but silent -> the beat proceeds to nudge) / "ok"/None
        # (below band -> proceed as normal). Fail-toward-nudge: only an explicit
        # self_triggered verdict skips. When self_trigger_fn is None the filter is
        # inert (legacy/test path — every actionable agent proceeds).
        if self_trigger_fn is not None:
            verdict = self_trigger_fn(aid)
            entry["self_trigger"] = verdict
            if verdict == "self_triggered":
                entry["armed_status"] = SKIP_SELF_TRIGGERED
                summary["skipped_self_triggered"] += 1
                beats.append(entry); continue

        is_hard = entry["action"] == "hard_rotate"
        if is_hard:
            # SHAW RULING 2026-08-15 (msg_8860a706): a HARD rotation NEVER auto-kills.
            # This re-arm ships tiers 1+2 ONLY (self-trigger + soft nudge), so the hard
            # branch is DEFERRED here. Tier 3 (a SEPARATE spec+congruence+build AFTER
            # 1+2 are live) will replace this deferral with an APPROVAL-EMIT: the beat
            # posts a rotation_kill decision card to the operator's approvals surface (agent id
            # / ctx% / why-wedged / evidence / Approve-kill|Deny|Defer) and the kill
            # runs ONLY on the operator's per-kill tap. It must NEVER call execute_rotation's
            # kill path unattended. (This REPLACES the old DEC-1786785676 soft->hard
            # blanket-flip.) Until Tier 3 lands, soft_only=True keeps the hard branch
            # deferred = safe.
            if soft_only:
                entry["armed_status"] = SKIP_SOFT_ONLY
                summary["skipped_soft_only"] += 1
                beats.append(entry); continue
            # PER-LINEAGE ARM GATE: soft_only=False, but a hard_rotate only proceeds
            # for a lineage explicitly opted-in via the self-retire arming allowlist.
            # A lineage NOT in armed_lineages defers here (SKIP_NOT_ARMED) BEFORE any
            # spawn/execute — so arming ONE lineage rotates exactly that seat, not the
            # whole T2 tier. FAIL-CLOSED: an EMPTY armed set defers every hard_rotate
            # (same scoping the auto-RETIRE half already gets via is_graduated_autoretire).
            if entry["lineage_root"] not in armed_lineages:
                entry["armed_status"] = SKIP_NOT_ARMED
                summary["skipped_not_armed"] += 1
                beats.append(entry); continue
            # C2b post-retire cooldown for this lineage (flap protection).
            if _lineage_in_cooldown(history, entry["lineage_root"], now,
                                    post_retire_cooldown_s):
                entry["armed_status"] = SKIP_LINEAGE_COOLDOWN
                summary["skipped_cooldown"] += 1
                beats.append(entry); continue
            # C2a rolling-hour cap across beats.
            if hourly_remaining <= 0:
                entry["armed_status"] = SKIP_HOURLY_CAP
                summary["skipped_hourly_cap"] += 1
                beats.append(entry); continue
            # guard 1 per-beat cap — the SHARED budget (D10): a spawn defers if it is
            # outside the top-ranked cohort OR the per-beat identity-swap budget was
            # already consumed (e.g. by a completion earlier this beat). When no
            # completion runs, `beat_swaps_remaining` starts at `max_rotations` and only
            # the <=max_rotations allowed_hard members decrement it, so this is a no-op
            # vs. the prior behavior; it only bites once completions share the budget.
            if aid not in allowed_hard or beat_swaps_remaining <= 0:
                entry["armed_status"] = SKIP_CAP_DEFERRED
                summary["deferred_cap"] += 1
                beats.append(entry); continue

        # cleared all guards + conditions -> REAL armed beat.
        real = beat_fn(agent, registry, canary=aid, now=now,
                       locks=locks, ledger=ledger, history=history, **beat_kwargs)
        locks = real["locks"]
        ledger = real["ledger"]
        entry["armed_status"] = real["status"]
        entry["rotation"] = real.get("rotation")
        summary["armed_run"] += 1
        if real["status"] == "rotated":
            summary["rotated"] += 1
            history = _record_retire(history, entry["lineage_root"], now)  # C2a/C2b
            hourly_remaining -= 1
            beat_swaps_remaining -= 1                         # D10 shared per-beat budget
        elif str(real["status"]).startswith("hold:"):
            summary["held"] += 1
        beats.append(entry)

    return {"beats": beats, "locks": locks, "ledger": ledger, "history": history,
            "summary": summary, "disabled": False, "advisory_sentinel": advisory}


def _log_line(aid, tier, dry, armed):
    """A single observable dry-run log line per agent per beat."""
    tag = "ARMED" if armed else "observe"
    return (f"[fleet-beat] {aid} tier={tier} disposition={dry['status']} "
            f"action={dry['action']} reason={dry['reason']} mode={tag}")


def armed_cohort(agents, registry, armed_tiers, *, exclude=DEFAULT_EXCLUDE,
                 ledger=None):
    """The subset of agent_ids whose tier is in armed_tiers AND that are actionable
    (soft_handoff / hard_rotate) AND clear the persistent guards (not excluded, no
    open hold) — the agents this beat would actually act on. Pure; for pre-flight
    cohort listing to gm + red-team BEFORE enabling (matches plan_fleet's filters)."""
    exclude = set(exclude or ())
    out = []
    for agent in agents:
        aid = agent.get("agent_id")
        if _tier_class(agent, registry) not in armed_tiers:
            continue
        if aid in exclude or _has_open_hold(ledger, aid):
            continue
        d = _beat.decide(agent)
        if d["action"] != "noop":
            out.append({"agent_id": aid, "action": d["action"], "reason": d["reason"]})
    return out
