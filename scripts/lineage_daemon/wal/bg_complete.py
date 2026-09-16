"""bg_complete — the async arm's completion phase so REAP ACTUALLY FIRES (v2-1,
gm bar #5). Without this, reap is dead code.

Why it's needed (verified by effect): real_seams.make_swap_fn ALWAYS returns
status='effects-incomplete' (identity committed synchronously; effects deferred,
sync_effects_owner=False). So bg_arm._do_swap never reaches the status=='complete'
branch — it writes DEGRADED(effects-incomplete) and STOPS. bg_supervise_fleet only
calls arm.beat, never a completion pass. Blue is therefore never reaped after Green
takes the identity (double-pane hazard = bar #5 headless/orphan failing silently).

This module IS "bg_arm's own effect layer": for a seat left in DEGRADED with a
resumable reason, it drives the reap as a BOUNDED effect (the same
_run_effects_bounded runner SwapExecutor uses — so v2-2 time-bounding comes free: a
hanging reap converts to a raise, never wedging the beat), then writes DRAINED.

Contract:
  * runs INSIDE the beat's per-seat firewall (a completion throw -> alarm/disarm,
    Claude-leg build note a) — bg_supervise_fleet calls it right after arm.beat;
  * IDEMPOTENT: a re-run on an already-DRAINED seat is a no-op (reap not re-fired);
  * only acts on DEGRADED(effects-incomplete | swap-timeout) — a #11 swap-timeout
    half-swap routes to reap-COMPLETION too (identity coherent, effects hung), never
    a cold Green-spawn.
"""
from .bg_state import BgStateStore
from .swap_executor import _run_effects_bounded


class CompletionTimeout(Exception):
    """The bounded reap effect exceeded its wall-clock budget (v2-2). Surfaced as a
    raise so the beat firewall alarms + disarms — never a silent wedge."""


# DEGRADED reasons that the completion phase can resolve by reaping Blue.
_RESUMABLE_REASONS = ("effects-incomplete", "swap-timeout")


def _last_reason(state_doc):
    hist = state_doc.get("history") or []
    return hist[-1].get("reason") if hist else None


