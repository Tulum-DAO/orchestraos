"""menu_bridge — LIVE-WIRING entry that ties the pure decision-menu core
(menu_bridge_core) to the durable ApprovalStore ledger.

This is the ONLY layer here that touches sqlite. It keeps all tmux/detector
I/O out of the testable logic: bridge_one / reconcile_orphans / run_cycle take
already-scraped fleet state (plain dicts) and a store, so tests inject temp DBs
and fake fleet state with NO tmux, NO notify, NO network.

Blast-radius note for operators: ApprovalStore.create() (approval_schema.py) is
a pure sqlite INSERT and does NOT fire notify(). Push notification lives in
approval.py's submit() wrapper (which menu_bridge does NOT call). Bridging a
menu therefore writes a durable pending row that the approvals surface /
notify-backstop cron will later push — but bridge_one itself never dials out.

LIVE ARMING: main() defaults to a DRY scan that prints what it WOULD bridge and
creates NOTHING. Real ledger writes require the explicit --arm flag, which
prints a loud banner first. collect_live() is the only function that touches
real tmux + get_agent_status; it is never called from tests.

INPUT_KIND CLASSIFIER: option input_kind stamping is delegated to the SINGLE
live classifier, watch_gateway.stamp_input_kinds — never duplicated here. The
pure core (build_menu_payload) intentionally does NOT stamp; bridge_one does a
lazy `from watch_gateway import stamp_input_kinds` (kept inside the function so
this module imports light + stays cron-safe) and stamps the payload before the
store.create() write. This project's tests are the contract suite for that
single classifier.
"""

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import menu_bridge_core as core

DEBOUNCE_S = 5.0


# ---------------------------------------------------------------------------
# 1. bridge_one — create (or dedup to) a single pending menu row
# ---------------------------------------------------------------------------

def _default_walk_fn(source_session):
    """The real active capture walk (watch_gateway.menu_capture_walk) — presses
    Right/Left (navigation-only) on the live pane under the per-session lock to
    page a multi-part menu, then RESTORES to part 0. Lazy import (cron-safe)."""
    from watch_gateway import menu_capture_walk
    return menu_capture_walk(source_session)


def _noop_walk_fn(source_session):
    """PASSIVE cron walk (Finding-1 / spec §3.1-a): NEVER presses a key. The
    always-on pickup path must never actuate a live pane — active hydration
    (menu_capture_walk) exists ONLY on the client-triggered /agent-menu-capture,
    fired when the operator opens a card. Returning None makes bridge_one keep the
    passive part-1 payload (walk_complete false); the client hydrates the full
    parts[] on demand. This is the #1 binding safety condition ."""
    return None


def _on_original_part(fresh, original_question):
    """True iff the freshly-reread pane is STILL showing the same part-0 question
    the debounced capture had. The real AskUserQuestion tab bar has no absolute
    index (part_index is always 0 — verified live 2026-08-16), so the reliable
    "is the operator still on part 0?" signal is QUESTION IDENTITY: on a real menu the
    on-screen `question` changes when he navigates to another part. A vanished/
    unreadable menu (fresh None) is NOT on the original part -> defer."""
    if not isinstance(fresh, dict):
        return False
    return fresh.get("question") == original_question


def _live_reread(source_session):
    """FRESH live pane read for the §4 mid-navigation guard — re-parse the pane
    NOW (not the stale debounced capture) so the guard reflects where the operator's menu
    actually is at walk time. Returns the current pending_menu or None. Lazy
    import (cron-safe); tests inject reread_fn instead."""
    import importlib
    agent_status = importlib.import_module("agent-status")
    try:
        st = agent_status.get_agent_status(source_session)
    except Exception:  # noqa: BLE001 -- a failed reread defers (fail-safe)
        return None
    return st.get("pending_menu") if isinstance(st, dict) else None


