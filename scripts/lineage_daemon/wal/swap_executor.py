"""swap_executor — stage-3 cutover, REBASED on the unified identity store.

the operator's identity-store unification collapsed the 3 JSON stores into one ACID DB,
so the swap's IDENTITY step is now a single ``orchestra_db.execute_swap`` txn
(atomic Blue→Green, U8 stale-sid clear + U14 Green runtime_state bootstrap
inside it). This module is the THIN wrapper that carries the DEC-1788323461 folds
around that txn:

  * identity is atomic — a crash before COMMIT rolls back to fully-Blue, zero
    repair (delegated to the store; we only choose when to call it);
  * POST-COMMIT EFFECTS (tmux rename, grandchild-safe Blue reap, WAL markers) are
    idempotent and re-runnable; a crash mid-effects leaves the swap labeled
    ``effects-incomplete`` (never an identity-reconcile state — identity is
    coherent from COMMIT, U9);
  * #11 BOUNDED EFFECT TIMEOUT — a HUNG (signal-ignoring) effect must not
    deadlock the executor or hold anything forever. On timeout we return a
    DISTINCT ``swap-timeout`` outcome (needs_degraded=True) so the beat routes it
    to reap-COMPLETION + DEGRADED+page, NOT to Green-spawn (gm GREEN criterion:
    a half-swap ≠ ordinary DEGRADED);
  * all external seams are dependency-INJECTED so stage-3 tests use pure fakes and
    stage-4 swaps in real spawn/verify/hydrate/reap (DP-U4).

INERT: nothing here runs on a live beat unless bg_state wires it behind
``bg_enabled`` (a separate, flag-gated, default-absent path).
"""
import threading
from dataclasses import dataclass

from identity_store.orchestra_db import (
    get_connection, execute_swap, run_post_commit_effects)

DEFAULT_EFFECT_TIMEOUT_S = 120.0  # #11 wall-clock budget for the whole effects section


@dataclass
class SwapOutcome:
    status: str            # 'complete' | 'effects-incomplete' | 'swap-timeout'
    swap_id: int
    green_generation_id: int
    needs_degraded: bool = False


def _run_effects_bounded(effects, timeout_s):
    """Run effects in order under a wall-clock budget. Returns (ok, timed_out,
    error). A hung effect cannot be force-killed (Python threads aren't
    cancellable), so on timeout we DETACH the worker (daemon thread) and return —
    the executor never blocks past the budget. A raised effect => (False, False,
    exc); clean completion => (True, False, None)."""
    result = {"done": False, "error": None}

    def _worker():
        try:
            for fn in effects:
                fn()
            result["done"] = True
        except Exception as exc:  # noqa: BLE001 -- surfaced as effects-incomplete
            result["error"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return False, True, None            # timed out (thread left detached)
    if result["error"] is not None:
        return False, False, result["error"]
    return True, False, None


class SwapExecutor:
    def __init__(self, db_path, effect_timeout_s=DEFAULT_EFFECT_TIMEOUT_S):
        self._db_path = db_path
        self._effect_timeout_s = effect_timeout_s

    def swap(self, root, green, blue_generation_id=None, effects=None,
             _fail_swap_after=None):
        """Atomic identity swap + bounded idempotent effects.

        Identity: execute_swap (one BEGIN IMMEDIATE txn; crash before COMMIT →
        rollback to Blue). Effects: bounded-timeout runner. The swaps row starts
        'effects-incomplete' and is marked 'complete' only when effects finish
        within budget.
        """
        conn = get_connection(self._db_path)
        try:
            res = execute_swap(conn, root, green,
                               blue_generation_id=blue_generation_id,
                               _fail_after=_fail_swap_after)
            swap_id = res["swap_id"]
            green_id = res["green_generation_id"]
            return self._drive_effects(conn, swap_id, green_id, effects or [])
        finally:
            conn.close()

    def resume_effects(self, swap_id, effects):
        """C2/#12 resume driver: re-run the idempotent effects for a swap left
        'effects-incomplete' (a crashed — NOT hung — executor). Identity is
        already coherent; this only replays effects and re-labels on success."""
        conn = get_connection(self._db_path)
        try:
            row = conn.execute(
                "SELECT green_generation_id FROM swaps WHERE id=?",
                (swap_id,)).fetchone()
            green_id = row["green_generation_id"] if row else None
            return self._drive_effects(conn, swap_id, green_id, effects)
        finally:
            conn.close()

    def _drive_effects(self, conn, swap_id, green_id, effects):
        ok, timed_out, error = _run_effects_bounded(
            effects, self._effect_timeout_s)
        if ok:
            run_post_commit_effects(conn, swap_id, [])  # marks status='complete'
            return SwapOutcome("complete", swap_id, green_id)
        if timed_out:
            # #11: distinct half-swap label — identity coherent, effects hung.
            # The swaps row stays 'effects-incomplete'; the DISTINCT signal to the
            # beat is the outcome status 'swap-timeout' + needs_degraded, routing
            # to reap-COMPLETION, never Green-spawn.
            return SwapOutcome("swap-timeout", swap_id, green_id,
                               needs_degraded=True)
        # a raised effect (crash, not hang): resumable by resume_effects.
        return SwapOutcome("effects-incomplete", swap_id, green_id)
