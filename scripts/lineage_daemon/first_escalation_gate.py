"""first_escalation_gate.py — the FIRST-escalation human-gate (DEC-1787808620 A.3
rework; gm pre-arm condition). Salvaged verbatim from the git-preserved
first_rollback_gate (@820807ec9), s/rollback/escalate/.

A false or premature escalation on the very first live completion is an INVESTIGATE
event, never silent behavior. The first-ever escalation for an armed lineage HOLDS the
auto-surface path, fires a LOUD notify + drops a non-synced once-sentinel; later
escalations hold until a human clears the lineage (adds it to
<runtime>/escalation_armed). Mirrors cron_beat._first_skip_notify.

Both A.3 loud-escalation cases use this gate: (1) pre-retire never-progressed-after-
assist -> retire-anyway + escalate; (2) post-retire no-first_effect-by-30min -> escalate.
"""
import os


def _runtime_dir(override=None):
    if override:
        return override
    try:
        from scripts.lineage_daemon import fleet as _fleet
        return _fleet._runtime_dir()
    except Exception:  # noqa: BLE001
        return os.path.expanduser("~/runtime")


def _cleared(lineage_root, rt) -> bool:
    """True iff the lineage is in <runtime>/escalation_armed (a human has reviewed the
    first gated escalation and armed auto-escalation for this lineage). Absent/unreadable
    => NOT cleared (fail-closed — the human-gate stays shut)."""
    try:
        with open(os.path.join(rt, "escalation_armed"), errors="replace") as fh:
            armed = {s.strip() for s in fh.read().splitlines()
                     if s.strip() and not s.strip().startswith("#")}
        return lineage_root in armed
    except Exception:  # noqa: BLE001
        return False


def first_escalation_gate(lineage_root, *, runtime_dir=None, notify_fn=None, ctx=None):
    """Return {proceed, reason, notified}. proceed=True ONLY when the lineage has been
    human-cleared (escalation_armed). Otherwise the FIRST call fires notify_fn + drops
    the once-sentinel (reason 'first-escalation-human-gate'); subsequent calls hold
    silently (reason 'awaiting-human-clear'). A notify failure NEVER flips proceed."""
    rt = _runtime_dir(runtime_dir)

    if _cleared(lineage_root, rt):
        return {"proceed": True, "reason": "cleared", "notified": False}

    sentinel = os.path.join(rt, f"FIRST_ESCALATION_GATED_{lineage_root}")
    if os.path.exists(sentinel):
        return {"proceed": False, "reason": "awaiting-human-clear", "notified": False}

    notified = False
    if notify_fn is not None:
        try:
            notify_fn(lineage_root, ctx)
            notified = True
        except Exception:  # noqa: BLE001 -- notify is best-effort; the gate still HOLDS
            notified = False
    try:
        os.makedirs(rt, exist_ok=True)
        open(sentinel, "w").write(lineage_root)
    except Exception:  # noqa: BLE001
        pass
    return {"proceed": False, "reason": "first-escalation-human-gate", "notified": notified}
