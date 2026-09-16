#!/usr/bin/env python3
"""cron_beat.py — the LIVE fleet-beat cron entrypoint (WS3 arming, DEC-1786785676).

DEC-1786785676 CONSENSUS_REACHED (gm+agy APPROVE) + the operator's explicit go. This runs
ONE fleet beat per */15 cron tick: gather the live fleet (read-only) -> plan_fleet
with armed_tiers={"T2"}, soft_only=True, and ALL guards -> persist locks/ledger/
history (flock + atomic) -> one log line per agent per beat.

FIRST-WINDOW POSTURE (the operator-approved, binding): soft_only=True. It may fire
REVERSIBLE author-triggers ONLY; EVERY hard_rotate defers (SKIP_SOFT_ONLY). The
soft->hard flip is a SEPARATE 2nd the operator go — this entrypoint hard-codes soft_only
True; enabling hard rotation is a deliberate future edit, not a config flip.

GUARDS (all enforced in plan_fleet, verified at source): kill-switch sentinel
state/FLEET_BEAT_DISABLED (instant no-op), max-1-rotation/beat, skip-open-HOLD,
persistent EXCLUDE, max-rotations-per-hour=4, post-retire-cooldown=1800.

Concurrency: a single flock on the state file serializes overlapping cron ticks
(a slow beat can't be double-run); the beat is skipped if the lock is held.
"""
import fcntl
import json
import os
import sys
import time

# Bootstrap package import when run directly (cron) — must precede package imports.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from scripts.lineage_daemon import fleet as _fleet
from scripts.lineage_daemon import executors as _executors
from scripts.lineage_daemon import beat as _beat
from scripts.lineage_daemon.collect import collect_fleet

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORCHESTRA_DIR = os.environ.get("ORCHESTRA_DIR", _ROOT)
STATE_PATH = os.path.join(ORCHESTRA_DIR, "state", "fleet-beat-state.json")
LOCK_PATH = os.path.join(ORCHESTRA_DIR, "state", "fleet-beat.lock")
LOG_PATH = os.path.join(ORCHESTRA_DIR, "logs", "fleet-beat.log")

ARMED_TIERS = frozenset({"T2"})       # wave-1: T2 only (T0/T1 observe-only)
SOFT_ONLY = False                      # ARMED (the operator-directed 2026-08-26) — per-lineage
                                       # arm gate scopes hard_rotate to ~/runtime/
                                       # self_retire_armed (pm-skyline only); every other
                                       # lineage still defers (SKIP_NOT_ARMED). Revert to
                                       # True to disarm globally.

# Notify-only (gm request, the operator wants an INSTANT ping on the first-ever autonomous
# self-rotation skip — gm's msg_store is dormant between the operator turns = unreliable). The
# once-sentinel lives on the NON-SYNCED runtime path so a stale sync can't re-fire it
# or suppress it. Bridge until PB's wake-on-delivery lands. Changes NO rotation decision.
FIRST_SKIP_NOTIFY_SENTINEL = "FIRST_ROTATION_SKIP_NOTIFIED"

def _first_skip_notify(beats, *, now, dry=False):
    """FAIL-OPEN, NOTIFY-ONLY: on the FIRST-EVER self_triggered skip, fire ONE Telegram
    then drop a non-synced once-sentinel so it never re-fires. Any error (tg failure,
    fs error) is swallowed — a notify problem must NEVER wedge or alter the beat.
    Returns True if it fired (for the caller's log), else False."""
    try:
        skipped = [b for b in beats
                   if b.get("armed_status") == _fleet.SKIP_SELF_TRIGGERED]
        if not skipped:
            return False
        sentinel = os.path.join(_fleet._runtime_dir(), FIRST_SKIP_NOTIFY_SENTINEL)
        if os.path.exists(sentinel):
            return False                         # already notified once — done
        agent = skipped[0]["agent_id"]
        ctx = skipped[0].get("ctx_pct")
        ctx_s = f"{round(ctx * 100)}%" if isinstance(ctx, (int, float)) and ctx >= 0 else "?"
        msg = (f"First autonomous self-rotation: {agent} self-triggered at {ctx_s}, "
               f"safety-net beat SKIPPED it correctly (both halves cooperating).")
        if dry:
            return True                          # dry-run: report intent, no send/sentinel
        import subprocess
        subprocess.run([os.path.join(ORCHESTRA_DIR, "scripts", "tg-notify.sh"),
                        "--from", "fleet-beat", msg],
                       capture_output=True, timeout=20)
        # drop the once-sentinel AFTER the send attempt (fire-once even if tg flaked —
        # we don't spam; the log still records every skip for the record).
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        open(sentinel, "w").write(str(now))
        return True
    except Exception:  # noqa: BLE001 -- notify is best-effort; NEVER wedge the beat
        return False


