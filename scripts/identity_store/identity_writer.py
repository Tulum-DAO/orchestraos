"""Flag-aware identity write routing — the seam every rewired writer calls.

Each of the enumerated direct writers (rotate_agent, promote_successor,
park-idle, session-index, the shell writers, ...) gets its direct-JSON write
replaced by a call to one of these ops:

    if not identity_writer.update_registry_agent(od, agent_id, fields):
        <existing legacy JSON write>   # unchanged

When the cutover flag is inactive (default) the op returns False and the writer
does exactly what it does today (INERT). When active, the op writes the
transactional store via the DB shims and returns True (handled) — the projector
regenerates the JSON artifacts, so the legacy write is skipped.
"""
import os

from scripts.identity_store import cutover, freeze, orchestra_db, shims


def _db_path(orchestra_dir):
    return os.path.join(orchestra_dir, "state", "orchestra-registry.db")


def _blue_resume_builder():
    """Injected into execute_swap so the low-level store never imports the promote
    layer (layering). Returns resume_command_for (per-runtime resume adapter; RAISES on
    a non-resumable/undeclared runtime — execute_swap swallows that and leaves the blue
    resume NULL rather than guessing). Lazy import keeps the flag-off surface minimal."""
    try:
        from scripts.promote_successor import resume_command_for
        return resume_command_for
    except Exception:
        return None


def _connect(orchestra_dir):
    return orchestra_db.get_connection(_db_path(orchestra_dir))


def update_registry_agent(orchestra_dir, agent_id, fields, full_record=None) -> bool:
    """Registry config/status write (rotate_agent, park-idle :615,
    reconcile :204, session-index). When ``full_record`` is given (DP-A2) the FULL
    agent document is persisted to the live document store in the same txn as the
    typed write. Returns True iff handled against the DB."""
    freeze.barrier(orchestra_dir)   # quiesce during the cutover migrate window
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        shims.registry_update(conn, agent_id, fields, full_record=full_record)
    finally:
        conn.close()
    return True


def update_session(orchestra_dir, agent_id, fields, full_record=None) -> bool:
    """Session write (promote_successor :1590, park-idle :620, reconcile :206,
    spinup sessions). Routes a session_id change through the same-txn take-over.
    ``full_record`` (DP-A2) persists the FULL session document atomically."""
    freeze.barrier(orchestra_dir)   # quiesce during the cutover migrate window
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        shims.sessions_update(conn, agent_id, fields, full_record=full_record)
    finally:
        conn.close()
    return True


def finalize_archive(orchestra_dir, root, generation, resume_command) -> bool:
    """Item (b), gm msg_b1429f36: finalize a swap-retired predecessor archive DB-first.
    The swap (execute_swap) stamps the blue generation's ``retired_at`` but does NOT
    carry its resume_command onto the typed row nor flip its runtime_state — so the
    archive looked non-resumable (resume_command NULL) and still 'online' in
    runtime_state. This writes BOTH, keyed to the archived NON-canonical (root,
    generation) — the one generation existing writers can't reach (update_session /
    write_agent_state target the CANONICAL generation, now the green successor).
    Idempotent + no-op when the row is absent. Returns True iff handled (cutover on)."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        row = conn.execute(
            "SELECT id FROM generations WHERE root=? AND generation=?",
            (root, generation)).fetchone()
        if row is None:
            return True  # nothing to finalize (idempotent)
        gid = row["id"]
        conn.execute("BEGIN IMMEDIATE")
        try:
            if resume_command:
                conn.execute("UPDATE generations SET resume_command=? WHERE id=?",
                             (resume_command, gid))
            # flip the archived gen off 'online' -> 'parked' (never leave a retired
            # generation claiming a live runtime_state). Only touch an EXISTING row.
            conn.execute("UPDATE runtime_state SET status='parked' WHERE generation_id=?",
                         (gid,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return True


def retire_agent(orchestra_dir, agent_id, reason=None, session_record=None) -> bool:
    """park-idle retire in DB terms, faithful to the legacy shape — ALL in one txn:
      * drop the canonical pointer (agent leaves the active roster — the legacy
        registry pop = the resurrection gate) while PRESERVING the generation row
        with retired_at set (resumable);
      * REMOVE the registry source document (park-idle pops registry.agents), so a
        rollback reproject cannot resurrect it as active;
      * PERSIST the retired session document (``session_record`` — park-idle KEEPS
        agent-sessions with status=retired + resume_command + session_id, DP-A2), so
        the retired-but-resumable state survives cutover.
    Returns True iff handled."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        row = conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                           (agent_id,)).fetchone()
        conn.execute("BEGIN IMMEDIATE")
        try:
            if row is not None:
                conn.execute("UPDATE generations SET retired_at=?, note=? WHERE id=?",
                             (orchestra_db._utcnow(), reason, row["generation_id"]))
                conn.execute("DELETE FROM canonical WHERE root=?", (agent_id,))
            shims.remove_document(conn, "registry.json", "agent", agent_id)
            if session_record is not None:
                shims.upsert_document(conn, "agent-sessions.json", "session",
                                      agent_id, session_record)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return True