def bridge_one(store, source_session, capture, first_seen_ts, now, state,
               debounce_s=DEBOUNCE_S, walk_fn=None, reread_fn=None,
               store_but_mark=False):
    """Bridge a single parked/idle, debounced pane-menu into the ledger.

    Returns the approval row id (new or the existing op_key-deduped row) when
    bridged, or None when the gate says not yet / not eligible. No side effects
    beyond the store.create() INSERT (which does NOT notify).

    MULTI-PART HYDRATION (P1 field regression 2026-08-16): a passive capture of a
    multi-part menu holds only part-1 (walk_complete false) — storing that leaves
    the bridged card with no full parts[], so the client fail-safes to the raw
    view (the old broken thing). When the gate passes AND the capture needs
    hydration, run the ACTIVE walk (walk_fn, default menu_capture_walk) to page
    the remaining parts and store the FULL parts[]. The walk is navigation-only
    (Right/Left) + restores to part 0, under the per-session lock, and only fires
    on a menu the gate already proved PARKED + debounced (>=debounce_s stable) —
    so it never fights a human mid-navigation. Fail-SAFE: any walk error/timeout
    stores the passive payload (walk_complete false) — honest partial, never a
    faked-complete, never a raise.
    """
    if not core.should_bridge(state, capture, first_seen_ts, now, debounce_s):
        return None
    payload = core.build_menu_payload(capture, source_session)
    if core.needs_hydration(capture):
        # §4 MID-NAVIGATION GUARD : immediately before the
        # walk, re-read the pane and SKIP hydration (defer to the next tick, store
        # the passive payload) unless the menu is on part 0. NEVER Right/Left a
        # menu the operator just started driving — structural, not probabilistic. A gone
        # menu (reread None) also defers. Fail-safe: a reread error defers too.
        rf = reread_fn or _live_reread          # default: a FRESH live pane read
        try:
            fresh = rf(source_session)
        except Exception:  # noqa: BLE001
            fresh = None
        # Guard signal = QUESTION IDENTITY (part_index is unreadable on a real
        # menu). Still on the captured part-0 question => safe to walk.
        on_part_zero = _on_original_part(fresh, capture.get("question"))
        if on_part_zero:
            wf = walk_fn or _default_walk_fn
            try:
                walked = wf(source_session)
            except Exception:  # noqa: BLE001 -- fail-SAFE: keep the passive payload
                walked = None
            if isinstance(walked, dict) and walked.get("walk_complete") \
                    and walked.get("parts"):
                payload["parts"] = walked["parts"]
                payload["part_count"] = walked.get("part_count", len(walked["parts"]))
                payload["walk_complete"] = True
        # else (not on part 0 / gone / error): leave the passive (walk_complete
        # false) payload — §1.1 skip (below) then keeps it off the approval page,
        # and the next tick retries when the operator is back on part 0.
    # §1.1(b) SKIP → STORE-BUT-MARK, behind a DEFAULT-OFF flag. An un-hydratable
    # multi-part card (hydration didn't complete: passive cron noop / mid-nav defer
    # / walk fail-timeout) holds only part-0.
    #   store_but_mark=False (DEFAULT, known-safe, the rollback target): SKIP it —
    #     never surface a broken single-part card; it stays answerable in the
    #     in-agent view (which hydrates on demand). This is §1.1(a) mitigation @16b309d44.
    #   store_but_mark=True (the durable §1.1(b) leg, flipped ON only after the
    #     client fail-safe is verified live — a the operator arm, NOT this code): STORE the
    #     card marked needs_hydration so the approval page/watch hydrate it via
    #     /agent-menu-capture (then the ledger write-back completes it), and a
    #     non-hydration-capable client gets the server-side read-only echo
    #     (handle_pending capability fail-safe) — never the raw broken card.
    if payload.get("multipart") and not payload.get("walk_complete"):
        if not store_but_mark:
            return None
        payload["needs_hydration"] = True
    # Delegate input_kind stamping to the SINGLE live classifier. Lazy import
    # keeps module import light + cron-safe (watch_gateway's heavy deps import
    # lazily inside its own handlers). stamp_input_kinds mutates + returns the
    # menu dict; we stamp in place before persisting.
    from watch_gateway import stamp_input_kinds
    stamp_input_kinds(payload)
    op_key = core.menu_op_key(source_session, capture["question"])
    rid = store.create(
        from_agent=source_session,
        question=capture["question"],
        worker_kind="pane",
        kind="menu",
        menu=payload,
        options=[o["label"] for o in payload["options"]],
        op_key=op_key,
        # PROVENANCE : only rows stamped 'menu_bridge' may
        # ever be auto-resolved by reconcile_orphans. A kind=menu row minted
        # anywhere else (e.g. approval.py's CLI --options auto-synthesize) has
        # no live pane behind it and must stay pending + visible. Passed via
        # store.create() directly — approval.py's --origin choices deliberately
        # reject this value so the CLI can never forge bridge provenance.
        origin="menu_bridge",
    )
    return rid


# ---------------------------------------------------------------------------
# 2. reconcile_orphans — flip vanished menus to resolved_elsewhere
# ---------------------------------------------------------------------------

def _db_path(store, db_path=None):
    if db_path is not None:
        return str(db_path)
    return str(getattr(store, "db_path"))


