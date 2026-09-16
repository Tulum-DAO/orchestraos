"""hydrate_green — the delta-hydrate seam for the live-pane drill.

Replays Blue's WAL DELTA (events with seq > last-hydrated) into Green each beat so
Green's working state is <= one beat stale at swap time — that staleness bound is
what makes the handoff lossless (gm bar #4). Properties:

  * DELTA only: render_digest(since_seq=<last>) emits only new events — no re-inject
    storm; a fully-hydrated re-entry ships nothing.
  * RESUMABLE + IDEMPOTENT: the last-hydrated seq is PERSISTED in bg_state
    (write_meta), so each beat resumes from the high-water mark and a crash re-enters
    cleanly.
  * COURT-SCRUB gated: the scrub seam is threaded into render_digest so poisoned WAL
    bodies are handled bytes-only (contagion firewall — the builder must not become a
    carrier).
  * DURABLE delivery: the injected deliver_fn is durable (default msg_store), NEVER
    tmux-only — a dropped ephemeral inject would be silent context loss. If delivery
    RAISES, the seq is NOT advanced, so the next beat re-ships the same delta.
"""
from .digest import render_digest
from .court_scrub import court_scrub


def _default_deliver(orchestra_dir):
    """Durable delivery: a msg_store lineage-hydrate row Green reads (auditable,
    survives a crash). Never tmux-only.

    The structured ingested-view artifact rides the SAME row as
    metadata['ingested_view'] — NOT a side file. That is load-bearing for bar #4:
    the green-boot hook parses the ACTUALLY-DELIVERED row, so grading proves the
    DELIVERY CHANNEL lossless (a side file would grade the writer, not the channel).
    Sends into <orchestra_dir>/state/tasks.db so it lands in the exact db the green
    reads (green_boot_probe.collect_ingested_artifact)."""
    import os
    from msg_store import MessageStore

    db_path = (os.path.join(orchestra_dir, "state", "tasks.db")
               if orchestra_dir else None)

    def deliver(root, green_alias, text, ingested_view=None, continue_capsule=None):
        md = {}
        if ingested_view:
            md["ingested_view"] = ingested_view
        if continue_capsule:  # BG Layer-3: the structured working-state capsule (auditable)
            md["continue_capsule"] = continue_capsule
        MessageStore(db_path=db_path).send(
            from_agent="lineage-daemon", to_agent=green_alias,
            type="lineage_hydrate", priority="high",
            subject=f"[WAL DELTA] hydrate from {root} blue",
            body=text, source="bg-hydrate",
            metadata=md or None)
    return deliver


def _ingested_view(store, root, *, since_seq, through_seq):
    """The structured ingested-view artifact for the delta (since_seq, through_seq].
    Carries {seq, kind, summary, body_ref} per event — `kind` REQUIRED (the green's
    probe._working_set filters kind==file_mod) and `body_ref` is the working-set
    source. The green derives working_set from these events (SR2: single source), so
    there is NO separate working_set field."""
    events = [{"seq": r["seq"], "kind": r["kind"], "summary": r["summary"],
               "body_ref": r["body_ref"]}
              for r in store.events(root)
              if since_seq < r["seq"] <= through_seq]
    return {"lineage_root": root,
            "scope": {"since_seq": since_seq, "through_seq": through_seq},
            "events": events}


def hydrate_green(root, green_alias, *, since_seq, wal_dir, store,
                  deliver_fn=None, scrub=None, orchestra_dir=None,
                  ceiling_tokens=800_000, resolve_body=None, prompt_n=3):
    """Ship Blue's WAL delta (seq > effective since_seq) to Green durably, then
    advance the persisted last-hydrated seq. since_seq=None => use the persisted
    value (default 0). Returns {count, empty, since_seq, new_since_seq, delivered}.

    BG Layer-3: a deterministic working-state CONTINUE-CAPSULE (objective + next
    action + working set, from blue's CURRENT directive thread, scrub-gated) is
    prepended to the digest text AND carried in metadata['continue_capsule'], so a
    booted green resumes blue's CURRENT objective instead of latching a salient old
    thread. resolve_body reads prompt bodies (world-input); default = the transcript
    reader. Capsule build is FAIL-SAFE: a failure never aborts the hydrate beat."""
    from .bg_state import BgStateStore
    from . import continue_capsule as _cc

    st = BgStateStore(wal_dir, root)
    if since_seq is None:
        since_seq = st.read_meta("last_hydrated_seq", 0) or 0
    scrub = scrub or court_scrub
    deliver = deliver_fn or _default_deliver(orchestra_dir)

    digest = render_digest(store, root, since_seq=since_seq,
                           ceiling_tokens=ceiling_tokens, scrub=scrub)
    count = sum(1 for r in store.events(root) if r["seq"] > since_seq)
    new_since = digest["max_seq"]

    if count == 0:
        return {"count": 0, "empty": True, "since_seq": since_seq,
                "new_since_seq": since_seq, "delivered": False}

    # Structured ingested-view artifact for THIS delta (rides the same row).
    ingested_view = _ingested_view(store, root, since_seq=since_seq,
                                   through_seq=new_since)

    # BG Layer-3: build the working-state continue-capsule (FAIL-SAFE — a build error
    # must never abort the delivery, the critical path). Objective anchored on the
    # CURRENT directive thread over the FULL WAL (not just the delta), scrub-gated.
    capsule, text = None, digest["text"]
    try:
        rb = resolve_body if resolve_body is not None else _cc.resolve_prompt_body
        # M1a (leg-(ii)): thread the blue-authored BUILD-CHECKPOINT (if the producer
        # wrote one) so the green resumes blue's EXPLICIT objective (source=checkpoint)
        # instead of an INFERRED directive-thread. Fail-safe: absent/broken => None =>
        # build_continue_capsule falls to the directive-thread (unchanged behavior).
        checkpoint = _cc.load_build_checkpoint(orchestra_dir, root) if orchestra_dir else None
        capsule = _cc.build_continue_capsule(store, root, resolve_body=rb,
                                             scrub=scrub, prompt_n=prompt_n,
                                             checkpoint=checkpoint)
        text = _cc.render_capsule_banner(capsule) + "\n\n" + digest["text"]
    except Exception:  # noqa: BLE001 — capsule is best-effort; never block hydrate.
        capsule, text = None, digest["text"]

    # DURABLE delivery FIRST; only advance the persisted seq on success (a raise
    # propagates -> seq unchanged -> next beat re-ships; the firewall alarms).
    deliver(root, green_alias, text, ingested_view, capsule)
    st.write_meta("last_hydrated_seq", new_since)
    # Record the green's hydrate BASELINE once (the first delivered since_seq) —
    # grade_probe's authoritative scope 'since' reads this (0 for a fresh green, B
    # for a mid-life green). Blue-side truth; never a green-supplied value.
    if st.read_meta("first_hydrated_seq", None) is None:
        st.write_meta("first_hydrated_seq", since_seq)
    return {"count": count, "empty": False, "since_seq": since_seq,
            "new_since_seq": new_since, "delivered": True}
