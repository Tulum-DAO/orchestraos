"""bg_beat — the Gate-3.5 LIVE DRIVER for the async Blue-Green arm.

The arm (`bg_arm.BgArm`) was merged INERT @51c6e3719 with NO invoker: `run_capture`
is capture-only and `cron_beat` had zero arm hook, so setting `bg_enabled` fired
nothing. This module is that missing invoker. `bg_supervise_fleet` runs ONCE per
fleet beat, AFTER `plan_fleet`, on the SAME already-collected fleet — a PARALLEL,
flag-gated supervisor pass that is a pure no-op until a seat is armed.

Locked contract: `.workspace/proposals/gate3.5-beat-supervisor-wiring-spec.md`
(DEC-1788415854, both-approve). FOLDED-with-hard-exception-firewall. The 7
load-bearing invariants (all RED-tested in bg_beat_test.py):

  1. FAIL-ISOLATION FIREWALL — the per-lineage `arm.beat` runs inside a try/except
     that swallows-to-alarm and NEVER re-raises; a per-seat exception `continue`s
     (skips only that seat). A bg bug can NEVER wedge the old plan_fleet rotation.
  2. MUTUAL EXCLUSION — cron_beat excludes armed seats from the old in-band path
     (exclude = DEFAULT_EXCLUDE | armed_roots) so two drivers never both act.
  3. INERT-by-effect — the whole pass short-circuits on ONE `is_armed` stat/agent
     when nothing is armed: no arm constructed, no seams built, no state write.
  4. bg_enabled ⊆ cutover-active — `BgArm.beat` raises `ArmRefused` if armed while
     cutover inactive; the firewall turns it into a real alarm, never a crash.
  5. GLOBAL BG_DISABLED — `is_armed` already honors the one-tap fleet kill-switch.
  6. DB WRITE-TRUTH — `blue_generation_id` + `green {gen+1,...}` come from the
     canonical DB each beat; an absent canonical raises SwapPreconditionError
     (fail-closed), caught by the firewall as a bg-failure.
  7. ANTI-ORPHAN — inv1 (per-seat continue) + inv2 (armed seat off the old path)
     together would orphan a persistently-throwing armed seat (rescued by NEITHER
     driver). So: N consecutive bg-failures => auto-disarm-to-legacy (drop the
     `bg_enabled` flag so the seat falls back to plan_fleet next beat) + PAGE.
     Never a silent indefinite orphan.
"""
import json
import os

from .bg_state import is_armed, BgStateStore
from .decide_bg import decide_bg
from .bg_arm import BgArm
from .decide_bg import PREWARM_AT  # noqa: F401  (documents the ctx scale for readers)
from .ctx_adapters import read_ctx as _read_ctx, DEFAULT_RUNTIME

# Anti-orphan: consecutive bg-failures on one armed seat before disarm-to-legacy
# + page. gm's RED criterion: a throwing seam for x3 beats => disarm-or-page.
ORPHAN_DISARM_AFTER = 3

# B3 fan-out cap (v2.3 addendum item 2a, DEC-1789452018556720). A BROAD arm can ask the beat to
# move many armed seats SOLO->PREWARMING in adjacent ticks; each green boot spikes ~0.5-1 GiB RSS,
# and g15's pred4 green OOM-died at boot under swap thrash. This caps how many greens may be booting
# CONCURRENTLY (complements spawn_green's per-spawn RAM floor: that bounds ONE spawn, this bounds N).
MAX_CONCURRENT_GREENS = 2
# A green is "in-flight" (resident RAM, not yet promoted-or-reaped) while its seat is in one of these
# states. Counting by STATE is provider-agnostic (no runtime literal). DRAINED = reaped/complete;
# DEGRADED (effects-incomplete) is reaped by the same-beat completion phase or handled by the orphan
# path, so it is not counted here.
_INFLIGHT_STATES = ("PREWARMING", "READY", "SWAPPING")


def _effective_max_concurrent_greens():
    """The fan-out cap = MAX_CONCURRENT_GREENS unless BG_MAX_CONCURRENT_GREENS overrides it (a
    tuning knob; the core constant is untouched, same PROCESS-env pattern as decide_bg's BG_TEST_*).
    A malformed/absent/<1 value falls back to the core constant — fail-SAFE: a typo never LIFTS the
    cap (and never sets it to 0, which would freeze all rotation)."""
    raw = os.environ.get("BG_MAX_CONCURRENT_GREENS")
    if raw is None:
        return MAX_CONCURRENT_GREENS
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MAX_CONCURRENT_GREENS
    return v if v >= 1 else MAX_CONCURRENT_GREENS