# Grace-period defaults (L1, card-pipeline runbook 1.1). A bridged menu whose
# op_key is absent from the scan must NOT resolve on the first missing tick —
# pane menus on busy seats vanish in under 60s (an inbound tmux inject dismisses
# the AskUserQuestion widget) while the question is still unanswered in the operator's
# life. Resolve only after the op_key has been absent for GRACE_TICKS consecutive
# scans AND at least GRACE_S wall-clock seconds — BOTH, so a burst of fast ticks
# cannot short-circuit the window. A reappearance resets the streak to zero.
MENU_RECONCILE_GRACE_TICKS = int(os.environ.get("MENU_RECONCILE_GRACE_TICKS", "3"))
MENU_RECONCILE_GRACE_S = float(os.environ.get("MENU_RECONCILE_GRACE_S", "180"))
# Scan-sanity (1.2). The grace period defends against a MENU vanishing; this
# defends against the SCAN ITSELF failing (deriver-first early-return, tmux
# hiccup, parse regression, partially-written file) — where every pending card
# looks absent at once and would be mass-resolved. If a single scan loses more
# than LOSS_FRAC of the previously-pending op_keys (with at least _SANITY_MIN of
# them), or the scan is empty against a non-empty pending set, refuse to
# reconcile this tick and DON'T consume the streak counters. Refusal is always
# fail-safe toward KEEPING the card: a stuck card is visible and dismissible; a
# vanished card is invisible and unrecoverable.
MENU_RECONCILE_SANITY_LOSS_FRAC = float(os.environ.get("MENU_RECONCILE_SANITY_LOSS_FRAC", "0.5"))
# Per-tick resolve cap . Belt for the
# 2026-09-04 single-tick mass-death class: if MORE than this many orphans would
# flip to resolved_elsewhere in ONE tick, refuse the whole tick (no resolves, no
# streak writes) and log it — a genuine fleet-wide "the operator answered everything at
# once" is rare; a scan/parse regression looks exactly like it.
MENU_RECONCILE_MAX_RESOLVES_PER_TICK = int(os.environ.get("MENU_RECONCILE_MAX_RESOLVES_PER_TICK", "8"))
_SANITY_MIN = 3
# DATA dir (orchestra.toml [data] dir, exported as ORCHESTRA_DIR by `orchestra up`); the
# checkout is the fallback for a bare run.
_DATA_DIR = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")
_RECONCILE_LOG = os.path.join(_DATA_DIR, "logs", "menu-reconcile.log")


def _reconcile_log_path():
    """Resolved at CALL time: MENU_RECONCILE_LOG_PATH overrides the production
    logs/menu-reconcile.log . That file is THE diagnostic seam
    for the stale-card class, so a test run must never stamp fake REFUSED lines
    into it — scripts/conftest.py points every pytest run at a tmp file. Unset
    (the cron) => prod path unchanged."""
    return os.environ.get("MENU_RECONCILE_LOG_PATH") or _RECONCILE_LOG


def _reconcile_log(msg):
    """Best-effort WARN line (file + stderr); never raises inside the cron."""
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    try:
        with open(_reconcile_log_path(), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr)


def _ensure_absence_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS menu_bridge_absence ("
              "op_key TEXT PRIMARY KEY, absent_ticks INTEGER NOT NULL, "
              "first_absent_at REAL NOT NULL)")


def _scan_is_sane(previously_bridged, currently_present):
    """(sane, reason). Fail-safe toward KEEPING cards. An empty scan against a
    non-empty pending set is always untrustworthy; a single-tick loss of more
    than LOSS_FRAC of a non-trivial pending set (>= _SANITY_MIN) is the mass-
    resolve signature. A legit single-seat finish (1-of-many) stays sane."""
    prev = set(previously_bridged)
    if not prev:
        return True, None
    if not currently_present:
        return False, "empty_scan"
    lost = prev - set(currently_present)
    if len(prev) >= _SANITY_MIN and (len(lost) / len(prev)) > MENU_RECONCILE_SANITY_LOSS_FRAC:
        return False, f"mass_loss:{len(lost)}/{len(prev)}"
    return True, None


def _orphan_owner(op_key):
    """'menu:{session}:{hash}' -> session (None for a malformed key)."""
    parts = op_key.split(":", 2)
    return parts[1] if len(parts) == 3 and parts[0] == "menu" else None


