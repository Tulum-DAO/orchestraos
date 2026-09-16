"""H10 — concurrency control (WS3 v2, DEC-1786724046).

The WS1-WS4 end-state is a daemon BEAT over the fleet: many agents, potentially
concurrent rotations. execute_rotation is deliberately single-canary; this module
adds the mutual-exclusion the fleet beat needs, so two simultaneous rotations
can't (a) cross-write the focus store, (b) rotate an agent that is the live
verifier / escalation-target of another in-flight rotation, or (c) rotate a
predecessor that is itself mid-S3-verification.

Pure decision layer over an injected `now` + the set of in-flight rotations; the
daemon persists the lock state (flock + atomic write) and calls can_rotate()
before starting, acquire()/release() around a rotation. Stale locks (older than
LOCK_TTL_S — a crashed daemon) are reclaimable so a lock can't wedge the fleet.
"""

LOCK_TTL_S = 3600  # a lock older than this is stale (crashed mid-rotation) -> reclaimable


def _active(locks, now):
    """The non-stale in-flight rotation rows."""
    return [l for l in locks.get("rotations", [])
            if (now - l["acquired_at"]) < LOCK_TTL_S]


def can_rotate(locks, *, canary, lineage_root, now):
    """Return {ok: bool, reason}. Blocks when:
      - a rotation for the SAME lineage is already in flight (one per lineage), OR
      - `canary` is the verifier/escalation-target of an in-flight rotation, OR
      - `canary` is itself the predecessor OR successor of an in-flight rotation.
    Stale locks (> LOCK_TTL_S) are ignored (reclaimable)."""
    for l in _active(locks, now):
        if l["lineage_root"] == lineage_root:
            return {"ok": False, "reason": f"lineage {lineage_root} already rotating"}
        if canary in (l.get("verifier"), l.get("escalation_target")):
            return {"ok": False,
                    "reason": f"{canary} is the verifier/escalation-target of "
                              f"in-flight rotation {l['canary']}"}
        if canary in (l["canary"], l.get("successor")):
            return {"ok": False,
                    "reason": f"{canary} is already party to in-flight rotation "
                              f"{l['canary']}"}
    return {"ok": True, "reason": "clear"}


def acquire(locks, *, canary, lineage_root, successor, now,
            verifier=None, escalation_target="gm"):
    """Record an in-flight rotation. Caller MUST have checked can_rotate() first.
    Returns a NEW locks dict."""
    rows = list(locks.get("rotations", []))
    rows.append({
        "canary": canary,
        "lineage_root": lineage_root,
        "successor": successor,
        "verifier": verifier or canary,      # the still-alive predecessor runs S3
        "escalation_target": escalation_target,
        "acquired_at": now,
    })
    return {**locks, "rotations": rows}


def release(locks, *, canary, lineage_root):
    """Drop the in-flight row for this rotation. Returns a NEW locks dict."""
    rows = [l for l in locks.get("rotations", [])
            if not (l["canary"] == canary and l["lineage_root"] == lineage_root)]
    return {**locks, "rotations": rows}


def reap_stale(locks, now):
    """Drop locks older than LOCK_TTL_S (a daemon that crashed mid-rotation). A
    lock must never permanently wedge the fleet. Returns a NEW locks dict."""
    return {**locks, "rotations": _active(locks, now)}
