"""recovery.py — pre-retire CONTEXT-ASSIST (DEC-1787808620 A.3 rework, re-congruence
DEC-1787817982 CONSENSUS).

The enrichment lever that REPLACES rollback for the common "successor needs a bit more
context" case: while the predecessor is STILL ALIVE (pre prompt-retire), if the
successor is struggling (not yet confirmed progressing), a trigger routes to the
predecessor asking it to inject ADDITIONAL context to strengthen the successor's
understanding. Fires ONLY when struggling AND the predecessor is still live (gm check
2: it must route to the LIVE predecessor). Best-effort — a send failure never breaks
the beat.
"""


def maybe_context_assist(predecessor, successor, *, struggling,
                         predecessor_live_fn, assist_fn):
    """Fire the pre-retire context-assist iff the successor is struggling AND the
    predecessor is still live. Returns True if the assist fired, else False.

    Seams (injectable):
      predecessor_live_fn(predecessor) -> bool : the predecessor session is still alive.
      assist_fn(predecessor, successor)        : route the enrichment request to the
                                                 predecessor (inject a context-assist msg).
    """
    if not struggling:
        return False
    try:
        if not predecessor_live_fn(predecessor):
            return False                       # predecessor gone -> cannot assist
    except Exception:  # noqa: BLE001 -- fail toward NOT-assisting (never crash the beat)
        return False
    try:
        assist_fn(predecessor, successor)
        return True
    except Exception:  # noqa: BLE001 -- best-effort; a send failure never breaks the beat
        return False