def reconcile_orphans(store, previously_bridged, currently_present, db_path=None,
                      now=None, grace_ticks=None, grace_s=None,
                      scanned_sessions=None, errored_sessions=None, max_resolves=None):
    """For each op_key bridged last cycle but absent now, mark its still-pending
    menu row 'resolved_elsewhere' — but ONLY after a grace window (1.1) and ONLY
    when the scan is trustworthy (1.2). Guarded UPDATE (status='pending' only) so
    an already-answered/terminal row is never clobbered and non-menu rows are
    left alone. Returns the list of op_keys actually transitioned.

    PROVENANCE GATE : only rows this bridge itself
    created (origin='menu_bridge' AND the genuine-bridge op_key prefix
    'menu:{session}:' — belt-and-suspenders, both required) are eligible.
    A kind=menu row minted by any other writer (the CLI --options
    auto-synthesize path, <card-id>'s killer) has no live pane behind it,
    so it would otherwise be a permanent "orphan" and get invisibly
    auto-resolved every tick. Such rows now stay pending + visible forever.

    SCAN EVIDENCE (L1b, 2026-09-07 approval-card-fixer). The 1.2 count guard
    (`present` empty => empty_scan; >50% lost => mass_loss) cannot tell "the
    scan broke" from "the operator answered every menu in the terminal" — with 1-4
    pending menus those are the SAME numbers — so from 2026-09-04 it refused
    every tick and no in-agent menu card ever left the operator's phone. When the caller
    supplies `scanned_sessions` (panes actually read this tick) and
    `errored_sessions` (panes whose read raised), the count guard is replaced by
    per-orphan evidence: an orphan accrues absence only if its owning pane was
    read and showed no menu, or the session is not in tmux at all; an orphan
    whose owner read errored is left untouched (no streak, no reset). An empty
    fleet scan (nothing read at all) still refuses wholesale. Callers that pass
    neither keep the legacy count guard unchanged.

    Grace state is persisted in a self-contained menu_bridge_absence table
    (op_key -> absent_ticks, first_absent_at) so a STATELESS */N cron carries the
    absence streak across ticks. Self-contained: opens the SAME DB the store uses
    (store.db_path, or an explicit db_path override for tests). Does not touch
    approval_schema.py.
    """
    now = time.time() if now is None else now
    grace_ticks = MENU_RECONCILE_GRACE_TICKS if grace_ticks is None else grace_ticks
    grace_s = MENU_RECONCILE_GRACE_S if grace_s is None else grace_s
    max_resolves = MENU_RECONCILE_MAX_RESOLVES_PER_TICK if max_resolves is None else max_resolves
    prev = set(previously_bridged)
    present = set(currently_present)
    path = _db_path(store, db_path)
    transitioned = []
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA busy_timeout=30000")
        _ensure_absence_table(c)
        # 1.2 — refuse an untrustworthy scan BEFORE resolving or touching streaks.
        evidence = scanned_sessions is not None
        errored = set(errored_sessions or ())
        if evidence:
            sane, reason = (bool(scanned_sessions), None if scanned_sessions else "no_fleet_scan")
        else:
            sane, reason = _scan_is_sane(prev, present)
        if not sane:
            _reconcile_log(f"WARN menu-reconcile REFUSED ({reason}): "
                           f"pending={len(prev)} present={len(present)} — kept all cards")
            c.commit()
            return []
        # Bound the streak table: drop rows for op_keys no longer relevant
        # (answered/terminal — gone from the pending set and not on screen).
        relevant = prev | present
        stale = [r[0] for r in c.execute("SELECT op_key FROM menu_bridge_absence").fetchall()
                 if r[0] not in relevant]
        for op_key in stale:
            c.execute("DELETE FROM menu_bridge_absence WHERE op_key=?", [op_key])
        # A reappearance resets the streak.
        for op_key in present:
            c.execute("DELETE FROM menu_bridge_absence WHERE op_key=?", [op_key])
        # 1.1 — grace: an orphan resolves only after GRACE_TICKS consecutive
        # absences AND GRACE_S elapsed since the first absence.
        # Pass 1 (read-only): decide each orphan's fate this tick.
        plan = []   # (op_key, ticks, first_absent, ripe)
        for op_key in core.orphans(prev, present):
            # L1b — with evidence, an orphan whose owner pane could not be read
            # this tick is HELD untouched: absence is only evidence when we
            # actually looked. (Owner not in tmux at all = cannot show a menu.)
            if evidence and _orphan_owner(op_key) in errored:
                continue
            row = c.execute("SELECT absent_ticks, first_absent_at FROM "
                            "menu_bridge_absence WHERE op_key=?", [op_key]).fetchone()
            # This absence is streak #ticks; first_absent anchors the wall clock.
            if row is None:
                ticks, first_absent = 1, now
            else:
                ticks, first_absent = int(row[0]) + 1, float(row[1])
            ripe = ticks >= grace_ticks and (now - first_absent) >= grace_s
            plan.append((op_key, ticks, first_absent, ripe))
        # Per-tick resolve cap: too many ripe at once = mass-death signature.
        n_ripe = sum(1 for _, _, _, r in plan if r)
        if n_ripe > max_resolves:
            _reconcile_log(f"WARN menu-reconcile REFUSED (resolve_cap:{n_ripe}>{max_resolves}): "
                           f"pending={len(prev)} present={len(present)} — kept all cards")
            c.rollback()
            return []
        # Pass 2 (write): resolve the ripe, persist streaks for the rest.
        for op_key, ticks, first_absent, ripe in plan:
            if ripe:
                res = c.execute(
                    "UPDATE approval_requests SET status='resolved_elsewhere' "
                    "WHERE op_key=? AND kind='menu' AND status='pending' "
                    "AND origin='menu_bridge' AND op_key LIKE 'menu:%'",
                    [op_key],
                )
                if res.rowcount > 0:
                    transitioned.append(op_key)
                c.execute("DELETE FROM menu_bridge_absence WHERE op_key=?", [op_key])
            else:
                # Persist the streak; keep the original first_absent_at on update.
                c.execute("INSERT INTO menu_bridge_absence(op_key, absent_ticks, "
                          "first_absent_at) VALUES(?,?,?) ON CONFLICT(op_key) DO "
                          "UPDATE SET absent_ticks=excluded.absent_ticks",
                          [op_key, ticks, first_absent])
        c.commit()
    finally:
        c.close()
    return transitioned


