#!/usr/bin/env python3
"""BG sequence A (bar#4, gm ruling DEC-1788609056) — the deterministic green-boot hook
that makes the REAL green PRODUCE its probe answer from its INGESTED view.

It reads the green's ACTUALLY-DELIVERED ``lineage_hydrate`` msg_store row(s) — NOT
``state/wal/<root>.db`` (that is the grader's answer key: reading it grades tautologically =
Potemkin) and NOT a side file (that would grade the artifact WRITER, not the delivery
channel) — reconstructs the probe answer from the structured ingested-view artifact those
rows carry (``metadata['ingested_view']``), and commits
``state/agent-handoffs/<green_alias>.probe.json`` (the path the verify seam reads). So a
lossy/broken msg_store delivery makes the green's parsed answer diverge from Blue's WAL →
grade fails = the real channel-fidelity signal (exactly what bar#4 must prove).

DETERMINISTIC (no LLM). Marker-gated: runs only when ``BG_GREEN_ROOT`` is set (spawn-agent.sh
invokes it for a BG green; a normal spawn never calls it). FAIL-SAFE: any error → loud
stderr + non-zero exit, but the CALLER must NOT abort the green spawn — the verify seam then
fail-closes the swap safely (no ``probe.json``), no worse than drill-inject-off today.

Contract with RAB/gen5 (producer side): ``hydrate_green._default_deliver`` carries the
structured artifact in the SAME ``lineage_hydrate`` msg_store row it already sends, as
``metadata['ingested_view'] = {lineage_root, scope:{since_seq,through_seq},
events:[{seq,kind,summary,body_ref}...], working_set?}``. ``grade_probe`` is made
delivered-scope-aware (RAB) so a mid-life green is graded only on the delta it received.
"""
import json
import os
import sys

# scripts/lineage_daemon/wal/green_boot_probe.py → repo root is four parents up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for _p in (_REPO, os.path.join(_REPO, "scripts")):  # msg_store at root; lineage_daemon under scripts/
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _ArtifactStore:
    """Adapt the delivered ingested-view artifact to the ``WalStore.events()`` shape so the
    EXISTING ``probe_answer.compute_probe_answer`` / ``probe._working_set`` run UNCHANGED —
    the produced answer is byte-identical to what ``grade_probe`` checks, with NO forked
    compute path (guaranteed shape parity)."""

    def __init__(self, events):
        # events: [{seq, kind, summary, body_ref}]; dedup by seq, keep seq order.
        merged = {}
        for e in events:
            merged[e["seq"]] = {"seq": e["seq"], "kind": e.get("kind"),
                                "summary": e.get("summary"), "body_ref": e.get("body_ref")}
        self._events = [merged[s] for s in sorted(merged)]

    def events(self, lineage_root=None):  # signature mirrors WalStore.events
        return self._events


def collect_ingested_artifact(orchestra_dir, green_alias):
    """Parse the green's ACTUALLY-DELIVERED ``lineage_hydrate`` msg_store row(s) and merge
    their ingested-view events by seq. Returns ``(lineage_root, events, scope)``. Raises if
    no row / no artifact was delivered (the fail-safe path handles it)."""
    from msg_store import MessageStore
    db_path = os.path.join(orchestra_dir, "state", "tasks.db")
    rows = MessageStore(db_path=db_path).query(to_agent=green_alias, type="lineage_hydrate")
    if not rows:
        raise RuntimeError(f"no lineage_hydrate row delivered to {green_alias!r}")
    events, root, since, through = {}, None, None, None
    for row in rows:
        md = row.get("metadata")
        if not md:
            continue
        md = json.loads(md) if isinstance(md, str) else md
        iv = md.get("ingested_view")
        if not iv:
            continue
        root = root or iv.get("lineage_root")
        for e in iv.get("events", []):
            events[e["seq"]] = e
        sc = iv.get("scope") or {}
        if sc.get("since_seq") is not None:
            since = sc["since_seq"] if since is None else min(since, sc["since_seq"])
        if sc.get("through_seq") is not None:
            through = sc["through_seq"] if through is None else max(through, sc["through_seq"])
    if not events:
        raise RuntimeError(
            f"lineage_hydrate row(s) for {green_alias!r} carried no ingested_view artifact "
            f"(RAB producer not enriched yet, or delivery lost it)")
    return root, [events[s] for s in sorted(events)], {"since_seq": since, "through_seq": through}


def produce_probe_answer(orchestra_dir, root, green_alias, k=5):
    """Reconstruct + commit the probe answer from the DELIVERED ingested view. Reuses
    ``probe_answer.write_probe_answer`` via ``_ArtifactStore`` so the shape matches
    ``grade_probe`` exactly. Returns the committed path."""
    from lineage_daemon.wal import probe_answer
    ing_root, events, _scope = collect_ingested_artifact(orchestra_dir, green_alias)
    store = _ArtifactStore(events)
    return probe_answer.write_probe_answer(orchestra_dir, root or ing_root, green_alias, store, k=k)


def main():
    root = os.environ.get("BG_GREEN_ROOT")
    green_alias = os.environ.get("BG_GREEN_ALIAS")
    orch = os.environ.get("ORCHESTRA_DIR", _REPO)
    try:
        k = int(os.environ.get("BG_GREEN_PROBE_K", "5"))
    except (TypeError, ValueError):
        k = 5
    if not root or not green_alias:
        return 0  # marker absent → INERT no-op (spawn-agent.sh only calls us when BG_GREEN_ROOT set)
    try:
        path = produce_probe_answer(orch, root, green_alias, k=k)
        sys.stderr.write(f"[bg-green-probe] {green_alias}: committed probe answer -> {path}\n")
        return 0
    except Exception as e:  # noqa: BLE001 — FAIL-SAFE: never abort the green spawn.
        sys.stderr.write(
            f"[bg-green-probe] {green_alias}: FAILED to produce probe answer ({e!r}). The "
            f"green spawn CONTINUES; the verify seam will fail-close the swap safely (no "
            f"probe.json). Not fatal to boot.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