# --- persisted beat state (locks + ledger + rotation history), flock + atomic ---

def load_state(path=STATE_PATH):
    """Read the persisted {locks, ledger, history}; missing/corrupt -> fresh."""
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("locks", {"rotations": []})
    d.setdefault("ledger", {"holds": []})
    d.setdefault("history", {"retires": []})
    return d


def save_state(state, path=STATE_PATH):
    """Atomically persist {locks, ledger, history} (write-temp + rename)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"locks": state["locks"], "ledger": state["ledger"],
                   "history": state["history"]}, fh, indent=2)
    os.replace(tmp, path)


def _log(lines, path=LOG_PATH):
    """Append log lines (one per agent per beat + a summary)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as fh:
        for ln in lines:
            fh.write(ln + "\n")


# --- live fleet (read-only) ---------------------------------------------------

def gather_live_fleet():
    """Read-only: run the observe-only lineage-daemon's fleet gatherer + registry,
    shaped to decide() inputs via collect_fleet. Loaded via importlib because the
    entry module is hyphenated (lineage-daemon.py). No fleet action taken here."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lineage_daemon_entry", os.path.join(ORCHESTRA_DIR, "scripts", "lineage-daemon.py"))
    ld = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ld)
    registry = ld.load_registry()
    agents = collect_fleet(ld.gather_fleet(), registry)
    return agents, registry


# --- the beat -----------------------------------------------------------------

def _bg_armed_roots_safe(agents, bg_wal_dir):
    """Firewalled pre-plan_fleet computation of the armed set (inv 1 ABSOLUTE, gm
    gen38). ANY exception — a FUTURE import-time error anywhere in the bg module
    tree (bg_beat/bg_arm/bg_state/decide_bg) or a stat error — degrades to an EMPTY
    armed set (byte-identical-legacy exclude) so a broken bg module can NEVER wedge
    the live fleet beat BEFORE plan_fleet runs. Returns (armed_set, error_or_None);
    the error is reported as a bg alarm (same swallow-to-alarm discipline as the
    AFTER-pass firewall). This closes the one seam that ran outside a guard."""
    try:
        from scripts.lineage_daemon.wal import bg_beat as _bg
        return set(_bg.armed_roots(agents, bg_wal_dir)), None
    except Exception as exc:  # noqa: BLE001 -- inv 1 firewall (the BEFORE seam)
        return set(), repr(exc)


def _bg_supervise(agents, *, orchestra_dir, bg_wal_dir, now, dry, armed):
    """Gate-3.5 bg supervisor pass — runs AFTER plan_fleet, inside a TOP-LEVEL
    exception firewall (gm hard req): a bg blow-up can NEVER wedge the old fleet
    rotation. A dry beat records the armed count only (no bg side effects). The
    inner bg_supervise_fleet already firewalls per-seat; this top-level catch is
    belt-and-suspenders so even an import/construction error stays in the summary."""
    if dry:
        return {"armed": len(armed), "dry": True}
    try:
        from scripts.lineage_daemon.wal import bg_beat as _bg
        return _bg.bg_supervise_fleet(
            agents, orchestra_dir=orchestra_dir, wal_dir=bg_wal_dir, now=now)
    except Exception as exc:  # noqa: BLE001 -- the top-level firewall (hard req)
        return {"armed": len(armed), "error": repr(exc), "firewalled": True}


def _decide_bg_shadow_safe(agents, *, orchestra_dir, bg_wal_dir, now):
    """Gate-3.5 decide_bg LOG-ONLY shadow pass (gm-g60 msg_727350a8). Per-beat, for EVERY
    collected seat (FLEET scope — the broad-arm lens), compute the decide_bg decision and
    log its would-fire/would-defer/would-suppress classification to
    state/wal/decide_bg_shadow.jsonl. Observational: ZERO spawn, ZERO fire, ZERO cards, no
    seams — it changes nothing. Inside a TOP-LEVEL firewall so a shadow blow-up can NEVER
    wedge the beat (same discipline as the bg supervisor pass). Runs on dry beats too
    (pure observation is exactly what a dry beat wants)."""
    try:
        from scripts.lineage_daemon.wal import decide_bg_shadow
        log_path = os.path.join(bg_wal_dir, "decide_bg_shadow.jsonl")
        return decide_bg_shadow.decide_bg_shadow_pass(
            agents, wal_dir=bg_wal_dir, orchestra_dir=orchestra_dir, now=now,
            shadow_log_path=log_path,
            armed_roots_fn=lambda ag, wd, force_roots=None: [
                a.get("agent_id") for a in ag if a.get("agent_id")])
    except Exception as exc:  # noqa: BLE001 -- the shadow must NEVER wedge the beat
        return {"error": repr(exc), "firewalled": True}


def run_beat(agents, registry, state, *, now=None, orchestra_dir=None,
             armed_tiers=ARMED_TIERS, soft_only=SOFT_ONLY, dry=False,
             confirm_fn=None, s3_seams_provider=None,
             grade_fn=None, approval_fn=None, grade_runner=None,
             armed_lineages=None, completion_provider=None,
             handoff_provider=None, bg_wal_dir=None):
    """One fleet beat over injected agents/registry/state (pure over seams). Returns
    the plan_fleet result with locks/ledger/history threaded. Real seams: msg_store
    author_inject (durable soft-handoff instruction), committed-handoff provider,
    real executors + the REAL S3 two-sample confirm seam (used only if soft_only is
    ever flipped; hard rotates defer while soft_only=True).

    `confirm_fn` / `s3_seams_provider` (injectable): the S3 confirm gate
    execute_rotation runs before the kill gates. Default builds the REAL
    s3_confirm_seam.build_s3_confirm_fn(seams_provider=s3_seams_provider) — NOT the
    old deceptive `held` constant. With no live seams provider (the first window) it
    HOLDS honestly ("s3-live-seams-unavailable"); a caller that provisions live
    successor-read / daemon-held-ground-truth seams gets a genuine confirm. Passing
    `confirm_fn` directly overrides the factory entirely (tests / future wiring).

    `grade_fn` / `approval_fn` / `grade_runner` (injectable, T2 auto-loop trio): the
    unattended machine gate + graduation-gated auto-approve execute_rotation runs
    AFTER S3 confirm, BEFORE KILL GATE 2. Defaults build the REAL trio:
      * grade_fn(canary, successor): resolves the predecessor's session_id from
        agent-sessions.json (`sid_of`) and calls auto_grade.auto_grade_successor(
        successor, predecessor_sid, strict=True). A non-PASS disposition HOLDS the
        rotation (HOLD_GRADE); nothing retires. `grade_runner` overrides the grader
        subprocess seam (tests pass a fake exit code — no shelling out).
      * approval_fn(canary, successor): graduation_approval.make_graduation_gated_
        approval(seat, graduated_fn=is_graduated_autoretire, confirmed_fn=<S3
        confirmed for THIS rotation>, fallback_fn=default the operator card). Auto-approves
        (no card) ONLY for a cold-verified graduated-T2 seat whose S3 confirmed;
        EVERY other case falls back to the the operator one-tap card.
    All three preserve current behavior by construction under the first window: with
    soft_only=True EVERY hard_rotate defers (grade/approve never run), and even if
    flipped, is_graduated_autoretire fails closed (no kill-switch-absent + armed
    allowlist + strict PASS) so approval_fn falls back to the the operator card. Passing a
    seam directly overrides the factory (tests / future wiring).

    `armed_lineages` (injectable, per-lineage arm gate): the set of lineage roots
    whose T2 hard_rotate may proceed when soft_only=False. None (default) => plan_fleet
    reads the canonical self-retire arming allowlist (~/runtime/self_retire_armed) via
    self_retire_gate — the SAME single-source file is_graduated_autoretire gates the
    auto-RETIRE half on. Absent/empty file => EMPTY set => FAIL-CLOSED (every
    hard_rotate defers). Under the first window (soft_only=True) it is moot — every
    hard_rotate defers before the arm gate — so this preserves current behavior; it
    only takes effect once soft_only is flipped, and even then rotates ONLY armed
    lineages. Tests inject an explicit set to bypass the file.

    `dry=True` -> TRUE dry-run: author_inject is a NO-OP recorder (fires NO real
    msg_store row); the beat computes the full plan + logs without any side effect."""
    now = now if now is not None else time.time()
    od = orchestra_dir or ORCHESTRA_DIR

    meta = registry.get("agents", {})
    import json as _json
    try:
        with open(os.path.join(od, "state", "agent-sessions.json")) as fh:
            sessions_meta = _json.load(fh)
    except Exception:  # noqa: BLE001
        sessions_meta = {}

    def author_inject(agent_id, text):
        # (The interim per-seat soft-handoff MUTE was RETIRED — DEC-1788165818:
        # the over-fire is now fixed at the root by real markdown recognition +
        # baseline-relative freshness, so a done seat reaches SOFT_READY and is
        # not re-nudged. No denylist hack stacked on top.)
        if dry:
            return f"DRY-would-send:{agent_id}"      # no real row in a dry-run
        # durable soft-handoff instruction row (reversible; the agent authors at will).
        from msg_store import MessageStore
        return MessageStore().send(
            from_agent="lineage-daemon", to_agent=agent_id,
            type="lineage_soft_handoff", priority="high",
            subject="[LINEAGE SOFT-HANDOFF] author your successor handoff now",
            body=text, source="fleet-beat")

    if handoff_provider is None:
        from scripts.lineage_daemon.handoff_provider import build_live_handoff_provider
        handoff_provider = build_live_handoff_provider(
            orchestra_dir=od, sessions_meta=sessions_meta, registry=registry)

    # A2 Axis-3(b): the prior-committed-rev seam for the C1 baseline fallback, so a
    # PROACTIVELY-authored seat (authored before its first soft beat) is FRESH
    # against its PRIOR handoff rather than deadlocking on baseline==current.
    from scripts.lineage_daemon.handoff_provider import build_prior_rev_fn
    prior_rev_fn = build_prior_rev_fn(orchestra_dir=od)

    def completion_fn(successor):
        # F17: a COMPLETED three-artifact handoff (successor canary authored +
        # readback recorded + comprehension PASS) -> the soft beat must not re-ping
        # the author-trigger. Read-only; a graded-but-failed comprehension is NOT
        # complete so the ping stays live for re-study.
        from scripts.lineage_daemon.author_gate import handoff_complete
        base = os.path.join(od, "state", "agent-handoffs")
        canary = os.path.exists(os.path.join(base, f"{successor}.canary.json"))
        readback = os.path.exists(os.path.join(base, f"{successor}.readback.md"))
        passed = False
        comp = os.path.join(base, f"{successor}.comprehension.json")
        if os.path.exists(comp):
            try:
                with open(comp) as fh:
                    passed = bool(json.load(fh).get("pass"))
            except Exception:  # noqa: BLE001 -- unreadable grade -> treat as not-passed
                passed = False
        return handoff_complete(canary_present=canary, readback_present=readback,
                                comprehension_passed=passed)

    # S3 content-gate confirm (two-sample) — the REAL seam, only reached if
    # soft_only=False. Replaces the old hardcoded `held` constant with the genuine
    # s3_confirm_seam: it HOLDS honestly when no live seams are provisioned (the
    # first window) and runs the real beat.two_sample_confirm when a caller supplies
    # a live s3_seams_provider. Injectable (confirm_fn overrides the factory).
    if confirm_fn is None:
        from scripts.lineage_daemon.s3_confirm_seam import build_s3_confirm_fn
        confirm_fn = build_s3_confirm_fn(seams_provider=s3_seams_provider)

    def sid_of(agent_id):
        return (sessions_meta.get(agent_id) or {}).get("session_id")

    self_trigger_fn = _fleet.self_trigger_classifier(
        sid_of=sid_of, now=now, orchestra_dir=od)

    # --- T2 AUTO-LOOP TRIO (grade_fn + approval_fn) — the unattended machine gate
    # + graduation-gated auto-approve execute_rotation runs AFTER S3 confirm and
    # BEFORE KILL GATE 2. Both are injectable; the defaults below build the REAL
    # trio. Inert in the first window (soft_only defers every hard_rotate) and
    # fail-closed even if flipped (is_graduated_autoretire falls back to the card).

    def _seat_of(canary):
        """Build the seat dict is_graduated_autoretire consumes for THIS rotation:
        the predecessor's registry row (durable tier/lineage) + resolved successor."""
        row = dict(meta.get(canary, {}) or {})
        row.setdefault("agent_id", canary)
        succ = _beat.resolve_successor_name(canary, registry, sessions_meta)
        if succ:
            row["successor"] = succ
        return row

    # grade_fn: resolve the predecessor's live sid and machine-grade the successor's
    # readback (STRICT). A non-PASS disposition HOLDS the rotation (HOLD_GRADE).
    if grade_fn is None:
        from scripts.lineage_daemon.auto_grade import auto_grade_successor

        def grade_fn(canary, successor):
            kwargs = {} if grade_runner is None else {"runner": grade_runner}
            return auto_grade_successor(
                successor, sid_of(canary), strict=True, **kwargs)

    # approval_fn: graduation-gated auto-approve. Per-rotation it binds the seat +
    # an S3-confirmed check (re-runs confirm_fn, treats outcome=="confirmed" as the
    # confirmed signal), then delegates to make_graduation_gated_approval. Falls
    # back to the default the operator card in EVERY non-graduated / not-confirmed case.
    if approval_fn is None:
        from scripts.lineage_daemon.graduation_approval import (
            make_graduation_gated_approval)
        from scripts.lineage_daemon.self_retire_gate import (
            is_graduated_autoretire)

        def _s3_confirmed(canary, successor):
            try:
                return (confirm_fn(canary, successor) or {}).get(
                    "outcome") == "confirmed"
            except Exception:  # noqa: BLE001 -- fail toward the card
                return False

        def approval_fn(canary, successor):
            gate = make_graduation_gated_approval(
                _seat_of(canary),
                graduated_fn=is_graduated_autoretire,
                confirmed_fn=_s3_confirmed)
            return gate(canary, successor)

    # --- MULTI-BEAT COMPLETION PROVIDER (DEC-1787789209) — resumes a HELD rotation
    # on the already-live successor on a LATER beat (S3 re-confirm -> strict grade ->
    # KILL GATE 1 -> graduation -> CAP -> promote-then-retire -> resolve_hold). Wraps
    # the SAME live seams the spawn path uses (the S3 confirm_fn built above, auto_grade,
    # is_graduated_autoretire, default_safety_recheck, promote_successor, plan_retire) —
    # NEVER reimplemented. INERT-UNTIL-ARMED: plan_fleet only calls it for an armed
    # lineage (soft_only False + self_retire_armed allowlist = pm-skyline only), so a
    # beat with no armed+ready held lineage NEVER fires it (rotated=0). `dry` makes the
    # promote/retire wrappers plan-only. Injectable (tests pass a fake).
    if completion_provider is None:
        from scripts.lineage_daemon.complete import build_completion_provider
        # SEPARATE completion-mode confirm (DEC-1787808620): the completion re-confirm
        # of a HELD readback-only successor gates on COMPREHENSION only (effect is a
        # post-promote artifact, verified within-N-beats-else-ROLLBACK). The spawn-time
        # confirm_fn above stays fully strict/UNCHANGED. Same live seams provider; only
        # the completion_mode flag differs. No seams provider (first window) => HOLDs
        # honestly, exactly like the spawn confirm.
        from scripts.lineage_daemon.s3_confirm_seam import build_s3_confirm_fn
        completion_confirm_fn = build_s3_confirm_fn(
            seams_provider=s3_seams_provider, completion_mode=True)
        # Rotation instrumentation (spec §B): a shadow-safe, PURE-ADDITIVE timing sink
        # that appends one row per section boundary to logs/rotation-timing.jsonl. It
        # changes NO rotation decision (only writes a log) so it ships safe live with no
        # arm gate. A dry beat still records timing (the plan is timed too).
        def _timing_sink(row):
            try:
                tp = os.path.join(od, "logs", "rotation-timing.jsonl")
                os.makedirs(os.path.dirname(tp), exist_ok=True)
                with open(tp, "a") as _fh:
                    _fh.write(json.dumps(row) + "\n")
            except Exception:  # noqa: BLE001 -- logging must never break the beat
                pass

        completion_provider = build_completion_provider(
            orchestra_dir=od, sessions_meta=sessions_meta, registry=registry,
            dry=dry, confirm_fn=completion_confirm_fn, completion_mode=True,
            timing_sink=_timing_sink)

    # Piece 1 (DEC-1787861488): S1 hold-open trigger snapshot — record the
    # predecessor's verified-fresh detector reading ON the hold row the moment
    # a hard_rotate HOLDs, so G7 can later validate the promote from the STORE
    # (C1) instead of a live detector the quiescent predecessor never re-stamps.
    # Positive-signal-only: any failure -> None -> the hold is byte-identical
    # to before (and the promote later requires the human override, as today).
    def trigger_snapshot_fn(canary):
        try:
            from scripts.promote_successor import build_trigger_snapshot
            return build_trigger_snapshot(canary)
        except Exception:  # noqa: BLE001 -- never fabricate, never wedge the beat
            return None

    # GATE 3.5 (DEC-1788415854) — inv 2 MUTUAL EXCLUSION: an armed seat is taken off
    # the old in-band plan_fleet path (exclude = DEFAULT_EXCLUDE | armed) so the two
    # drivers never both act on one lineage in a beat. With nothing armed the delta
    # is empty and plan_fleet's exclude is byte-identical to today (INERT-by-effect).
    # armed_roots is the whole unarmed-beat cost: one is_armed stat/agent.
    bg_wal_dir = bg_wal_dir or os.path.join(od, "state", "wal")
    # inv 1 ABSOLUTE: this pre-plan_fleet seam is FIREWALLED — a broken bg module
    # tree degrades to armed=set() (pure legacy exclude), never wedging the beat.
    armed, pre_bg_error = _bg_armed_roots_safe(agents, bg_wal_dir)

    result = _fleet.plan_fleet(
        agents, registry, armed_tiers=armed_tiers, now=now,
        locks=state["locks"], ledger=state["ledger"], history=state["history"],
        soft_only=soft_only, armed_lineages=armed_lineages, orchestra_dir=od,
        executors_impl=_executors, confirm_fn=confirm_fn,
        grade_fn=grade_fn, approval_fn=approval_fn,
        author_inject=author_inject, handoff_provider=handoff_provider,
        prior_rev_fn=prior_rev_fn,
        completion_fn=completion_fn, sessions_meta=sessions_meta,
        self_trigger_fn=self_trigger_fn, session_turns=200,
        completion_provider=completion_provider,
        trigger_snapshot_fn=trigger_snapshot_fn,
        exclude=_fleet.DEFAULT_EXCLUDE | armed)

    # inv 1 TOP-LEVEL FIREWALL: the bg supervisor pass runs AFTER plan_fleet and can
    # NEVER wedge it (a bg blow-up is contained to result["bg"]). If the BEFORE seam
    # already degraded (broken bg module tree), report that and skip the after-pass
    # (which would just re-fail on the same import) — the beat still completed.
    if pre_bg_error is not None:
        result["bg"] = {"armed": 0, "error": pre_bg_error, "firewalled": True}
    else:
        result["bg"] = _bg_supervise(agents, orchestra_dir=od, bg_wal_dir=bg_wal_dir,
                                     now=now, dry=dry, armed=armed)

    # Gate-3.5 decide_bg LOG-ONLY shadow (gm-authorized, msg_727350a8): accumulate the
    # fleet FIRE-decision window per-beat to de-risk (B). Observational/INERT; firewalled
    # so it can never wedge the beat.
    result["bg_shadow"] = _decide_bg_shadow_safe(
        agents, orchestra_dir=od, bg_wal_dir=bg_wal_dir, now=now)
    return result