class SwapPreconditionError(Exception):
    """Under cutover, a manual rotation (promote_successor / rotate_agent) hit a
    root whose lineage/canonical rows are absent from the store. FAIL-CLOSED:
    refuse rather than write identity around the store (the splinter we abolish).
    Resolve the missing rows (migration import / spawn adopt) and retry."""


def swap_generation(orchestra_dir, root, green, blue_generation_id=None,
                    now=None, documents=None, sync_effects_owner=False) -> bool:
    """THE in-house swap seam for the swap-path (promote_successor / rotate_agent).
    Wraps orchestra_db.execute_swap so the swap activates through the SAME cutover
    flag + freeze barrier as every other write. When ``blue_generation_id`` is
    None it is resolved from the current canonical pointer. FAIL-CLOSED (raise
    SwapPreconditionError) if, under cutover, the root has no lineage/canonical
    rows — never fall back to a direct-JSON write. ``documents`` (DP-A2) is a list
    of ``(file, kind, key, record)`` the promote would have written (successor doc
    under root + predecessor ``<root>-gen<N>`` archive + sessions/state), persisted
    IN the swap txn so the fail-closed path writes NOTHING. ``sync_effects_owner``
    (F1): the SYNC caller (promote/rotate) runs its own effects choreography
    synchronously, so its swap is recorded TERMINAL ('complete'). DEFAULT False keeps
    the async (r-a-b) resume-driver contract — it labels 'effects-incomplete' and marks
    complete via run_post_commit_effects. Returns True iff handled against the DB, False
    when the cutover is not active (caller does its legacy write). See SEAM.md."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        if conn.execute("SELECT 1 FROM lineages WHERE root=?",
                        (root,)).fetchone() is None:
            raise SwapPreconditionError(
                f"cutover active but no lineage row for {root!r} — refusing to "
                f"write identity around the store")
        if blue_generation_id is None:
            crow = conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                                (root,)).fetchone()
            if crow is None:
                raise SwapPreconditionError(
                    f"cutover active but no canonical row for {root!r}")
            blue_generation_id = crow["generation_id"]
        orchestra_db.execute_swap(conn, root, green, blue_generation_id, now=now,
                                  documents=documents,
                                  sync_effects_owner=sync_effects_owner,
                                  blue_resume_builder=_blue_resume_builder())
    finally:
        conn.close()
    return True


def project_now(orchestra_dir) -> bool:
    """F3 read-your-writes seam (gm ruling msg_2e109091): SYNCHRONOUSLY run
    ``project_faithful`` so an identity write is readable by a LEGACY JSON
    reader IMMEDIATELY — before the projector daemon's debounce window closes.
    Call after any identity write that a spawn/exec will read next
    (rotate_agent register->spawn; r-a-b async arm prewarm — the same race on
    the async path). FAIL-CLOSED contract: any projection failure RAISES so the
    caller ABORTS before spawning into an unprojected state. Flag-off => INERT
    no-op (False)."""
    from . import projector  # lazy: keeps the flag-off import surface minimal
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        projector.project_faithful(conn, orchestra_dir)
    finally:
        conn.close()
    return True


def register_provisional(orchestra_dir, root, generation, model=None,
                         now=None) -> bool:
    """rotate_agent Step-3 successor registration in DB terms: insert a PROVISIONAL
    generation — a NON-canonical, live ``generations`` row (canonical stays on the
    predecessor; session_id NULL until attributed) — so ``project_faithful`` emits
    the ``<root>-g<N>`` alias (Part B) instead of a direct-JSON registry write.
    FAIL-CLOSED (SwapPreconditionError) if the lineage is absent — never register
    identity around the store (r-a-b precondition). Idempotent: an existing
    ``(root, generation)`` is a no-op. Returns True iff handled."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        if conn.execute("SELECT 1 FROM lineages WHERE root=?",
                        (root,)).fetchone() is None:
            raise SwapPreconditionError(
                f"cutover active but no lineage row for {root!r} — refusing to "
                f"register a provisional successor around the store")
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT id FROM generations WHERE root=? AND generation=?",
                (root, generation)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO generations (root, generation, session_id, model, "
                    "spawned_at) VALUES (?, ?, NULL, ?, ?)",
                    (root, generation, model or "unknown",
                     now or orchestra_db._utcnow()))
            elif model:
                # UPSERT the model on a RE-REGISTERED successor slot (gm msg_92f7ef2d):
                # a reused (root, generation) row would otherwise keep its STALE model —
                # the fable-DOA class where a successor spawns on a credit-walled model
                # the lineage no longer runs. Stamp the CURRENT model so every rotation
                # spawns the successor on the canonical's actual model. GUARD: never
                # re-stamp the CANONICAL generation's row (only a non-canonical
                # provisional/successor slot); session_id/spawned_at untouched.
                canon = conn.execute(
                    "SELECT generation_id FROM canonical WHERE root=?", (root,)).fetchone()
                if canon is None or canon[0] != existing[0]:
                    conn.execute("UPDATE generations SET model=? WHERE id=?",
                                 (model, existing[0]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return True


def prune_provisional(orchestra_dir, root, generation) -> bool:
    """rotate_agent hold/abort cleanup in DB terms: delete the PROVISIONAL
    (non-canonical, live) generation row so the ``<root>-g<N>`` alias disappears.
    GUARDED — refuses to delete a generation that is canonical or already retired
    (only a live provisional row is prunable), so a promoted/archived identity is
    never destroyed by cleanup. Idempotent: a missing row is a no-op. Returns True
    iff handled (so the caller skips its legacy JSON prune)."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        row = conn.execute(
            "SELECT id, retired_at FROM generations WHERE root=? AND generation=?",
            (root, generation)).fetchone()
        if row is None:
            return True  # nothing to prune (idempotent)
        gid = row["id"]
        is_canonical = conn.execute(
            "SELECT 1 FROM canonical WHERE generation_id=?", (gid,)).fetchone()
        if is_canonical is not None or row["retired_at"] is not None:
            return True  # not a live provisional row — refuse to prune (safe no-op)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM generations WHERE id=?", (gid,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return True


def respawn_retired_canonical(orchestra_dir, root, model, now=None, session_id=None):
    """gm msg_f145ea51 (2026-09-16): a RESPAWN of a root whose CANONICAL generation is already
    retired (reconciler retired it: no process / no resume) must mint the NEXT generation and
    repoint canonical to it — in the DB, before any flat write — instead of coming up as a live
    pane behind a retired pointer (task-gm: flat online, canonical -> retired gen 1, guard
    test_no_canonical_row_points_at_a_retired_generation red every tick).
    ``session_id`` (gm msg_e9a921fe ruling (2), 2026-09-16): a RESUME through spawn-agent.sh
    --resume <sid> knows its sid at adopt time — write it on the minted generation so the
    resumed seat has canonical + sid at once, not after a spawn_attribute_sid poll.
    Returns the new generation number, or None when the canonical generation is live (nothing
    to do). FAIL-CLOSED: SwapPreconditionError when the root has no lineage/canonical row;
    ValueError when ``model`` is empty/'unknown' (never mint a partial identity). Inert (None)
    when the cutover is inactive."""
    freeze.barrier(orchestra_dir)
    if not cutover.is_active(orchestra_dir):
        return None
    if not model or str(model).strip().lower() == "unknown":
        raise ValueError(f"respawn of {root!r}: a real model is required to mint a generation")
    now = now or orchestra_db._utcnow()
    conn = _connect(orchestra_dir)
    try:
        if conn.execute("SELECT 1 FROM lineages WHERE root=?", (root,)).fetchone() is None:
            raise SwapPreconditionError(f"cutover active but no lineage row for {root!r}")
        crow = conn.execute(
            "SELECT cn.generation_id, g.retired_at FROM canonical cn "
            "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?", (root,)).fetchone()
        if crow is None:
            raise SwapPreconditionError(f"cutover active but no canonical row for {root!r}")
        if crow["retired_at"] is None:
            return None
        conn.execute("BEGIN IMMEDIATE")
        try:
            nxt = (conn.execute("SELECT MAX(generation) FROM generations WHERE root=?",
                                (root,)).fetchone()[0] or 0) + 1
            cur = conn.execute(
                "INSERT INTO generations (root, generation, session_id, model, spawned_at, "
                "spawned_by, promoted_at, promoted_by, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (root, nxt, session_id or None, model, now, "spawn_adopt", now, "spawn_adopt",
                 f"respawn: canonical generation {crow['generation_id']} was retired"
                 + (" (resume of a known sid)" if session_id else "")))
            gid = cur.lastrowid
            conn.execute("UPDATE canonical SET generation_id=?, status='online' WHERE root=?",
                         (gid, root))
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state (generation_id, status, last_updated) "
                "VALUES (?, 'online', ?)", (gid, now))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return nxt
    finally:
        conn.close()


def adopt_identity(orchestra_dir, ident, now=None) -> bool:
    """U16 ADOPT seam for the SANCTIONED identity-establishing spawn path
    (spinup-orchestra-builder-v2.sh :44/:52). The spawn mints a COMPLETE identity
    (root/generation/model/tier/runtime), so under cutover it ADOPTS that identity
    into the store (its own txn) instead of writing registry.json/agent-sessions.json
    directly. FAIL-CLOSED: orchestra_db.adopt_identity RAISES IdentityAdoptionError on
    a missing _REQUIRED_ADOPT field — never fabricate a partial identity (DEC-1788346974
    (b)). Returns True iff handled; False when the cutover is inactive (caller does its
    byte-identical legacy json.dump)."""
    freeze.barrier(orchestra_dir)   # quiesce during the cutover migrate window
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        orchestra_db.adopt_identity(conn, ident, now=now)
    finally:
        conn.close()
    return True


def write_agent_state(orchestra_dir, agent_id, state_fields, full_record=None) -> bool:
    """Per-agent operational state write (state-snapshot-agents.sh, reconcile
    :173/:199). Updates the canonical generation's runtime_state row. When
    ``full_record`` is given (DP-A2) the FULL free-form state blob is persisted to
    the live document store (this file is almost entirely mutable-unmodeled, so
    serving it whole is the only faithful option) atomically with the typed write."""
    freeze.barrier(orchestra_dir)   # quiesce during the cutover migrate window
    if not cutover.is_active(orchestra_dir):
        return False
    conn = _connect(orchestra_dir)
    try:
        row = conn.execute(
            "SELECT generation_id FROM canonical WHERE root=?",
            (agent_id,)).fetchone()
        if row is None:
            return True  # active but unknown agent: nothing to update (no-op)
        gid = row["generation_id"]
        cols = {k: v for k, v in state_fields.items()
                if k in ("status", "current_task", "last_updated", "last_active")}
        if cols or full_record is not None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if cols:
                    set_clause = ", ".join(f"{k}=?" for k in cols)
                    conn.execute(
                        f"UPDATE runtime_state SET {set_clause} WHERE generation_id=?",
                        (*cols.values(), gid))
                if full_record is not None:
                    shims.upsert_document(conn, "state/agents", "state_agent",
                                          f"{agent_id}.json", full_record)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()
    return True