def _read_canonical_generation(orchestra_dir, root):
    """P0.3 reap-gate input: the CURRENT canonical generation NUMBER for ``root`` from
    the write-truth orchestra-registry.db, or None if absent/unreadable. Fail-closed by
    caller: a None (no canonical row) is treated as 'not green' -> gate the reap."""
    import os
    if not orchestra_dir:
        return None
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    if not os.path.exists(db):
        return None
    try:
        from identity_store.orchestra_db import get_connection
        conn = get_connection(db)
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(
            "SELECT g.generation FROM canonical c JOIN generations g "
            "ON g.id = c.generation_id WHERE c.root = ?", (root,)).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def complete_swap(root, *, wal_dir, seams, blue_generation_id,
                  timeout_s=120.0, orchestra_dir=None,
                  expected_green_generation=None, canonical_reader=None,
                  tmux_ops=None):
    """Complete a DEGRADED(effects-incomplete|swap-timeout) swap by running the reap
    as a bounded effect, then writing DRAINED. No-op unless the seat is in a
    resumable DEGRADED state. Returns {reaped, state, reason}.

    P0.3 REAP-GATE (opt-in via ``expected_green_generation``): before reaping Blue,
    re-read canonical's CURRENT generation and require it == GREEN (blue.generation+1).
    If canonical did NOT advance to green (swap identity did not land, or canonical is
    absent), DO NOT reap — reaping Blue while canonical still points to Blue would leave
    NO live canonical. Instead write RETIRE_PENDING and surface. When
    ``expected_green_generation`` is None the gate is OFF (legacy/back-compat)."""
    store = BgStateStore(wal_dir, root)
    doc = store.read()
    state = doc.get("state")
    if state != "DEGRADED":
        return {"reaped": False, "state": state, "reason": None}
    reason = _last_reason(doc)
    if reason not in _RESUMABLE_REASONS:
        return {"reaped": False, "state": state, "reason": reason}

    # P0.3 reap-gate: verified-promotion re-read of write-truth canonical.
    if expected_green_generation is not None:
        reader = canonical_reader or _read_canonical_generation
        current = reader(orchestra_dir, root)
        if current != expected_green_generation:
            store.write_state(
                "RETIRE_PENDING",
                reason=f"reap-gate:canonical-not-green(got={current},"
                       f"want={expected_green_generation})")
            return {"reaped": False, "state": "RETIRE_PENDING",
                    "reason": "canonical-not-green", "gated": True}

    # Drive the reap as a BOUNDED effect (v2-1 fires it, v2-2 bounds it). A hang
    # converts to a timeout -> raise (firewall alarms); a raised reap surfaces too.
    ok, timed_out, error = _run_effects_bounded(
        [lambda: seams.reap(root, blue_generation_id)], timeout_s)
    if timed_out:
        raise CompletionTimeout(
            f"reap of {root!r} blue_gen={blue_generation_id} exceeded "
            f"{timeout_s}s — surfacing as a raise so the beat can alarm/disarm")
    if error is not None:
        raise error   # a raised reap -> firewall catches it (alarm/disarm)

    # FINISH-SWAP (STEP 1, DEC-1789416608601489 v3): after the process reap, run the
    # FULL post-commit effect set so a swap finishes CLEAN, not effects-incomplete.
    # Opt-in via orchestra_dir — None keeps the legacy path (reap -> DRAINED), so this
    # is INERT off the live supervisor path (which always passes orchestra_dir).
    if orchestra_dir is None:
        store.write_state("DRAINED", reason="swap-complete")
        return {"reaped": True, "state": "DRAINED", "reason": reason}

    fin = _run_finish_effects(
        root, wal_dir=wal_dir, orchestra_dir=orchestra_dir,
        expected_green_generation=expected_green_generation, tmux_ops=tmux_ops)
    if fin.get("deferred"):
        # ATTACHED-green (OQ-1, gm-endorsed): promoted_at stamped, consolidation
        # deferred, effects_status stays 'effects-incomplete', NO DRAIN — a later
        # DETACHED beat re-runs and finishes it (idempotent). 'complete' therefore
        # strictly means fully-consolidated-to-bare.
        return {"reaped": True, "state": "DEGRADED", "reason": reason,
                "consolidated": False, "deferred": fin["deferred"]}

    store.write_state("DRAINED", reason="swap-complete")
    return {"reaped": True, "state": "DRAINED", "reason": reason,
            "effects": "complete", "consolidated": True}


class _RealTmux:
    """Live tmux ops for the finish-swap consolidation. kill_session is idempotent
    (a missing session is a no-op, never a raise); rename_session RAISES on failure so
    a real name collision surfaces to the beat firewall (negative-control safety)."""

    @staticmethod
    def _run(args):
        import subprocess
        return subprocess.run(["tmux", *args], capture_output=True, text=True)

    def session_exists(self, name):
        return self._run(["has-session", "-t", name]).returncode == 0

    def pane_pid(self, name):
        r = self._run(["display-message", "-p", "-t", f"{name}:0.0", "#{pane_pid}"])
        if r.returncode != 0:
            return None
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None

    def find_session_by_pid(self, pid):
        r = self._run(["list-panes", "-a", "-F", "#{session_name} #{pane_pid}"])
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == str(pid):
                return parts[0]
        return None

    def kill_session(self, name):
        self._run(["kill-session", "-t", name])   # idempotent: ignore 'no such session'

    def rename_session(self, old, new):
        r = self._run(["rename-session", "-t", old, new])
        if r.returncode != 0:
            raise RuntimeError(
                f"tmux rename-session {old!r}->{new!r} failed: {r.stderr.strip()}")

    def is_attached(self, name):
        r = self._run(["list-clients", "-t", name, "-F", "#{client_name}"])
        return r.returncode == 0 and bool(r.stdout.strip())


