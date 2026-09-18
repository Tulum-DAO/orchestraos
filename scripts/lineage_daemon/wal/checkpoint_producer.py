#!/usr/bin/env python3
"""checkpoint_producer — the BUILD-CHECKPOINT producer (BG leg-(ii) M1a, spec §3).

The ONE genuinely-new design. A green resumes Blue's objective from the capsule
(continue_capsule): priority checkpoint > directive-thread > degraded. M1a supplies the
checkpoint at the swap boundary so the green resumes Blue's EXPLICIT current objective
instead of an inferred one.

Option (a) — SELF-AUTHOR (Target B, supervised): the driver injects Blue a bounded
"author your BUILD-CHECKPOINT to <path>" and BOUNDED-WAITS for the file.
Option (b) — WAL-DERIVED (the FALLBACK): synthesize the objective from Blue's WAL
directive-thread (no Blue cooperation).

gm consensus condition (binding): the (a) live-Blue inject MUST be IDEMPOTENT + bounded-
wait with a HARD auto-fallback to (b), so a NON-RESPONSIVE Blue NEVER stalls the swap.
inject + clock are dependency-injected so this is testable without a live pane. The
producer WRITES the file; hydrate_green loads it (continue_capsule.load_build_checkpoint).
INERT: nothing calls produce_checkpoint until the supervised driver (or the A-time
autonomous path) invokes it; it never runs inside the beat by itself.
"""
import json
import os
import time

from . import continue_capsule as _cc


def wal_derived_checkpoint(store, root, *, resolve_body=None, scrub=None, prompt_n=3):
    """(b) FALLBACK: synthesize a checkpoint from Blue's WAL directive-thread. Returns
    ``{objective, next_action, source, lineage_root, authored_at_seq}`` or None when the
    WAL yields no real objective (degraded) — None lets hydrate fall to its OWN
    directive-thread / degraded path (never a fabricated objective). Stamps
    lineage_root + authored_at_seq (the WAL head at authoring) for staleness (cond 3)."""
    cap = _cc.build_continue_capsule(store, root, resolve_body=resolve_body,
                                     scrub=scrub, prompt_n=prompt_n)
    if cap.get("degraded") or not cap.get("objective") \
            or cap["objective"] == _cc.UNKNOWN_OBJECTIVE:
        return None
    return {"objective": cap["objective"],
            "next_action": cap.get("next_action"),
            "source": "wal-derived",
            "lineage_root": root,
            "authored_at_seq": store.max_seq()}


def _is_fresh(checkpoint, root, fresh_since_seq):
    """Cond 3 staleness gate: a checkpoint is fresh iff its lineage matches (when
    stamped) AND it was authored at/after the current prewarm baseline. fresh_since_seq
    None => no seq gate (back-compat: any lineage-matching checkpoint is fresh)."""
    lr = checkpoint.get("lineage_root")
    if lr is not None and lr != root:
        return False
    if fresh_since_seq is None:
        return True
    return checkpoint.get("authored_at_seq", -1) >= fresh_since_seq