def _append_fleet_beat_log(orchestra_dir, line):
    """Append a LOUD beat-decision line to logs/fleet-beat.log (the same log the cron beat writes
    its decisions to). Fail-soft — a logging hiccup can NEVER break the beat."""
    try:
        path = os.path.join(orchestra_dir, "logs", "fleet-beat.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(line.rstrip("\n") + "\n")
    except OSError:
        pass


class BgSeamsNotWired(Exception):
    """A live effect seam (spawn/verify/hydrate/reap) is not yet wired. The arm was
    delivered INERT; the real setsid-detached spawn / wal-probe verify / grandchild
    reap are bound at the live-pane drill (stage-4 seam-integration note). Until
    then an armed seat that reaches prewarm/swap raises this — caught by the
    firewall, alarmed, and (after N) disarmed-to-legacy. Fail-closed by design."""


# ---- observation building (collect.py shape -> decide_bg obs) ----------------

DEFAULT_CTX_TTL_S = 120.0  # a detector file older than this is treated as UNKNOWN


def _resolve_live_blue_sid(orchestra_dir, root):
    """(b) P0.6-live-sid: the LIVE pane-occupant sid for `root`, reusing
    blocker_surface_watchdog's named R1 mechanism — session-index's declaration-
    resolved sid in ``<orchestra_dir>/state/agent-sessions.json`` (mirrors
    ``sid_resolver_from_sessions``: reads the row's ``session_id`` only). Returns the
    live sid, or None when the file/row/sid is absent or null. FAIL-SOFT: any read
    error => None (build_obs then falls back to the canonical seat sid — never a
    raise into the beat firewall)."""
    if not orchestra_dir or not root:
        return None
    try:
        with open(os.path.join(orchestra_dir, "state", "agent-sessions.json")) as fh:
            row = (json.load(fh) or {}).get(root) or {}
        sid = row.get("session_id")
        return sid or None
    except (OSError, ValueError, TypeError, AttributeError):
        # AttributeError guards a JSON-ARRAY-shaped file (.get on a list); ValueError
        # covers JSONDecodeError. Fail-soft: never a raise into the beat firewall.
        return None


def build_obs(agent, blue, wal_dir=None, now=None, ctx_ttl_s=None, detector_dir=None,
              live_sid_fn=None, status_fn=None, attached_fn=None, pending_cards_fn=None,
              screen_fn=None, meta_store=None):
    """Map one collected-fleet agent + its canonical Blue record to the obs dict
    `decide_bg` consumes. collect.py gives ctx.status_bar_pct as a PERCENT int and
    death as a dict; decide_bg wants a 0..1 fraction and a death TOKEN.

    M3 (leg-(ii)): when ``wal_dir`` is given, source the green's REAL live sid from
    ``<wal_dir>/<green_alias>.sid`` (written by capture_green_sid at the green's
    SessionStart) into ``green["session_id"]`` so the swap attributes it at promote
    (canonical no longer ends session_id=null). Absent file => no key (never
    fabricate). ``wal_dir=None`` is legacy-identical (older callers/tests)."""
    import time
    from scripts.lineage_daemon.death import death_signal

    now = time.time() if now is None else now
    ttl_s = DEFAULT_CTX_TTL_S if ctx_ttl_s is None else ctx_ttl_s
    detector_dir = "/tmp" if detector_dir is None else detector_dir

    # v2 (the operator #1, data-driven): the runtime is resolved FIRST (a data key, no
    # conditional) so the ctx-source ladder can consult the provider-adapter registry.
    runtime = (agent.get("runtime") or DEFAULT_RUNTIME).strip().lower()
    # P0.6 ctx-source ladder (blind-telemetry fix): the provider-ADAPTER read_ctx (keyed
    # on runtime via the registry — the detector file for the default provider, the direct
    # token readers for the others) FIRST, then status_bar; an UNKNOWN reading NEVER
    # coerces to 0.0 — retain the last-valid value + age and flag ctx_unknown so decide_bg
    # alarms instead of a silent 0-noop.
    ctx = agent.get("ctx") or {}
    _sb = ctx.get("status_bar_pct")
    _sb_pct = (_sb / 100.0) if isinstance(_sb, (int, float)) else None
    # (b) P0.6-live-sid: key the detector read on the LIVE pane-occupant sid (C1/R1 —
    # never the stale canonical seat sid), falling back to the canonical sid only when
    # no live sid is resolvable (no regression). The mismatch is surfaced for
    # observability (a fresh-occupant signal), non-gating.
    _canonical_sid = blue.get("session_id")
    _live_sid = None
    if live_sid_fn is not None:
        try:
            _live_sid = live_sid_fn(agent.get("agent_id"))
        except Exception:  # noqa: BLE001 — fail-soft: a resolver hiccup falls back
            _live_sid = None
    _ctx_sid = _live_sid or _canonical_sid
    _sid_source = "live" if _live_sid else "canonical"
    _sid_mismatch = bool(_live_sid and _canonical_sid and _live_sid != _canonical_sid)
    _adp_pct, _adp_fresh = _read_ctx(
        runtime, agent.get("agent_id"),
        detector_dir=detector_dir, sid=_ctx_sid, now=now, ttl_s=ttl_s)
    _bgst = BgStateStore(wal_dir, agent.get("agent_id")) if wal_dir else None
    if _adp_pct is not None and _adp_fresh:
        ctx_pct, ctx_source, ctx_unknown, ctx_age_s = _adp_pct, "adapter", False, 0.0
    elif _sb_pct is not None:
        ctx_pct, ctx_source, ctx_unknown, ctx_age_s = _sb_pct, "status_bar", False, 0.0
    else:
        # UNKNOWN — retain last-valid (+ its age) rather than pretend the ctx is empty.
        _lv = _bgst.read_meta("last_valid_ctx_pct") if _bgst else None
        _lts = _bgst.read_meta("last_valid_ctx_ts") if _bgst else None
        if _lv is not None:
            ctx_pct, ctx_source, ctx_unknown = _lv, "stale", True
            ctx_age_s = (now - _lts) if _lts is not None else None
        else:
            ctx_pct, ctx_source, ctx_unknown, ctx_age_s = None, "unknown", True, None
    if _bgst is not None and not ctx_unknown:
        # NEVER store an un-normalized fraction: a >1 (or <0) ctx poisons the arm gate's
        # retained-idle path (inspiration 1.43 refused as retained-out-of-range). Guard at
        # the WRITE — skip + LOUD breadcrumb (reason ctx:out-of-range), never silently clamp.
        if ctx_pct is not None and 0.0 <= ctx_pct <= 1.0:
            _bgst.write_meta("last_valid_ctx_pct", ctx_pct)
            _bgst.write_meta("last_valid_ctx_ts", now)
        else:
            _bgst.write_meta("last_ctx_out_of_range",
                             {"pct": ctx_pct, "source": ctx_source, "ts": now})
            try:
                import sys as _sys
                print(f"[bg_beat] ctx:out-of-range write skipped pct={ctx_pct} "
                      f"source={ctx_source} (never clamp)", file=_sys.stderr)
            except Exception:  # noqa: BLE001 — logging must never break the beat
                pass
    _death_obs = agent.get("death") or {}
    death = death_signal(_death_obs)
    # P1.5 lull band: carry Blue's idle state + age (collect nests them under `death`) so
    # decide_bg can cut over at a lull in [PREWARM, SWAP) instead of the 0.80 wall.
    lull_state = _death_obs.get("state")
    lull_state_age_s = _death_obs.get("state_age_s") or 0
    # DEC-1789517918317482 §3A: collect is claude-hook-scoped, so codex/gemini blues carry
    # death.state None and the idle legs were unreachable. Fall back to the SAME oracle the
    # arm gate keys on (agent-status: deriver + screen anatomy, runtime-agnostic). Live
    # defaults engage only when wal_dir is given (legacy callers/tests stay probe-free).
    _live = wal_dir is not None
    _root = agent.get("agent_id")
    if lull_state is None:
        if status_fn is None and _live:
            status_fn = _default_status_fn
        if status_fn is not None:
            try:
                lull_state = (status_fn(_root) or {}).get("state")
            except Exception:  # noqa: BLE001 — unknown => not idle (fail-closed)
                lull_state = None
    # G2 age truth (gm msg_51be17ad, by effect 00:45Z): collect.py:168 fills death.state for
    # EVERY runtime from the gathered status, and for a codex/gemini seat its state_age_s is
    # the deriver SNAPSHOT age (1-4 s), never observed idle. So observe idle on EVERY beat
    # whatever the state source: persist idle_since in bg meta on the first observed idle,
    # clear on any non-idle read; state_age_s = max(collect age, observed age) keeps a real
    # claude hook age and supersedes a snapshot age. Never inferred backwards.
    if meta_store is None and _live:
        meta_store = BgStateStore(wal_dir, _root)   # module-level import (:164 uses it too)
    lull_state_age_s = max(float(lull_state_age_s or 0),
                           float(_observed_idle_age(meta_store, lull_state, now)))
    blue_turn_complete = lull_state == "idle"
    blue_attached = _probe(attached_fn, _default_attached_fn if _live else None, _root,
                           default=True)
    blue_pending_cards = _probe(pending_cards_fn,
                                _default_pending_cards_fn if _live else None, _root,
                                default=0)
    blue_composer_text = _probe(screen_fn and (lambda r: _composer_of(screen_fn(r))),
                                (lambda r: _composer_of(_default_screen_fn(r))) if _live else None,
                                _root, default=None)
    # v2 DATA-DRIVEN calibration (the operator #1, gm RULING msg_02d19242): a seat is calibrated
    # when it presents a ctx that is PRESENT, FRESH (not a retained/stale read), and in
    # range 0..1 — NO runtime-name check. Every provider that its adapter can read a fresh
    # normalized ctx for is calibrated; absent/stale/out-of-range fails closed for EVERY
    # runtime (incl the default). Death still dominates calibration regardless (decide_bg).
    # GOAL-FINDING #2 idle-ceiling + RETENTION WINDOW (gm ruling msg_de091167): an IDLE
    # seat's ctx cannot have moved since its last render — BUT only if the read was captured
    # INSIDE the current idle stretch. A RETAINED last-valid value (ctx_unknown,
    # ctx_source=='stale') calibrates the ceiling only when state=='idle' AND the seat has
    # been idle at least as long as the read is old (lull_state_age_s >= ctx_age_s). A read
    # that PREDATES a busy stretch (state_age_s < ctx_age_s: the seat went busy-then-idle
    # after the read) is fail-closed — its ctx could have moved during that busy stretch.
    # A BUSY seat's ctx IS moving, so a stale read stays fail-closed (TTL rejection for busy
    # only). A wholly-unknown ctx (ctx_pct None, no retained value) is never calibrated.
    _stale_on_idle_within_retention = (
        lull_state == "idle" and ctx_age_s is not None
        and (lull_state_age_s or 0) >= ctx_age_s)
    calibrated = (ctx_pct is not None) and (0.0 <= ctx_pct <= 1.0) and (
        (not ctx_unknown) or _stale_on_idle_within_retention)
    green = {"generation": blue["generation"] + 1, "model": blue.get("model")}
    if wal_dir:
        # M3: the green wrote its sid to <wal_dir>/<alias>.sid at SessionStart; the
        # alias is the PROJECTED provisional name {root}-g{N} (matches bg_arm's
        # _green_alias_for + projector._alias_payload). Absent => leave it out.
        from scripts.lineage_daemon.wal.capture_green_sid import read_green_sid
        _sid = read_green_sid(wal_dir, f"{agent.get('agent_id')}-g{green['generation']}")
        if _sid:
            green["session_id"] = _sid
    return {
        "root": agent.get("agent_id"),
        "runtime": runtime,
        "ctx_pct": ctx_pct,
        "ctx_source": ctx_source,       # P0.6: detector | status_bar | stale | unknown
        "ctx_unknown": ctx_unknown,     # P0.6: True => blind read, decide_bg alarms
        "ctx_age_s": ctx_age_s,         # P0.6: age of a retained (stale) reading, else 0/None
        "blue_sid_used": _ctx_sid,          # (b): the sid the detector was keyed on
        "blue_sid_source": _sid_source,     # (b): live (occupant) | canonical (seat, fallback)
        "blue_sid_live_mismatch": _sid_mismatch,  # (b): live_sid != canonical seat sid (fresh occupant)
        "state": lull_state,            # P1.5: Blue's activity (idle/busy/stalled) for the lull band
        "state_age_s": lull_state_age_s,  # P1.5: how long Blue has held that state (s)
        "blue_turn_complete": blue_turn_complete,   # DEC-…7482 L3: hook/oracle idle
        "blue_attached": blue_attached,             # G3: the operator attached (unknown => True)
        "blue_pending_cards": blue_pending_cards,   # L3: own pending cards (unknown => 0)
        "blue_composer_text": blue_composer_text,   # G1: '' empty | text | None unreadable
        "death": death,
        "ceiling_calibrated": calibrated,
        "blue_generation_id": blue["blue_generation_id"],
        "green": green,
    }


def _probe(fn, default_fn, root, *, default):
    """Run an injected probe (or the live default when engaged); any raise => the
    fail-closed default."""
    f = fn or default_fn
    if f is None:
        return default
    try:
        v = f(root)
    except Exception:  # noqa: BLE001 — fail-closed
        return default
    return default if v is None else v


def _observed_idle_age(meta_store, state, now):
    """G2: seconds of OBSERVED idle. idle_since is written on the first idle read and
    cleared on any non-idle read; no store => 0 (never inferred backwards)."""
    if meta_store is None:
        return 0
    try:
        if state != "idle":
            if meta_store.read_meta("idle_since") is not None:
                meta_store.write_meta("idle_since", None)
            return 0
        since = meta_store.read_meta("idle_since")
        if since is None:
            meta_store.write_meta("idle_since", now)
            return 0
        return max(0.0, float(now) - float(since))
    except Exception:  # noqa: BLE001 — fail-closed: unknown age => 0
        return 0


def _composer_of(lines):
    from .composer_read import composer_text
    return composer_text(lines)


_AGENT_STATUS_MOD = None


def _default_status_fn(root):
    """§3A live oracle: agent-status.get_agent_status (deriver + screen anatomy). The
    module is loaded once per process (a beat probes every armed root)."""
    global _AGENT_STATUS_MOD
    if _AGENT_STATUS_MOD is None:
        import importlib.util as _ilu
        import os as _os
        p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__)))), "agent-status.py")
        spec = _ilu.spec_from_file_location("agent_status_probe_bg", p)
        m = _ilu.module_from_spec(spec)
        spec.loader.exec_module(m)
        _AGENT_STATUS_MOD = m
    return _AGENT_STATUS_MOD.get_agent_status(root) or {}