# ---------------------------------------------------------------------------
# 2b. ledger_pending_op_keys — the stateless cron's source of truth
# ---------------------------------------------------------------------------

def ledger_pending_op_keys(store, db_path=None):
    """Return the set of op_key for every still-pending bridged menu row.

    Reads the SAME DB the store uses (store.db_path, or an explicit db_path
    override for tests): rows WHERE kind='menu' AND status='pending' AND
    op_key IS NOT NULL. This is the durable source of truth a stateless */N
    cron reads to know what it bridged on a prior tick — it has no in-memory
    'bridged' set to consult across process restarts.

    PROVENANCE GATE : scoped to origin='menu_bridge' + the
    genuine 'menu:' op_key prefix, so a CLI-minted kind=menu card can never
    enter the reconciler's orphan candidate set (see reconcile_orphans).
    """
    path = _db_path(store, db_path)
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA busy_timeout=30000")
        rows = c.execute(
            "SELECT op_key FROM approval_requests "
            "WHERE kind='menu' AND status='pending' AND op_key IS NOT NULL "
            "AND origin='menu_bridge' AND op_key LIKE 'menu:%'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        c.close()


def is_session_carded(store, session, question, db_path=None) -> bool:
    """True iff a pending menu card ALREADY exists for this exact (session,
    question) — the perm-card lane seam  signature, not session-only). v2's pulse G1 hook calls
    this BEFORE its gm-escalate: carded -> card is primary, DON'T escalate;
    absent -> escalate gm as the fallback. Exact op_key match (menu_op_key =
    SHA-256 of session+question) so sequential prompts in one session don't
    false-suppress each other's escalation."""
    op_key = core.menu_op_key(session, question)
    return op_key in ledger_pending_op_keys(store, db_path=db_path)


# ---------------------------------------------------------------------------
# 2b'. backfill_bridge_origin — one-shot C2 migration 
# ---------------------------------------------------------------------------

def backfill_bridge_origin(store, db_path=None):
    """One-shot backfill: stamp origin='menu_bridge' onto LEGACY genuine bridge
    rows (kind='menu', status='pending', origin IS NULL) whose op_key carries
    the genuine-bridge prefix 'menu:{session}:' — the same discriminator
    resolve_pending_menus_for_session / hydrate_menu / cached_hydration already
    key on. Without this, every pre-fix genuine orphan becomes un-reconcilable
    forever the instant the provenance gate lands (C2, mandatory).

    CLI mis-mints (origin NULL with a NULL or non-'menu:' op_key) are correctly
    NOT stamped — they stay pending + visible. Terminal rows and non-menu rows
    are never touched. Idempotent (already-stamped rows don't match). Returns
    the number of rows stamped.
    """
    path = _db_path(store, db_path)
    c = sqlite3.connect(path, timeout=30)
    try:
        c.execute("PRAGMA busy_timeout=30000")
        res = c.execute(
            "UPDATE approval_requests SET origin='menu_bridge' "
            "WHERE kind='menu' AND status='pending' AND origin IS NULL "
            "AND op_key LIKE 'menu:%'"
        )
        c.commit()
        return res.rowcount
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 2c. run_cron_cycle — stateless, ledger-backed cycle for a */N cron
# ---------------------------------------------------------------------------

def run_cron_cycle(store, sessions_status, now, debounce_s=DEBOUNCE_S,
                   walk_fn=None, skip=None, store_but_mark=False):
    """One reconciliation cycle for a STATELESS cron (fresh process each tick).

    PASSIVE-ONLY (Finding-1): walk_fn defaults to _noop_walk_fn, so the cron path
    can NEVER reach menu_capture_walk — no background key-press on a live pane.
    Active hydration stays client-triggered (/agent-menu-capture). `skip(session)`
    (gm-owned live-test/scratch list) excludes flagged sessions from surfacing.

    A standalone */N cron has no memory across ticks, so it cannot track a
    cross-tick debounce window nor an in-memory 'bridged' set. The LEDGER is the
    source of truth on both counts:

    - Orphan reconcile: previously_bridged is read from the ledger
      (ledger_pending_op_keys) BEFORE this cycle's writes, so a menu bridged on a
      prior tick and now gone from the scan is reconciled to resolved_elsewhere —
      the whole point of ledger-backed reconcile.
    - Bridging: cross-tick debounce cannot live in memory, so the cron gates
      purely on state in {idle, waiting_permission} + pending_menu present. A
      first_seen anchor is seeded to `now` locally; a menu already pending in the
      ledger re-bridges as an idempotent op_key-deduped no-op, and a newly-seen
      one bridges once the state gate passes. (Documented trade-off: no
      per-menu settle delay across ticks; the tick interval itself is the
      effective settle window.)

    Returns {"bridged": [...op_keys...], "resolved": [...op_keys...]}.
    """
    walk_fn = walk_fn if walk_fn is not None else _noop_walk_fn
    # Source of truth for what we bridged on prior ticks — read BEFORE writing.
    previously_bridged = ledger_pending_op_keys(store)

    # Local, process-scoped first-seen: an op_key already pending in the ledger
    # is treated as past-debounce (seed to 0.0 so the gate's elapsed check
    # passes); a newly-seen one is seeded to `now` and, because the cron gate is
    # state-only (below), bridges this tick too.
    first_seen = {}
    currently_present = set()
    bridged = []

    scanned, errored = _scan_evidence(sessions_status, skip)

    for session, status in sessions_status.items():
        if session not in scanned:
            continue                                  # skipped or read-errored
        state = status.get("state")
        capture = status.get("pending_menu")
        if capture is None:
            continue
        op_key = core.menu_op_key(session, capture["question"])
        currently_present.add(op_key)
        # Single gate: bridge_one -> should_bridge decides on pending_menu
        # presence (the authoritative signal; a real parked menu is state=
        # 'working'). No cross-tick memory in a cron, so debounce_s=0.0 makes
        # the tick interval the settle window; op_key dedup keeps it idempotent.
        seed = 0.0 if op_key in previously_bridged else now
        first_seen.setdefault(op_key, seed)
        rid = bridge_one(store, session, capture, first_seen[op_key], now, state,
                         debounce_s=0.0, walk_fn=walk_fn,
                         store_but_mark=store_but_mark)
        if rid is not None:
            bridged.append(op_key)

    resolved = reconcile_orphans(store, previously_bridged, currently_present, now=now,
                                 scanned_sessions=scanned, errored_sessions=errored)
    return {"bridged": bridged, "resolved": resolved}


def _scan_evidence(sessions_status, skip=None):
    """Split a fleet scan into (scanned, errored) session sets for the L1b
    reconcile evidence. A session is `errored` when collect_live flagged its
    read (scan_error=True) OR the gm skip-list excludes it — either way we did
    NOT look at that pane this tick, so its orphans are held, not resolved."""
    scanned, errored = set(), set()
    for session, status in sessions_status.items():
        if (skip is not None and skip(session)) or (status or {}).get("scan_error"):
            errored.add(session)
        else:
            scanned.add(session)
    return scanned, errored


# ---------------------------------------------------------------------------
# 2d. run_pickup_cron — the always-on PASSIVE surface-pickup entry (S3 item 3)
# ---------------------------------------------------------------------------
# Spec §2.1/§2.2 + §3 safety model. The single always-on process that guarantees
# every system-raised native menu becomes a durable surface row — READ-ONLY:
# it detects + writes ledger rows and NEVER presses a key (bridge_one gets the
# no-op walk_fn). Ships DISABLED (surface-pickup-config.json enabled:false);
# rollout is disabled -> --would-surface shadow -> gm flips enabled. A kill-switch
# sentinel (~/runtime/SURFACE_PICKUP_DISABLED) hard-stops it instantly, and a
# Operator-owned skip-list keeps live-test/scratch sessions off the real surface.

SURFACE_PICKUP_CONFIG = os.path.join(_DATA_DIR, "state", "surface-pickup-config.json")
SURFACE_PICKUP_KILLSWITCH = os.environ.get("SURFACE_PICKUP_KILLSWITCH") or os.path.join(_DATA_DIR, "state", "SURFACE_PICKUP_DISABLED")


def _load_pickup_cfg(path=None):
    """gm-owned config: {enabled, skip_sessions[], skip_prefixes[]}. Fail-safe to
    DISABLED + empty on missing/broken (never surfaces without an explicit go)."""
    path = path or SURFACE_PICKUP_CONFIG
    try:
        with open(path) as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("enabled", False)
            d.setdefault("skip_sessions", [])
            d.setdefault("skip_prefixes", [])
            d.setdefault("store_but_mark", False)      # §1.1(b): DEFAULT OFF (=skip)
            return d
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"enabled": False, "skip_sessions": [], "skip_prefixes": [],
            "store_but_mark": False}


