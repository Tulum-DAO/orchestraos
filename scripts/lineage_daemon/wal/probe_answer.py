"""probe_answer — compute the WAL-probe answer a PERFECT green produces.

The verify seam grades Green's answer (last-K WAL events + working-set) against the
WAL via probe.grade_probe (the WAL is the answer key). This module produces that
answer FROM the WAL. It is DUAL-USE:

  * DRILL (B): the shakedown harness calls write_probe_answer between beat1 and beat2
    to inject the correct answer for a PERFECT green, so verify's grade_probe runs FOR
    REAL and passes — unblocking end-to-end validation of the downstream high-blast-
    radius seams (swap/completion/reap) OFF the operator's watch. A drill-injected answer does
    NOT satisfy gm-gate bar #4 (lossless-by-effect requires the GREEN to produce it).

  * REAL (A, next): the green computes its OWN answer from its ingested WAL using
    compute_probe_answer and commits <green>.probe.json as part of BG-green boot —
    that IS the true lossless proof. Same function both sides => the drill validates
    exactly the shape the real green must emit.

compute_probe_answer mirrors what grade_probe checks (probe.py): the last-K events'
{seq, summary} in order + the working-set paths — so a green that genuinely ingested
the WAL reproduces it, and a wedged/lossy green cannot.
"""
import json
import os

from .probe import _working_set


def compute_probe_answer(store, lineage_root, k=5):
    """The probe answer a perfect green produces: last-K WAL events (seq+summary, in
    order) + the working-set paths. Identical shape to what grade_probe expects."""
    events = store.events(lineage_root)
    last_events = [{"seq": r["seq"], "summary": r["summary"]} for r in events[-k:]]
    return {"last_events": last_events, "working_set": _working_set(store, lineage_root)}


def _probe_path(orchestra_dir, green_alias):
    return os.path.join(orchestra_dir, "state", "agent-handoffs",
                        f"{green_alias}.probe.json")


def write_probe_answer(orchestra_dir, root, green_alias, store, k=5):
    """Compute the answer from the REAL WAL and write it to the exact path the verify
    seam reads (_default_probe_answer_fn): <orch>/state/agent-handoffs/<green>.probe.json.
    Atomic temp+rename. Returns the path. DRILL/GREEN helper — the caller decides
    whether this is a drill injection or the real green committing its own answer."""
    answer = compute_probe_answer(store, root, k=k)
    path = _probe_path(orchestra_dir, green_alias)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(answer, fh, indent=2)
    os.replace(tmp, path)
    return path