def _default_attached_fn(root):
    """G3: True iff any tmux client is attached to the seat's session (bg_complete.py:180
    makes the same call). A tmux error raises => caller's fail-closed default (True)."""
    import subprocess as _sp
    r = _sp.run(["tmux", "list-clients", "-t", root, "-F", "#{client_name}"],
                capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "tmux list-clients failed")
    return bool(r.stdout.strip())


def _default_pending_cards_fn(root):
    """L3: PENDING approval cards authored by the CANONICAL root (never an alias), read
    from the approvals store (APPROVAL_DB_PATH honored). Commitment/human-task cards are
    standing obligations, not a decision the seat's turn is waiting on — excluded."""
    import os as _os
    import sys as _sys
    _sd = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _sd not in _sys.path:
        _sys.path.insert(0, _sd)
    from approval_schema import ApprovalStore
    c = ApprovalStore(db_path=_os.environ.get("APPROVAL_DB_PATH") or None)._conn()
    try:
        row = c.execute(
            "SELECT COUNT(*) FROM approval_requests WHERE from_agent=? AND status='pending' "
            "AND COALESCE(kind,'') NOT IN ('human_task','commitment')", (root,)).fetchone()
        return int(row[0])
    finally:
        c.close()


def _default_screen_fn(root):
    import subprocess as _sp
    r = _sp.run(["tmux", "capture-pane", "-p", "-t", root], capture_output=True, text=True,
                timeout=10)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "tmux capture-pane failed")
    return r.stdout.splitlines()


def detect_green_sid_silent_null(wal_dir, root, green_alias, *, sid_reader=None):
    """M3h: surface the silent-null edge M3 does NOT cover. M3 attributes the green's sid
    at promote ONLY when it is KNOWN; a green that reached READY but whose SessionStart
    sid-hook silently failed to write ``state/wal/{alias}.sid`` yields NO sid, so the
    promote attributes nothing and canonical.session_id ends NULL silently (the rotation
    landmine). A green .sid is written at SessionStart — BEFORE probe.json — so a
    verified-READY green MUST already have it. Return True + stamp a durable
    ``green_sid_missing_alarm`` meta when the seat is READY but the .sid is absent; else
    False. NON-GATING: this is an arm-precondition surface (caught before any arm), not a
    state-machine block."""
    from .bg_state import BgStateStore
    from .capture_green_sid import read_green_sid
    st = BgStateStore(wal_dir, root)
    if st.read().get("state") != "READY":
        return False               # only a promotable (READY) green matters
    reader = sid_reader or read_green_sid
    if reader(wal_dir, green_alias) is not None:
        return False               # sid present => M3 will attribute it (clean)
    st.write_meta("green_sid_missing_alarm", green_alias)
    return True