def _stamp_promoted_at(conn, root, generation):
    """Idempotent promoted_at stamp on the green generation (IS-NULL guard). Stamped
    only AFTER the reap (OQ-2): a promotion whose blue is provably gone."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "UPDATE generations SET promoted_at=? "
        "WHERE root=? AND generation=? AND promoted_at IS NULL",
        (now, root, generation))


def _consolidate_green_to_bare(conn, root, green_session, wal_dir, tmux):
    """Consolidate the green {root}-g{N} pane down to bare {root} + repoint canonical.
    Idempotent + collision-safe. If the green session no longer exists, consolidation
    already happened (green runs at bare {root}) -> repoint-only no-op."""
    if not tmux.session_exists(green_session):
        conn.execute("UPDATE canonical SET tmux_session=? WHERE root=?", (root, root))
        return
    # (a) kill blue's REAL live session, resolved BY EFFECT from the recorded
    # blue_pane_pid (prewarm) — robust even in a degraded chain where blue is NOT at
    # bare {root} ({root}-g{blue}). Never kill green.
    recorded = BgStateStore(wal_dir, root).read_meta("blue_pane_pid")
    if recorded is not None:
        blue_session = tmux.find_session_by_pid(recorded)
        if blue_session and blue_session != green_session:
            tmux.kill_session(blue_session)
    # (b) belt: clear any remaining bare-{root} holder (a stale/older husk) that is not
    # green, so the rename target name is free.
    if (tmux.session_exists(root)
            and tmux.pane_pid(root) != tmux.pane_pid(green_session)):
        tmux.kill_session(root)
    # (c) rename green {root}-g{N} -> bare {root} (collision-free after a+b).
    tmux.rename_session(green_session, root)
    # (d) repoint canonical.tmux_session -> bare {root}.
    conn.execute("UPDATE canonical SET tmux_session=? WHERE root=?", (root, root))


def _run_finish_effects(root, *, wal_dir, orchestra_dir,
                        expected_green_generation, tmux_ops):
    """Run the post-commit effect set for a just-reaped swap. Returns
    {consolidated: bool} on success, or {deferred:'attached'} when the operator is attached to
    the green pane (OQ-1: defer consolidation, stay effects-incomplete)."""
    import os
    from identity_store.orchestra_db import get_connection, run_post_commit_effects
    tmux = tmux_ops or _RealTmux()
    green_session = f"{root}-g{expected_green_generation}"
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    conn = get_connection(db)
    try:
        row = conn.execute(
            "SELECT s.id FROM swaps s JOIN generations g "
            "ON g.id = s.green_generation_id "
            "WHERE s.root=? AND g.generation=? "
            "AND s.effects_status='effects-incomplete' "
            "ORDER BY s.id DESC LIMIT 1",
            (root, expected_green_generation)).fetchone()
        if row is None:
            # No effects-incomplete swap row for the expected green -> nothing to do
            # (already complete, or a legacy/no-swap-row path). Belt idempotency.
            return {"consolidated": True}
        swap_id = row[0]

        # the operator-attach gate BY EFFECT, immediately before consolidation (never rename a
        # session the operator is attached to). Defer WITHOUT flipping complete / DRAINing.
        if tmux.is_attached(green_session):
            _stamp_promoted_at(conn, root, expected_green_generation)
            return {"deferred": "attached", "consolidated": False}

        # DETACHED: route promoted_at + consolidation through run_post_commit_effects so
        # effects_status flips 'complete' ONLY after every effect succeeds. A raising
        # effect propagates BEFORE the flip (negative control) — no complete, no DRAIN.
        run_post_commit_effects(conn, swap_id, [
            lambda: _stamp_promoted_at(conn, root, expected_green_generation),
            lambda: _consolidate_green_to_bare(
                conn, root, green_session, wal_dir, tmux),
        ])
        return {"consolidated": True}
    finally:
        conn.close()