def _pickup_skip(cfg):
    sessions = set(cfg.get("skip_sessions") or [])
    prefixes = tuple(p for p in (cfg.get("skip_prefixes") or []) if p)
    def skip(session):
        return session in sessions or (bool(prefixes) and session.startswith(prefixes))
    return skip


def _would_surface(fleet, skip=None):
    """Read-only: op_keys the pickup WOULD bridge this tick, writing NOTHING.
    Same gate the armed cron uses (pending_menu present + should_bridge)."""
    out = []
    for session, status in fleet.items():
        if skip is not None and skip(session):
            continue
        cap = status.get("pending_menu")
        if cap is None:
            continue
        if core.should_bridge(status.get("state"), cap, 0.0, 0.0, debounce_s=0.0):
            out.append({"session": session,
                        "op_key": core.menu_op_key(session, cap["question"]),
                        "question": cap["question"]})
    return out


def run_pickup_cron(store, fleet, now, cfg=None, killswitch_path=None, shadow=False):
    """One PASSIVE surface-pickup tick. Kill-switch first, then (shadow OR
    disabled) => compute would-surface + write NOTHING, else armed run_cron_cycle
    with the no-op walk_fn + gm skip-list. store/fleet/cfg/killswitch injectable
    for tests. Returns a dict describing what it did (never raises on config)."""
    killswitch_path = killswitch_path or SURFACE_PICKUP_KILLSWITCH
    if os.path.exists(killswitch_path):
        return {"disabled": "killswitch", "armed": False, "would_surface": []}
    cfg = cfg if cfg is not None else _load_pickup_cfg()
    skip = _pickup_skip(cfg)
    if shadow or not cfg.get("enabled"):
        return {"armed": False,
                "mode": "shadow" if shadow else "disabled",
                "would_surface": _would_surface(fleet, skip=skip)}
    # §1.1(b) store-but-mark: gm-owned config flag, DEFAULT OFF (=skip, known-safe).
    # When ON, an un-hydratable multi-part card is STORED (needs_hydration) instead
    # of skipped; the capability fail-safe (handle_pending) still protects old
    # clients. The flip is a the operator arm coordinated by all-model-parity + gm — not here.
    result = run_cron_cycle(store, fleet, now=now, walk_fn=_noop_walk_fn, skip=skip,
                            store_but_mark=bool(cfg.get("store_but_mark")))
    result["armed"] = True
    return result