def write_build_checkpoint(orchestra_dir, root, checkpoint):
    """Atomically write the checkpoint to the convention path. Returns the path."""
    path = _cc.build_checkpoint_path(orchestra_dir, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(checkpoint, fh)
    os.replace(tmp, path)
    return path


def produce_checkpoint(root, orchestra_dir, store, *, inject_fn=None,
                       operator_attached_fn=None, fresh_since_seq=None,
                       poll_interval_s=0.5, timeout_s=30.0, resolve_body=None,
                       scrub=None, prompt_n=3, now=None, sleep=None):
    """Ensure a FRESH BUILD-CHECKPOINT exists for ``root`` before the swap. Returns the
    checkpoint dict now in place, or None if neither Blue nor the WAL yields one (then
    hydrate falls to the directive-thread).

    Flow (gm build-conditions 1-3): IDEMPOTENT re-entry — reuse an existing checkpoint
    ONLY if fresh (cond 3: lineage match + authored_at_seq >= fresh_since_seq); a STALE
    one is re-produced. (cond 2) if the operator is attached to Blue's pane (operator_attached_fn),
    SKIP the (a) inject (never pane-nudge a seat the operator is on) and go straight to (b).
    Else (a) ask Blue ONCE (inject_fn) + BOUNDED-WAIT; on a fresh file → stamp lineage +
    seq and return. On timeout/absent/stale/the operator-attached → HARD fallback to (b)
    wal_derived + durable write. Bounded + fallback ⇒ a non-responsive Blue never stalls."""
    now = now or time.time
    sleep = sleep or time.sleep

    existing = _cc.load_build_checkpoint(orchestra_dir, root)
    if existing is not None and _is_fresh(existing, root, fresh_since_seq):
        return existing  # idempotent: a FRESH checkpoint is honored (crash re-entry / prior)

    operator_here = bool(operator_attached_fn and operator_attached_fn())
    if inject_fn is not None and not operator_here:
        inject_fn(root)                      # (a) ask Blue to author it (idempotent single ask)
        deadline = now() + timeout_s
        while now() < deadline:
            cp = _cc.load_build_checkpoint(orchestra_dir, root)
            if cp is not None and _is_fresh(cp, root, fresh_since_seq):
                cp.setdefault("lineage_root", root)
                cp.setdefault("authored_at_seq", store.max_seq())
                return cp                    # Blue authored a fresh one in time
            sleep(poll_interval_s)

    derived = wal_derived_checkpoint(store, root, resolve_body=resolve_body,
                                     scrub=scrub, prompt_n=prompt_n)  # (b) HARD fallback
    if derived is not None:
        write_build_checkpoint(orchestra_dir, root, derived)
    return derived


def _proc_alive(pid):
    """Default aliveness for the supervised wrapper: the REAL /proc/{pid} check."""
    try:
        return os.path.exists(f"/proc/{int(pid)}")
    except (TypeError, ValueError):
        return False


def produce_checkpoint_supervised(root, orchestra_dir, store, *, blue_pid, blue_session,
                                  real_inject_fn, alive_fn=None, operator_attached_fn=None,
                                  fresh_since_seq=None, **kwargs):
    """(ii-B) SUPERVISED wrapper over ``produce_checkpoint`` for the hand-drive driver
    (bg_live_beat.py). Adds the TWO gm-required caller conditions ON TOP of
    produce_checkpoint's own operator-gate + bounded-wait→(b) fallback:

      cond 2 — blue_pid DEAD (``/proc/{pid}`` via ``alive_fn``): SKIP the (a) inject and
               go straight to (b) — a corpse never writes, so don't burn the timeout.
      cond 1 — the operator attached to Blue's pane (``operator_attached_fn``): SKIP the (a) inject
               (never pane-nudge a seat the operator is on) and go straight to (b).

    The (a) live-Blue inject (``real_inject_fn``) is threaded into ``produce_checkpoint``
    ONLY when ``(alive and not operator)``; otherwise ``inject_fn=None`` so the producer takes
    its WAL-derived (b) path. ``blue_session`` is the pane the caller's ``real_inject_fn``
    targets (documented here for the glue).

    TOCTOU CLOSE (arm-precondition c): ``operator_attached_fn`` is ALSO re-passed into
    ``produce_checkpoint`` so it RE-CHECKS the operator-attachment just before the inject — if the operator
    attaches in the window between this wrapper's check (T0) and the actual inject (T1), the
    producer's own gate skips the inject and falls to (b). Accepts a 2nd tmux list-clients;
    never pane-nudges a seat the operator stepped onto mid-swap.

    Delegation keeps the idempotent/bounded-wait/staleness (cond 3) logic in ONE place
    (``produce_checkpoint`` @9ca96dd92e / @99aee1ffb1). INERT: nothing calls this until the
    supervised driver invokes it, and BG_DISABLED still gates the whole beat."""
    alive = (alive_fn or _proc_alive)(blue_pid)
    operator = bool(operator_attached_fn and operator_attached_fn())
    inject_fn = real_inject_fn if (alive and not operator) else None
    return produce_checkpoint(root, orchestra_dir, store, inject_fn=inject_fn,
                              operator_attached_fn=operator_attached_fn,
                              fresh_since_seq=fresh_since_seq, **kwargs)