def main(argv=None) -> int:
    dry = "--dry-run" in (argv or sys.argv[1:])
    now = time.time()
    os.makedirs(os.path.join(ORCHESTRA_DIR, "state"), exist_ok=True)

    # flock: serialize overlapping cron ticks; skip this beat if one is in flight.
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log([f"[fleet-beat] {int(now)} SKIPPED — prior beat still in flight (flock held)"])
        print(json.dumps({"ts": now, "skipped": "flock-held"}))
        return 0

    try:
        state = load_state()
        agents, registry = gather_live_fleet()
        # Gap-1 (DEC-1787767251): the live S3 seams provider. WIRED as KEY 4 of the
        # the operator-directed T2 ARM (2026-08-26). Fail-closed by construction: returns None
        # (=> the confirm HOLDs => NOTHING retires) for any seat whose committed handoff
        # is missing/degenerate or whose transcript is unresolvable. Scoped by the
        # per-lineage arm gate (self_retire_armed=pm-skyline) so only pm-skyline lineage
        # can reach a hard_rotate. To DISARM: revert SOFT_ONLY=True (or remove this wire)
        # + clear ~/runtime/self_retire_armed + restore ~/runtime/SELF_RETIRE_DISABLED.
        from scripts.lineage_daemon.s3_live_seams import build_live_seams_provider
        s3_seams_provider = build_live_seams_provider(orchestra_dir=ORCHESTRA_DIR)
        result = run_beat(agents, registry, state, now=now, dry=dry,
                          s3_seams_provider=s3_seams_provider)

        # one log line per agent per beat + a summary line.
        lines = [b["log"] + f" -> {b.get('armed_status', b['dry_status'])}"
                 for b in result["beats"]]
        # ADVISORY (gm msg_b8f1c614): a stale synced in-tree sentinel is present but is
        # NOT authoritative — log a LOUD warning so an operator notices/cleans it, but
        # the beat runs (only ~/runtime/FLEET_BEAT_DISABLED brakes).
        if result.get("advisory_sentinel"):
            lines.append(
                f"[fleet-beat] {int(now)} ADVISORY-WARN: in-tree state/FLEET_BEAT_DISABLED "
                f"present (stale synced sentinel) — NOT authoritative, beat still runs. "
                f"Authoritative e-brake = ~/runtime/FLEET_BEAT_DISABLED. Clean the stale file.")
        s = result["summary"]
        lines.append(
            f"[fleet-beat] {int(now)} SUMMARY soft_only={SOFT_ONLY} "
            f"total={s['total']} actionable={s['actionable']} armed_run={s['armed_run']} "
            f"rotated={s['rotated']} held={s['held']} soft_deferred={s.get('skipped_soft_only',0)} "
            f"disabled={result.get('disabled', False)}")
        # Gate-3.5 bg supervisor summary (INERT until a seat is bg_enabled).
        bg = result.get("bg") or {}
        lines.append(
            f"[fleet-beat] {int(now)} BG armed={bg.get('armed', 0)} "
            f"acted={bg.get('acted', 0)} failed={bg.get('failed', 0)} "
            f"disarmed={bg.get('disarmed', 0)} alarms={bg.get('alarms', 0)}"
            + (f" FIREWALLED err={bg['error']}" if bg.get('firewalled') else ""))
        # NOTIFY-ONLY (gm request): INSTANT Telegram on the FIRST-EVER self_triggered
        # skip (once, non-synced sentinel, fail-open). Changes no rotation decision.
        if result["summary"].get("skipped_self_triggered", 0) >= 1:
            if _first_skip_notify(result["beats"], now=now, dry=dry):
                _log([f"[fleet-beat] {int(now)} FIRST-SKIP-NOTIFY fired (Telegram, once)"])
        _log(lines)

        if not dry:
            state["locks"] = result["locks"]
            state["ledger"] = result["ledger"]
            state["history"] = result["history"]
            save_state(state)

        # HARD-ROTATION INVARIANT for the first window: NEVER > 0 rotations here.
        print(json.dumps({
            "ts": now, "disabled": result.get("disabled", False),
            "total": s["total"], "actionable": s["actionable"],
            "armed_run": s["armed_run"], "rotated": s["rotated"],
            "soft_deferred": s.get("skipped_soft_only", 0), "dry_run": dry}))
        return 0
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