# ---------------------------------------------------------------------------
# 3. run_cycle — pure-ish orchestrator over injected fleet state
# ---------------------------------------------------------------------------

def new_seen_state():
    """Fresh mutable state carried across run_cycle calls.

    first_seen: {op_key -> first-sighting timestamp} (debounce anchor)
    bridged:    {op_key} set of menus bridged in the prior cycle
    """
    return {"first_seen": {}, "bridged": set()}


def run_cycle(store, sessions_status, seen_state, now, debounce_s=DEBOUNCE_S):
    """One reconciliation cycle over injected fleet state.

    sessions_status: {session -> {"state": str, "pending_menu": capture|None}}
    seen_state:      mutable dict from new_seen_state(); mutated in place.

    Tracks first-seen timestamps, bridges eligible menus, computes the op_keys
    present this cycle, reconciles orphans (previously bridged, now gone), and
    returns {"bridged": [...op_keys...], "resolved": [...op_keys...]}.

    No tmux/detector here — the caller supplies fleet state.
    """
    first_seen = seen_state.setdefault("first_seen", {})
    prev_bridged = set(seen_state.setdefault("bridged", set()))

    currently_present = set()
    bridged = []

    for session, status in sessions_status.items():
        state = status.get("state")
        capture = status.get("pending_menu")
        if capture is None:
            continue
        op_key = core.menu_op_key(session, capture["question"])
        currently_present.add(op_key)
        # first-seen anchor: record on first sighting, keep it stable afterward.
        if op_key not in first_seen:
            first_seen[op_key] = now
        rid = bridge_one(store, session, capture, first_seen[op_key], now, state,
                         debounce_s)
        if rid is not None:
            bridged.append(op_key)

    # Reconcile menus that were bridged before but are gone now.
    scanned, errored = _scan_evidence(sessions_status)
    resolved = reconcile_orphans(store, prev_bridged, currently_present, now=now,
                                 scanned_sessions=scanned, errored_sessions=errored)

    # Drop first-seen anchors for menus no longer present so a reappearing menu
    # gets a fresh debounce window.
    for op_key in list(first_seen.keys()):
        if op_key not in currently_present:
            del first_seen[op_key]

    # Carry the union of previously- and newly-bridged op_keys that are still
    # present, so a menu bridged this cycle is reconciled if it vanishes next.
    seen_state["bridged"] = (prev_bridged | set(bridged)) & currently_present

    return {"bridged": bridged, "resolved": resolved}


# ---------------------------------------------------------------------------
# 4. collect_live + main — the ONLY live tmux/detector seam (guarded)
# ---------------------------------------------------------------------------

def registry_scoped_sessions(sessions, registry_path=None):
    """Keep only tmux sessions that are a registered seat (key or tmux_session in
    <data>/registry.json). No registry / unreadable => [] (never scan the whole host)."""
    path = registry_path or os.path.join(_DATA_DIR, "registry.json")
    try:
        with open(path) as f:
            agents = (json.load(f) or {}).get("agents") or {}
    except (OSError, ValueError):
        return []
    known = set(agents.keys()) | {str(v.get("tmux_session")) for v in agents.values() if isinstance(v, dict) and v.get("tmux_session")}
    return [s for s in sessions if s in known]