def read_canonical_blue(orchestra_dir, root, connect=None):
    """Resolve the Blue generation for `root` from the canonical DB (write-truth).
    Fail-closed: raises SwapPreconditionError if the root has no canonical row
    (never guess a generation for a live swap)."""
    from identity_store import orchestra_db
    from identity_store.identity_writer import SwapPreconditionError

    db_path = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    conn = (connect or orchestra_db.get_connection)(db_path)
    try:
        # #15: LEFT JOIN the lineage runtime so blue_record carries the seat's declared
        # runtime through to build_swap_documents — a promoted gemini seat needs runtime
        # to derive `agy --conversation <cid>`; without it the promote stamps
        # resume_command=None (unresumable, silent seat-corruption). LEFT so a missing
        # lineage row never fails-closed the swap (runtime falls to None -> refuses to
        # guess a resume_command, never guesses claude).
        row = conn.execute(
            "SELECT g.id AS blue_generation_id, g.generation, g.model, g.session_id, "
            "l.runtime AS runtime "
            "FROM canonical c JOIN generations g ON g.id = c.generation_id "
            "LEFT JOIN lineages l ON l.root = c.root "
            "WHERE c.root = ?", (root,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SwapPreconditionError(
            f"no canonical generation for {root!r} — cannot resolve Blue for a "
            f"blue-green swap (write-truth absent); fail-closed")
    # P0.6: surface Blue's OWN typed sid (may be NULL until attributed) so build_obs
    # can read its live detector file /tmp/claude-ctx-{sid}.json (the ctx source).
    return {"blue_generation_id": row["blue_generation_id"],
            "generation": row["generation"], "model": row["model"],
            "session_id": row["session_id"], "runtime": row["runtime"]}


# ---- real seams (INERT delivery: swap/register/project real, effects stubbed) -

class SeamTimeout(Exception):
    """A wired effect seam (spawn/verify/hydrate) exceeded its wall-clock budget
    (v2-2). Surfaced as a raise so the beat firewall alarms + disarms — a hung seam
    can NEVER wedge the fleet beat."""


def bounded_call(fn, timeout_s, *, what):
    """Run fn() under a wall-clock budget, returning its value. A hang -> SeamTimeout
    (Python threads aren't cancellable, so the worker is detached daemon-style, same
    as SwapExecutor's runner); a raised fn re-raises. This is how v2-2 time-bounds
    every seam the firewall would otherwise only catch on a raise, not a hang."""
    import threading
    box = {"val": None, "err": None, "done": False}

    def _worker():
        try:
            box["val"] = fn()
            box["done"] = True
        except Exception as exc:  # noqa: BLE001 -- re-raised to the caller below
            box["err"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise SeamTimeout(f"{what} exceeded {timeout_s}s — raising so the beat "
                          f"firewall alarms/disarms (never wedge)")
    if box["err"] is not None:
        raise box["err"]
    return box["val"]


class _Seams:
    """The 7 dependency-injected seams bg_arm.beat consumes. register_provisional /
    project_now / swap bind to the LIVE identity store (real_seams.make_*). The 4
    effect seams (spawn/verify/hydrate/reap) are WIRED (live-pane drill) to the real
    modules — setsid-detached spawn (spawn_green), wal-probe verify (verify_green),
    delta hydrate (hydrate_green), grandchild-safe reap (reaper). Every effect seam
    is time-bounded (v2-2): a hang converts to SeamTimeout the firewall catches."""

    def __init__(self, *, register_provisional, project_now, swap,
                 spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn=None,
                 green_ingested_seq_fn=None, timeout_s=120.0):
        self.register_provisional = register_provisional
        self.project_now = project_now
        self.swap = swap
        self._spawn_fn = spawn_fn
        self._verify_fn = verify_fn
        self._hydrate_fn = hydrate_fn
        self._reap_fn = reap_fn
        self._produce_fn = produce_fn
        self._green_ingested_seq_fn = green_ingested_seq_fn
        self._timeout_s = timeout_s

    def spawn(self, root, green_alias):
        return bounded_call(lambda: self._spawn_fn(root, green_alias),
                            self._timeout_s, what=f"spawn({root})")

    def verify(self, root, green_alias):
        return bounded_call(lambda: self._verify_fn(root, green_alias),
                            self._timeout_s, what=f"verify({root})")

    def hydrate(self, root, green_alias, since_seq):
        return bounded_call(lambda: self._hydrate_fn(root, green_alias, since_seq),
                            self._timeout_s, what=f"hydrate({root})")

    def reap(self, root, blue):
        # reap is also bounded here; the completion phase (bg_complete) additionally
        # bounds it — belt+suspenders on the highest-blast-radius seam.
        return bounded_call(lambda: self._reap_fn(root, blue),
                            self._timeout_s, what=f"reap({root})")

    def produce(self, root, green_alias):
        # bar#4 seq-A: the green-boot producer (bounded). No-op if unwired (a base
        # without green_boot_probe, or the drill). bg_arm additionally guards the
        # call, so a producer error/timeout is fail-safe (verify then fail-closes).
        if self._produce_fn is None:
            return None
        return bounded_call(lambda: self._produce_fn(root, green_alias),
                            self._timeout_s, what=f"produce({root})")

    def green_ingested_seq(self, green_alias):
        # M1b-h: the green's ACTUAL ingested through_seq (bounded). No-op (None) if
        # unwired. bg_arm additionally guards the call and treats None as NOT-verified,
        # so an unreadable ingest fail-closes the non-death swap to a defer (never a
        # blind promote).
        if self._green_ingested_seq_fn is None:
            return None
        return bounded_call(lambda: self._green_ingested_seq_fn(green_alias),
                            self._timeout_s, what=f"green_ingested_seq({green_alias})")


def _seat_cwd(orchestra_dir, root):
    """Best-effort seat cwd (the green inherits it) for the Layer-2 liveness cwd
    binding. Reads the flat registry.json; None if unresolvable — green_liveness then
    skips the cwd belt-suspender and relies on the stronger declared_identity anchor."""
    import json
    try:
        with open(os.path.join(orchestra_dir, "registry.json")) as fh:
            agents = json.load(fh).get("agents") or {}
        return (agents.get(root) or {}).get("cwd")
    except (OSError, ValueError):
        return None


def _default_register_green_sid_fn(orchestra_dir):
    """(a) The live green-side #1 registrar for the autonomous beat: resolve the green's
    cid provider-agnostically (resolve_cid_any) and write it DB-first (update_session) +
    reproject + .sid. Bounded-poll so a still-booting green is a clean miss (None), never a
    hang. Zero runtime literals — resolver + writers are the injected registries."""
    def register(green_alias):
        from . import capture_green_sid as _cgs
        from .ctx_adapters import resolve_cid_any
        from identity_store import identity_writer
        return _cgs.register_green_session(
            orchestra_dir, green_alias, resolve_cid_fn=resolve_cid_any,
            update_session_fn=lambda aid, fields, full_record=None:
                identity_writer.update_session(orchestra_dir, aid, fields,
                                               full_record=full_record),
            project_fn=lambda: identity_writer.project_now(orchestra_dir),
            poll_attempts=6, sleep_fn=lambda: __import__("time").sleep(2))
    return register


def _default_green_wake_fn(orchestra_dir, wal_dir, root, ingest_text=None, *,
                           capture_fn=None, send_fn=None, sleep_s=3):
    """B3 adoption: the PRODUCTION provider-agnostic green waker (#3/#4), threaded into the
    autonomous beat's BgArm so an armed seat's booted green is ACTUALLY woken to ingest its
    hydrate digest (not left to the legacy verify-only PREWARM->READY). Mirrors the one-off
    fire driver's waker but production-shaped: the default ingest instruction just says
    ingest-then-hold (no demo drill checklist), and it keys off ``root`` (not a module global).
    Zero runtime literals — the started-probe resolves the green's own 'You are <green_alias>'
    declaration via resolve_cid_any, so the SAME waker drives ANY runtime's green (each provider's
    own store). Idempotent across beats (send once, persisted in bg_state; Enter-resend only when
    idle). ``ingest_text`` overrides the default instruction (a DEMO fire supplies a lossless-proxy
    checklist task for a demo seat with no real work; production leaves it None)."""
    import subprocess
    from .green_wake import wake_green_to_ingest
    from .ctx_adapters import resolve_cid_any

    ingest_text = ingest_text or (
        "ingest your hydrate digest (your predecessor's handoff + delivered context), "
        "then hold — do not start new work until told.")

    from .composer_read import composer_text
    if send_fn is None:
        def send_fn(args):
            subprocess.run(["tmux", "send-keys", *args], capture_output=True)
    if capture_fn is None:
        def capture_fn(target):
            out = subprocess.run(["tmux", "capture-pane", "-t", target, "-p"],
                                 capture_output=True, text=True)
            return (out.stdout or "").splitlines()

    def wake(green_alias):
        st = BgStateStore(wal_dir, root)
        def inject(text):
            send_fn(["-t", green_alias, "-l", text])
            send_fn(["-t", green_alias, "Enter"])
        last_frame = {"lines": None}
        def probe_started():
            # SUBMIT-VERIFY by effect (gm msg_e7d7f734 (a); live 01:30Z the codex green held
            # the wake UNSUBMITTED): this send has started only when the composer reads EMPTY
            # after it. The old probe (resolve_cid_any(green_alias) is not None) only proved
            # the green's FIRST boot turn, so confirm-started was vacuously true and the
            # bounded Enter-resend never fired. Unreadable composer (None) => not started =>
            # a bare Enter on the pane (harmless on an empty composer, bounded by retries).
            lines = capture_fn(green_alias)
            if composer_text(lines) == "":
                last_frame["lines"] = lines
                return True
            # STALE-SCREEN guard (gm msg_86bcc168 item 1; semantic-recall-wiring-dev-g5 sat
            # PREWARMING 2.5h 2026-09-16): a Claude TUI can stop repainting after Enter/C-u,
            # so two byte-identical captures around a resend are PIXELS, not state. Kick the
            # repaint with one benign printable + BSpace (a no-op on the composer either
            # way) and re-read before calling this send not-started.
            if last_frame["lines"] is not None and lines == last_frame["lines"]:
                send_fn(["-t", green_alias, "-l", " "])
                send_fn(["-t", green_alias, "BSpace"])
                lines = capture_fn(green_alias)
            last_frame["lines"] = lines
            return composer_text(lines) == ""
        def reenter():
            send_fn(["-t", green_alias, "Enter"])
        def probe_idle():
            tail = "\n".join(capture_fn(green_alias))[-1200:].lower()
            busy = ("running" in tail or "esc to interrupt" in tail
                    or "queued message" in tail)
            return not busy
        started = wake_green_to_ingest(
            inject, probe_started, reenter, ingest_text=ingest_text, max_retries=3,
            sleep_fn=lambda: __import__("time").sleep(sleep_s),
            probe_idle_fn=probe_idle,
            already_sent_fn=lambda: bool(st.read_meta("ingest_wake_sent")),
            mark_sent_fn=lambda: st.write_meta("ingest_wake_sent", True))
        return started

    return wake


def _page_green_wake_failed(st, alarm_fn, root, green_gen, summary):
    """gm msg_86bcc168 item 1(b): BgArm._wake_and_verify_ingest writes green_wake_failed_alarm
    but nothing paged it (semantic-recall-wiring-dev PREWARMING 21:48-00:35 ET silently).
    Page ONCE per episode through alarm_fn like green-quota; the episode key is the green's
    sid (bg meta green_session_id), else the green alias. BgArm clears the flag when a later
    wake succeeds, so a recovered green stops paging on its own. Best-effort, never raises."""
    try:
        if not st.read_meta("green_wake_failed_alarm"):
            return
        # The flag only means anything while the seat is PREWARMING: once READY/DRAINED the
        # green did wake (live semantic-recall-wiring-dev carried a stale True after its swap).
        if (st.read() or {}).get("state") != "PREWARMING":
            return
        alias = f"{root}-g{green_gen}"
        key = st.read_meta("green_session_id") or alias
        if st.read_meta("green_wake_failed_paged") == key:
            return
        alarm_fn(kind="green-wake-failed", root=root, consecutive=1, page=True,
                 detail=(f"green {alias} (sid {key}) never started its ingest turn after the "
                         f"bounded Enter resends; seat stays PREWARMING, not READY. Check the "
                         f"green pane: composer text still sitting => the TUI may need a "
                         f"printable key to repaint; empty composer + no turn => wake lost."))
        st.write_meta("green_wake_failed_paged", key)
        summary["alarms"] = summary.get("alarms", 0) + 1
        summary["green_wake_failed"] = summary.get("green_wake_failed", 0) + 1
    except Exception:  # noqa: BLE001 — paging is best-effort; never wedge the beat
        pass


def _default_green_quota_fn(wal_dir, root, runtime):
    """Vendor-quota gate default (gm msg_7b3ffa11): the green's quota by ITS runtime's own
    truth (green_quota.GREEN_QUOTA_READERS), keyed on the green's sid (bg meta
    green_session_id from capture, else resolve_cid_any live). None => unknown => no gate."""
    from .green_quota import green_quota_any
    from .ctx_adapters import resolve_cid_any

    def quota(green_alias):
        sid = BgStateStore(wal_dir, root).read_meta("green_session_id")
        if not sid:
            sid = resolve_cid_any(green_alias)
        return green_quota_any(runtime, green_alias, sid)
    return quota


def _default_green_progress_fn(orchestra_dir, wal_dir):
    """(b) Green-keyed progress high-water — NEVER the blue root's WAL. Resolve the green's
    live sid (bg_state green_session_id from (a), else resolve_cid_any live each beat so (b)
    is robust to (a)'s spawn-time timing), then read that cid's own progress via the
    green-progress registry; fall back to the GREEN's own WAL db <green_alias>.db. A green
    that cannot be resolved AT ALL returns 0 (flat) => the stall bound accrues (correct: an
    unresolvable green is a real problem), but a green advancing on its own cid climbs =>
    the stall counter resets (no false stall)."""
    from .store import WalStore
    from .bg_state import BgStateStore
    from .ctx_adapters import green_progress_any, resolve_cid_any

    def progress(root_, green_alias):
        sid = BgStateStore(wal_dir, root_).read_meta("green_session_id")
        if not sid:
            sid = resolve_cid_any(green_alias)
        v = green_progress_any(sid) if sid else None
        if v is not None:
            return v
        # fallback: the GREEN's own captured WAL (never the root/blue db)
        try:
            gs = WalStore(os.path.join(wal_dir, f"{green_alias}.db"))
            try:
                return gs.max_seq()
            finally:
                gs.close()
        except Exception:  # noqa: BLE001 — absent green WAL => 0 (flat)
            return 0
    return progress


def _live_effect_seams(root, orchestra_dir, wal_dir, blue, *,
                       answer_fn=None, deliver_fn=None, live_fn=None,
                       register_green_sid_fn=None, green_progress_fn=None):
    """Bind the 4 real effect-seam callables to bg_arm's (root, green_alias, ...)
    signatures. Lazy imports keep bg_beat importable on a base without them."""
    from .store import WalStore
    from . import spawn_green as _spawn
    from . import verify_green as _verify
    from . import hydrate_green as _hydrate
    from . import reaper as _reaper

    # (a) green-side #1 registrar (default = the live one) + (b) green-keyed progress reader.
    register_green_sid_fn = register_green_sid_fn or _default_register_green_sid_fn(orchestra_dir)
    green_progress_fn = green_progress_fn or _default_green_progress_fn(orchestra_dir, wal_dir)

    # CROSS-THREAD SAFETY (real-fire wall #5): the DB-touching seams (verify/hydrate
    # + the stall progress_fn) run INSIDE bounded_call's WORKER thread (the v2-2
    # SeamTimeout bound). A sqlite3 connection can only be used in the thread that
    # CREATED it, so a single WalStore built here at bind-time (main thread) and used
    # from the worker raises ProgrammingError. Therefore open the WalStore INSIDE
    # each seam call (per-thread) — construction + use in the same worker thread.
    _db_path = os.path.join(wal_dir, f"{root}.db")

    def _open_store():
        return WalStore(_db_path)

    def spawn_fn(root_, green_alias):
        # (a) thread the green-side #1 registrar so spawn_green registers the green's live
        # cid DB-first + flat + .sid BEFORE returning (Blocker-1 applied to the green).
        return _spawn.spawn_green(root_, green_alias, orchestra_dir=orchestra_dir,
                                  wal_dir=wal_dir,
                                  register_green_sid_fn=register_green_sid_fn)

    from . import verify_stall as _stall

    # Liveness gate (hoisted so BOTH _raw_verify AND the stall bound's liveness-before-stall
    # rule use the SAME runtime-keyed liveness dispatcher). Injected live_fn wins (tests).
    if live_fn is not None:
        _liveness_fn = live_fn
    else:
        from . import green_liveness as _live

        def _liveness_fn(r_, g_):
            return _live.green_is_live(r_, g_, wal_dir=wal_dir,
                                       orchestra_dir=orchestra_dir,
                                       expected_cwd=_seat_cwd(orchestra_dir, r_))

    def _raw_verify(root_, green_alias):
        # answer_fn defaults to reading Green's committed probe-answer artifact;
        # None answer => still warming => verify False (never a timed pass).
        af = answer_fn or _default_probe_answer_fn(orchestra_dir)
        # bar#4 gap-c: grade on the BLUE-AUTHORITATIVE delivered scope, sourced from
        # bg_state (since=first_hydrated_seq baseline, through=last_hydrated_seq
        # high-water) — NOT the union of the rows Green received (a whole-row loss
        # would shrink that and mask itself). A mid-life Green graded on the full WAL
        # would false-fail; scope=None (nothing hydrated yet) => full-WAL, but a
        # not-yet-produced answer is already None => verify False, so no false pass.
        from .bg_state import BgStateStore
        _st = BgStateStore(wal_dir, root_)
        _through = _st.read_meta("last_hydrated_seq", 0) or 0
        scope = None
        if _through:
            scope = {"since_seq": _st.read_meta("first_hydrated_seq", 0) or 0,
                     "through_seq": _through}
        # Layer-2 green-liveness gate (DEC-1788655588): ANDed with the channel probe
        # so a green frozen/hung PRE-READY (trust dialog / init hang) — which the
        # orchestrator-produced channel probe cannot see — fails verify and is never
        # promoted (reap-blue-promote-dead). Runtime-keyed dispatch inside green_is_live.
        return _verify.verify_green(root_, green_alias, wal_dir=wal_dir,
                                    store=_open_store(), answer_fn=af, scope=scope,
                                    live_fn=_liveness_fn)

    # v2-3: wrap verify with the stall bound — a Green wedged for M beats raises
    # VerifyStalled -> firewall alarms/disarms (no silent PREWARMING stall). (b) FIX: the
    # progress high-water is the GREEN's OWN progress keyed on its sid (green_progress_fn),
    # NEVER the blue root's WAL (state/wal/<root>.db) — the false-stall the live re-fire #3
    # surfaced (the green was ingesting on its own cid while the root WAL sat flat). A live
    # warmup climbs => the stall counter resets; a genuinely wedged green stays flat => trips.
    verify_fn = _stall.verify_with_stall_bound(
        verify_fn=_raw_verify,
        progress_fn=green_progress_fn,
        wal_dir=wal_dir,
        # (step 5) liveness-before-stall: a LIVE green that is merely holding after ingest
        # (flat progress) is READY-eligible, not wedged — only flat AND not-live trips.
        live_fn=_liveness_fn)

    def hydrate_fn(root_, green_alias, since_seq):
        return _hydrate.hydrate_green(root_, green_alias, since_seq=since_seq,
                                      wal_dir=wal_dir, store=_open_store(),
                                      deliver_fn=deliver_fn,
                                      orchestra_dir=orchestra_dir)

    def reap_fn(root_, blue_generation_id):
        # P0.1: prefer the IMMUTABLE blue pane pid recorded at prewarm (the dead-pane
        # landmine fix); pass the live GREEN pane pid for the never-reap-green guard.
        # Green gen = blue gen + 1 (projector `{root}-g{N}` convention). A None recorded
        # pid => reap_blue_generation falls back to the legacy tmux-name resolver.
        from .bg_state import BgStateStore
        recorded = BgStateStore(wal_dir, root_).read_meta("blue_pane_pid")
        green_alias = f"{root_}-g{blue['generation'] + 1}"
        green_pane = _reaper._default_pane_pid_fn(green_alias)
        return _reaper.reap_blue_generation(orchestra_dir, root_, blue_generation_id,
                                            recorded_blue_pid=recorded,
                                            green_pane_pid=green_pane)

    def produce_fn(root_, green_alias):
        # bar#4 seq-A (gm Option-1): the REAL green-boot producer reads the
        # DELIVERED lineage_hydrate rows (metadata['ingested_view']), reconstructs
        # the probe answer via the shared compute path, and commits <green>.probe.json.
        # Lazy import keeps bg_beat importable on a base without green_boot_probe;
        # absent => no-op (drill uses drill-inject instead). bg_arm guards the call.
        try:
            from . import green_boot_probe as _gbp
        except ImportError:
            return None
        return _gbp.produce_probe_answer(orchestra_dir, root_, green_alias)

    return spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn


def _default_probe_answer_fn(orchestra_dir):
    """Read Green's committed probe answer (state/agent-handoffs/<green>.probe.json).
    Absent => None => verify False (Green still warming). Green writes it on boot
    after ingesting the hydrate digest."""
    def answer_fn(root, green_alias):
        import json
        path = os.path.join(orchestra_dir, "state", "agent-handoffs",
                            f"{green_alias}.probe.json")
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    return answer_fn


def _green_ingested_seq_reader(orchestra_dir):
    """M1b-h reader closure: the green's ACTUAL ingested through_seq from its
    ACTUALLY-DELIVERED lineage_hydrate rows. Lazy import (green_boot_probe pulls
    msg_store) keeps bg_beat importable on a base without it. RAISES on no-row / no
    artifact — bg_arm._green_ingested_through guards it to None (fail-closed defer)."""
    def read(green_alias):
        from .green_boot_probe import collect_ingested_artifact
        return collect_ingested_artifact(orchestra_dir, green_alias)[2].get("through_seq")
    return read


def real_seams_for(root, orchestra_dir, blue, cwd=None, now=None, wal_dir=None):
    """Build the seams object for an armed lineage. Loads real_seams lazily so a
    base that predates identity_writer.project_now still imports this module. The 4
    effect seams are WIRED to the real live-pane-drill modules."""
    from .real_seams import (make_swap_fn, make_register_provisional_fn,
                             make_project_now_fn)

    # #15: carry the seat's runtime into blue_record so make_swap_fn -> build_swap_documents
    # can derive the promoted seat's per-runtime resume_command (missing runtime -> the
    # builder refuses, never guesses claude).
    blue_record = {"generation": blue["generation"], "model": blue.get("model"),
                   "runtime": blue.get("runtime")}
    wal_dir = wal_dir or os.path.join(orchestra_dir, "state", "wal")
    spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn = _live_effect_seams(
        root, orchestra_dir, wal_dir, blue)
    # M1b-h: the green's ACTUAL ingested through_seq, read from its ACTUALLY-DELIVERED
    # lineage_hydrate rows in <orchestra_dir>/state/tasks.db (the same channel the
    # hydrate seam writes). bg_arm's non-death swap requires this >= the final cut H.
    return _Seams(
        register_provisional=make_register_provisional_fn(orchestra_dir),
        project_now=make_project_now_fn(orchestra_dir),
        swap=make_swap_fn(orchestra_dir, blue_record, cwd=cwd, now=now),
        spawn_fn=spawn_fn, verify_fn=verify_fn, hydrate_fn=hydrate_fn,
        reap_fn=reap_fn, produce_fn=produce_fn,
        green_ingested_seq_fn=_green_ingested_seq_reader(orchestra_dir))


# ---- arming set (the measured, sub-microsecond is_armed stat/agent) ----------

def armed_roots(agents, wal_dir, force_roots=None):
    """The roots armed for async Blue-Green THIS beat. One `is_armed` filesystem
    stat per agent (honors the global BG_DISABLED kill-switch). This is the whole
    unarmed-beat cost — measured, not asserted (the operator-cron requirement).

    ``force_roots`` is the SUPERVISED hand-drive allowlist (leg (i) —
    bg_live_beat.py --manual-override): a collected root it names is armed for THIS
    beat REGARDLESS of the global BG_DISABLED kill-switch and its per-lineage
    bg_enabled flag. It is an in-memory, per-beat override ONLY — it writes no disk
    state and touches no flag. Every autonomous caller (cron_beat / telemetryd)
    passes NOTHING, so with BG_DISABLED present they still strict-no-op and there is
    no second driver to race the manual beat."""
    force = set(force_roots or ())
    out = []
    for a in agents:
        root = a.get("agent_id")
        if root and (root in force or is_armed(wal_dir, root)):
            out.append(root)
    return out


# ---- anti-orphan failure counter (persisted across beats/processes) ----------

def _failure_path(wal_dir, root):
    return os.path.join(wal_dir, f"{root}.bg_failures")


def _read_failures(wal_dir, root):
    try:
        with open(_failure_path(wal_dir, root)) as fh:
            return int(json.load(fh).get("count", 0))
    except (OSError, ValueError):
        return 0


def _bump_failures(wal_dir, root):
    n = _read_failures(wal_dir, root) + 1
    os.makedirs(wal_dir, exist_ok=True)
    tmp = _failure_path(wal_dir, root) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"count": n}, fh)
    os.replace(tmp, _failure_path(wal_dir, root))
    return n


def _reset_failures(wal_dir, root):
    try:
        os.remove(_failure_path(wal_dir, root))
    except OSError:
        pass


def _default_disarm_fn(wal_dir):
    """Disarm-to-legacy: remove the per-seat bg_enabled flag so the seat falls back
    to the old plan_fleet path on the NEXT beat (no longer excluded)."""
    def disarm(root):
        flag = os.path.join(wal_dir, f"{root}.bg_enabled")
        try:
            os.remove(flag)
        except OSError:
            pass
    return disarm


def _default_alarm_fn(orchestra_dir):
    """A REAL alarm (gm msg_store row), not just a log line (inv 7a). Best-effort:
    its own try/except means an alarm-send failure can never wedge the beat."""
    def alarm(*, kind, root, detail, consecutive, page):
        try:
            import sys
            sys.path.insert(0, os.path.join(orchestra_dir))
            from msg_store import MessageStore
            tag = "PAGE" if page else "ALARM"
            subject = (f"[BG-{tag}] {root}: {kind} (consecutive={consecutive})")
            body = (f"async Blue-Green supervisor caught a bg-failure on armed seat "
                    f"{root!r}: {detail}. "
                    + ("N consecutive failures => DISARMED-to-legacy (bg_enabled "
                       "dropped; seat back on the plan_fleet path next beat). "
                       "Investigate before re-arming." if page else
                       "The old rotation path is unaffected (firewall)."))
            MessageStore().send(
                from_agent="fleet-beat-bg", to_agent="gm",
                type="bg_alarm", priority="high" if page else "medium",
                subject=subject, body=body, source="fleet-beat")
        except Exception:  # noqa: BLE001 -- alarm is best-effort; never wedge
            pass
    return alarm


def _default_prune_provisional_fn(orchestra_dir, root, generation):
    def prune():
        from identity_store import identity_writer
        return identity_writer.prune_provisional(orchestra_dir, root, generation)
    return prune


def _default_reap_pane_fn(orchestra_dir):
    def reap(green_alias):
        # kill the green's tmux pane via the fleet spawn script (handles the process tree).
        # GUARD: only invoke --kill when the session ACTUALLY exists — so a fail-closed beat
        # with no green pane (and every hermetic unit test) never shells into spawn-agent.sh
        # (which touches the registry). No pane => nothing to reap => no-op.
        import subprocess
        from . import spawn_green as _sg
        if not _sg._tmux_has_session(green_alias):
            return
        subprocess.run([os.path.join(orchestra_dir, "spawn-agent.sh"), "--kill", green_alias],
                       capture_output=True, text=True, timeout=30)
    return reap


def prune_failed_green(orchestra_dir, wal_dir, root, green_generation, green_alias, *,
                       prune_fn=None, reap_pane_fn=None):
    """(d) PRUNE-ON-FAIL. On any fail-closed path AFTER a provisional green was registered
    (spawn fail / VerifyStalled / timeout), remove the leaked provisional so NO live
    provisional generations row + pane survive (the M1 delta the live re-fire left).
      * ``prune_fn()`` deletes the provisional DB row (identity_writer.prune_provisional —
        itself GUARDED: refuses a canonical/retired row, idempotent);
      * ``reap_pane_fn(green_alias)`` kills the green's tmux pane;
      * the seat's bg_state is reset to SOLO and the green markers cleared, so a re-arm
        re-spawns cleanly instead of verifying against a gone green.
    Both effects are injected (defaults = live). FAIL-SOFT: we are ALREADY on a failure
    path, so no step may raise — a cleanup hiccup is swallowed (the anti-orphan disarm +
    alarm still fire independently)."""
    prune_fn = prune_fn or _default_prune_provisional_fn(orchestra_dir, root, green_generation)
    reap_pane_fn = reap_pane_fn or _default_reap_pane_fn(orchestra_dir)
    try:
        prune_fn()
    except Exception:  # noqa: BLE001 — fail-soft cleanup
        pass
    try:
        reap_pane_fn(green_alias)
    except Exception:  # noqa: BLE001 — fail-soft cleanup
        pass
    try:
        st = BgStateStore(wal_dir, root)
        for k in ("green_session_id", "ingest_wake_sent", "verify_stall_beats",
                  "verify_last_seq", "wal_empty_beats", "green_pane_pid",
                  "green_pane_id", "green_spawned_at"):
            st.write_meta(k, None)
        st.write_state("SOLO", reason="pruned-failed-green")
    except Exception:  # noqa: BLE001 — fail-soft cleanup
        pass


# ---- the driver --------------------------------------------------------------

def bg_supervise_fleet(agents, *, orchestra_dir, wal_dir, now,
                       cutover_active=None, seams_factory=None, arm_ctor=None,
                       alarm_fn=None, blue_reader=None, disarm_fn=None,
                       orphan_after=ORPHAN_DISARM_AFTER, force_roots=None,
                       green_wake_fn=None, green_ingested_seq_fn=None,
                       blue_wal_event_count_fn=None,
                       max_concurrent_greens=None, inflight_state_fn=None,
                       log_fn=None):
    """One supervised bg pass over the collected fleet. PARALLEL to plan_fleet;
    INERT until >=1 seat armed. Returns a summary dict (never raises out of the
    per-seat firewall). All external effects are dependency-injected for testing;
    the defaults wire the live identity store + a real gm alarm.

    B3 ADOPTION (v2.3 addendum, DEC-1789452018556720): the beat now threads the lossless-seam
    hooks into each armed seat's BgArm — the PRODUCTION provider-agnostic ``green_wake_fn`` (#3/#4
    wake-green-to-ingest; None here => bind the LIVE waker per-root, NOT legacy — BgArm's own
    green_wake_fn=None is the legacy sentinel, and adopting the real waker is the gm-locked
    arm-card precondition), plus ``green_ingested_seq_fn`` / ``blue_wal_event_count_fn``
    (None => BgArm's seams / real-WAL-reader fallback, unchanged). Plus the MAX_CONCURRENT_GREENS
    fan-out cap: a SOLO seat that would boot a NEW green is DEFERRED (fail-SAFE, ``reason=fanout:cap``)
    when in-flight greens >= cap; forced (supervised/death) drives NEVER hit the cap."""
    summary = {"armed": 0, "acted": 0, "failed": 0, "disarmed": 0, "alarms": 0,
               "deferred_fanout": 0}

    # inv 3 + inv 5: one is_armed stat/agent, then short-circuit. This is the whole
    # unarmed-beat cost — no arm, no seams, no state write when nothing is armed.
    force = set(force_roots or ())
    roots = armed_roots(agents, wal_dir, force_roots=force)
    summary["armed"] = len(roots)
    if not roots:
        return summary

    # Lazily bind the live defaults ONLY once at least one seat is armed.
    if cutover_active is None:
        from identity_store import cutover
        cutover_active = lambda: cutover.is_active(orchestra_dir)  # noqa: E731
    arm_ctor = arm_ctor or BgArm
    alarm_fn = alarm_fn or _default_alarm_fn(orchestra_dir)
    blue_reader = blue_reader or (
        lambda root: read_canonical_blue(orchestra_dir, root))
    disarm_fn = disarm_fn or _default_disarm_fn(wal_dir)
    if seams_factory is None:
        seams_factory = lambda root, blue: real_seams_for(  # noqa: E731
            root, orchestra_dir, blue)
    log_fn = log_fn or (lambda line: _append_fleet_beat_log(orchestra_dir, line))
    # B3 fan-out cap + in-flight accounting (provider-agnostic: by STATE, no runtime literal).
    cap = (max_concurrent_greens if max_concurrent_greens is not None
           else _effective_max_concurrent_greens())
    read_state = inflight_state_fn or (
        lambda r: BgStateStore(wal_dir, r).read().get("state"))
    inflight = sum(1 for r in roots if read_state(r) in _INFLIGHT_STATES)
    # B3 hook adoption: bind the LIVE provider-agnostic green waker PER-ROOT unless a caller
    # injected one (tests). None does NOT mean legacy at the beat (see docstring).
    if green_wake_fn is None:
        green_wake_for = lambda r: _default_green_wake_fn(  # noqa: E731
            orchestra_dir, wal_dir, r)
    else:
        green_wake_for = lambda r: green_wake_fn  # noqa: E731  (injected: the single test seat)

    by_id = {a.get("agent_id"): a for a in agents}
    for root in roots:
        agent = by_id.get(root)
        if agent is None:
            # Armed but not in the live collected fleet this beat — unobservable,
            # NOT a failure (acting blind would be worse than waiting; and a false
            # failure here would wrongly march an idle seat toward disarm).
            continue
        # B3 FAN-OUT CAP: a SOLO seat that would boot a NEW green is DEFERRED when in-flight
        # greens >= cap (fail-SAFE — the seat stays SOLO, a freed slot admits it a later tick).
        # Forced roots (supervised / death-safety drives) BYPASS the cap — it NEVER blocks the
        # safety path. An already-in-flight seat (PREWARMING/READY/SWAPPING) is always driven to
        # completion (it already holds its slot).
        state_before = read_state(root)
        if root not in force and state_before == "SOLO" and inflight >= cap:
            log_fn(f"reason=fanout:cap root={root} inflight={inflight} cap={cap} "
                   f"(deferring SOLO->PREWARMING; freed slot admits it later)")
            summary["deferred_fanout"] += 1
            continue
        # (d) PRUNE-ON-FAIL: track the provisional green this beat MIGHT register so the
        # firewall can prune it on a fail-closed raise (None until obs resolves the gen).
        _green_gen = None
        try:
            # inv 1 FIREWALL: everything that could throw lives inside this block;
            # it swallows-to-alarm and continues to the next seat, never re-raises.
            blue = blue_reader(root)              # inv 6 write-truth, fail-closed
            obs = build_obs(                       # M3: source green sid
                agent, blue, wal_dir=wal_dir,
                # (b) P0.6-live-sid: key blue's ctx read on the LIVE pane-occupant sid
                # (state/agent-sessions.json), not the stale canonical seat sid.
                live_sid_fn=lambda r: _resolve_live_blue_sid(orchestra_dir, r))
            _green_gen = (obs.get("green") or {}).get("generation")
            seams = seams_factory(root, blue)
            # B3: thread the lossless-seam hooks into the per-seat arm. green_wake_fn is the real
            # #3/#4 waker (adoption); green_ingested_seq_fn / blue_wal_event_count_fn pass through
            # (None => BgArm's seams / real-WAL-reader fallback).
            arm = arm_ctor(wal_dir, root, seams=seams,
                           cutover_active=cutover_active,
                           forced=(root in force),
                           green_wake_fn=green_wake_for(root),
                           green_ingested_seq_fn=green_ingested_seq_fn,
                           blue_wal_event_count_fn=blue_wal_event_count_fn,
                           green_quota_fn=_default_green_quota_fn(
                               wal_dir, root, obs.get("runtime")))
            arm.beat(obs)                          # inv 4 ArmRefused raises here
            # Vendor-quota HOLD (gm msg_7b3ffa11): page ONCE per episode (alarm key =
            # kind:reset_at written by BgArm._green_quota_hold), never every beat.
            _qst = BgStateStore(wal_dir, root)
            _qkey = _qst.read_meta("green_quota_alarm")
            if _qkey and _qst.read_meta("green_quota_alarm_paged") != _qkey:
                _crumb = _qst.read_meta("green_quota_limited") or {}
                alarm_fn(kind="green-quota", root=root, consecutive=1, page=True,
                         detail=(f"green {root}-g{_green_gen} quota-limited at seam "
                                 f"{_crumb.get('seam')}: kind={_crumb.get('kind')} "
                                 f"limit_id={_crumb.get('limit_id')} reset_at={_crumb.get('reset_at')} "
                                 f"({_crumb.get('detail')}) — HOLD, no prune/respawn; "
                                 f"credits do not self-recover"))
                _qst.write_meta("green_quota_alarm_paged", _qkey)
                summary["alarms"] += 1
                summary["green_quota_hold"] = summary.get("green_quota_hold", 0) + 1
            # Wake-failed page (gm msg_86bcc168 item 1(b)): once per green episode.
            _page_green_wake_failed(_qst, alarm_fn, root, _green_gen, summary)
            # OBSERVABILITY (gm-approved): emit the beat's OWN decision + the REAL ctx value it read
            # to logs/fleet-beat.log (like the fan-out cap) so the autonomous beat's ctx decisions
            # are visible in the canonical log, not only in bg_state history. The ctx carried is the
            # obs the beat itself built (build_obs -> read_ctx adapter) — provably not a forced obs.
            _state_after = read_state(root)
            _reason = ((BgStateStore(wal_dir, root).read().get("history") or [{}])[-1]
                       .get("reason"))
            _ctx = obs.get("ctx_pct")
            # gm queue (c): carry the decide_bg REASON token too (pure re-evaluation of the
            # same obs), so a READY seat held by an L3 guard (suppress:composer/attached) or
            # one that qualified (card:wait-swap) is legible from the canonical log.
            try:
                _decide = decide_bg(obs).get("reason")
            except Exception:  # noqa: BLE001 — observability must never break the beat
                _decide = None
            log_fn(f"decision root={root} ctx={_ctx if _ctx is None else round(_ctx, 4)} "
                   f"ctx_source={obs.get('ctx_source')} {state_before}->{_state_after} "
                   f"reason={_reason} decide={_decide}")
            # B3 within-beat fan-out accounting: a seat that just went SOLO -> in-flight
            # (booted a green this beat) consumes a slot for the seats still to come this tick.
            if state_before == "SOLO" and _state_after in _INFLIGHT_STATES:
                inflight += 1
            # v2-1 COMPLETION PHASE (gm bar #5): a swap via the async effects-
            # incomplete seam leaves DEGRADED without reaping Blue. Drive the
            # completion pass (reap as a bounded effect -> DRAINED) INSIDE this
            # firewall so a completion throw alarms/disarms like any bg failure
            # (Claude-leg build note a). No-op unless the seat is resumable-DEGRADED.
            from . import bg_complete
            comp = bg_complete.complete_swap(
                root, wal_dir=wal_dir, seams=seams,
                blue_generation_id=obs.get("blue_generation_id"),
                # P0.3 reap-gate: only reap Blue once canonical has advanced to GREEN
                # (blue+1). orchestra_dir is the write-truth DB root for the re-read.
                orchestra_dir=orchestra_dir,
                expected_green_generation=(obs.get("green") or {}).get("generation"))
            if comp.get("reaped"):
                summary["reaped"] = summary.get("reaped", 0) + 1
            # M3h: surface the silent-null (READY green with no .sid) LOUD — a durable
            # alarm caught BEFORE any arm, so a null-sid green never promotes unnoticed.
            _green_alias = f"{root}-g{(obs.get('green') or {}).get('generation')}"
            if detect_green_sid_silent_null(wal_dir, root, _green_alias):
                summary["alarms"] += 1
                summary["green_sid_missing"] = summary.get("green_sid_missing", 0) + 1
            _reset_failures(wal_dir, root)         # success clears the orphan count
            summary["acted"] += 1
        except Exception as exc:  # noqa: BLE001 -- the firewall (hard requirement)
            n = _bump_failures(wal_dir, root)
            summary["failed"] += 1
            page = n >= orphan_after               # inv 7 escalation threshold
            alarm_fn(kind="orphan-disarm" if page else "bg-failure",
                     root=root, detail=repr(exc), consecutive=n, page=page)
            summary["alarms"] += 1
            # (d) PRUNE-ON-FAIL: if this beat had registered (or would have) a provisional
            # green, remove the leak (DB row + pane + reset SOLO) so a fail-closed beat never
            # strands a live provisional (the M1 delta). prune_provisional is guarded
            # (idempotent, refuses canonical/retired) so an as-yet-unregistered gen is a safe
            # no-op. Inside the firewall — a cleanup hiccup can never re-break the beat.
            if _green_gen is not None:
                prune_failed_green(orchestra_dir, wal_dir, root, _green_gen,
                                   f"{root}-g{_green_gen}")
                summary["pruned_failed"] = summary.get("pruned_failed", 0) + 1
            if page:
                # inv 7: never a silent indefinite orphan — disarm-to-legacy + page.
                disarm_fn(root)
                _reset_failures(wal_dir, root)
                summary["disarmed"] += 1
    return summary