def collect_live():
    """Scan the real fleet: {session -> {state, pending_menu}} via tmux +
    get_agent_status. This is the ONE function that touches live I/O; it is
    never invoked by tests (they monkeypatch it). Imports are local so importing
    this module never pulls the detector/tmux stack.
    """
    import subprocess

    import importlib
    agent_status = importlib.import_module("agent-status")

    out = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True, timeout=10,
    )
    sessions = [s for s in out.stdout.splitlines() if s.strip()]
    # Registry-scoped: tmux is host-global, so only sessions that belong to a registered seat
    # are scanned (a second install or an unrelated tmux session never gets a card here).
    sessions = registry_scoped_sessions(sessions)

    fleet = {}
    for sess in sessions:
        try:
            st = agent_status.get_agent_status(sess)
        except Exception:  # noqa: BLE001 — a single bad pane never aborts the scan
            # L1b: keep the session in the scan as ERRORED so the reconciler
            # holds its orphans (we did not look) instead of treating the
            # silence as absence.
            fleet[sess] = {"state": None, "pending_menu": None, "scan_error": True}
            continue
        fleet[sess] = {
            "state": st.get("state"),
            "pending_menu": st.get("pending_menu"),
        }
    return fleet


_LIVE_BANNER = (
    "==================================================================\n"
    " LIVE: will create real ledger rows + the notify backstop will\n"
    "       push these decisions to the operator's phone. (--arm active)\n"
    "=================================================================="
)


def main(argv=None, store=None, seen_state=None, now=None):
    """CLI entry. Default (no --arm) is a DRY scan: prints what it WOULD bridge
    and creates NOTHING. --arm prints a loud banner then performs real ledger
    writes via run_cycle. store/seen_state/now are injectable for tests.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    armed = "--arm" in argv
    cron = "--cron" in argv
    would_surface = "--would-surface" in argv
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    if "--now" in argv:
        now = float(argv[argv.index("--now") + 1])
    if now is None:
        now = time.time()

    # C2 one-shot migration : stamp origin='menu_bridge' onto
    # legacy genuine bridge rows so they stay reconcilable behind the new
    # provenance gate. Standalone, explicit, idempotent; touches NOTHING else.
    # Run ONCE at deploy: python3 menu_bridge.py --backfill-origin
    if "--backfill-origin" in argv:
        from approval_schema import ApprovalStore
        store = store or ApprovalStore()
        store.migrate()
        n = backfill_bridge_origin(store)
        print(f"[backfill-origin] stamped {n} legacy menu_bridge row(s)")
        return 0

    fleet = collect_live()

    # S3 item 3: the always-on PASSIVE surface-pickup entry (crontab */1m). Runs
    # run_pickup_cron: kill-switch + gm skip-list + config-gated arming. Default
    # crontab uses --would-surface (shadow, zero writes) until gm flips
    # surface-pickup-config.json enabled:true, then swaps the entry to --cron.
    if cron or would_surface:
        if only is not None:
            fleet = {s: v for s, v in fleet.items() if s == only}
        store = None
        if cron:
            from approval_schema import ApprovalStore
            store = ApprovalStore(); store.migrate()
        res = run_pickup_cron(store, fleet, now=now, shadow=would_surface)
        print(f"[surface-pickup] {json.dumps(res)}")
        return 0

    # Canary scope: --only <session> restricts the cycle to ONE agent's menu, so
    # the first real-ledger arm can be proven on a single throwaway session and
    # can never touch any other agent's menu. Applies to both dry + armed paths.
    if only is not None:
        fleet = {s: v for s, v in fleet.items() if s == only}
        print(f"[scope] --only {only}: {len(fleet)} matching session(s)")

    if not armed:
        print("DRY scan (no --arm): the following menus WOULD be bridged.")
        would = []
        for session, status in fleet.items():
            cap = status.get("pending_menu")
            if cap is None:
                continue
            # Same gate the cron uses: pending_menu presence is authoritative
            # (a real parked menu is state='working'); only a live composer
            # (stranded_input) is excluded.
            if core.should_bridge(status.get("state"), cap, 0.0, 0.0, debounce_s=0.0):
                would.append(core.menu_op_key(session, cap["question"]))
                print(f"  WOULD bridge: {session} :: {cap['question']!r}")
        if not would:
            print("  (nothing eligible)")
        print("Created NOTHING. Re-run with --arm to write the ledger.")
        return 0

    print(_LIVE_BANNER)
    if store is None:
        from approval_schema import ApprovalStore
        store = ApprovalStore()
        store.migrate()
    # --arm runs the STATELESS, ledger-backed cron cycle (seen_state is unused:
    # a */N cron has no cross-tick memory; the ledger is the source of truth).
    result = run_cron_cycle(store, fleet, now=now)
    print(f"LIVE result: bridged={result['bridged']} resolved={result['resolved']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
