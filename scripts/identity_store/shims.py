"""Writer shims — redirect legacy identity writes to the transactional store.

Post-migration the three identity files (registry.json / agent-sessions.json /
state/agents/*.json) are projector-owned READ-ONLY artifacts. Legacy writers must
not write them directly; they call these shims, which write the DB.

* ``refuse_direct_json(path)`` (U5) — the path-based guard. It REFUSES + LOGS any
  write aimed at a managed projection path (guarding by PATH so it also covers
  the embedded-python shell writers' targets — ob rider 2), and does NOT
  over-refuse adjacent files (live-roster.json, agent-state/, agent-handoffs/,
  protected-sessions.json).
* ``registry_update`` / ``sessions_update`` — the DB bodies behind the
  registry-update.py / sessions-update.py CLIs (DP-U3): they write the store,
  never JSON, and reuse the same-txn sid take-over.
"""
import json
import logging
import os

from scripts.identity_store import orchestra_db

_LOG = logging.getLogger("identity_store.shims")


def upsert_document(conn, file: str, kind: str, key: str, record) -> None:
    """DP-A2 — persist a writer's FULL record into the LIVE document store
    (``source_records``) so ``project_faithful`` serves it live rather than the
    stale migrated value. The caller runs this INSIDE the same transaction as its
    typed-column write, so the identity index and the full document never diverge
    (gm msg_23f94e56). On conflict only the payload is replaced (the ordinal — a
    byte-fidelity aid the semantic diff ignores — is preserved)."""
    conn.execute(
        "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
        "VALUES (?, ?, ?, 0, ?) ON CONFLICT(file, kind, key) DO UPDATE SET "
        "payload_json=excluded.payload_json",
        (file, kind, key, json.dumps(record)))


def remove_document(conn, file: str, kind: str, key: str) -> None:
    """Delete a live document from ``source_records`` (a record that LEFT its file
    — e.g. park-idle popping an agent from the registry). The caller runs this in
    the SAME txn as the typed change. Without it a rolled-back ``migrate.reproject``
    could resurrect the popped record; ``project_faithful`` already omits it because
    the typed pointer is gone, so this only closes the reproject/rollback path."""
    conn.execute("DELETE FROM source_records WHERE file=? AND kind=? AND key=?",
                 (file, kind, key))


class InvalidModel(Exception):
    """A model_reconcile ADDENDUM (gm-approved): an LLM seat (runtime claude/codex/gemini)
    tried to persist an empty/'unknown' model through the sanctioned session writer. The
    guard the fleet-model program relies on (zero LIVE LLM rows with an unknown model) can
    only hold if the WRITE side refuses to (re)introduce one — services write the explicit
    constant 'n/a', never 'unknown', so the guard excludes them by value."""


# model_reconcile ADDENDUM: which runtimes must carry a real model, and the unknown zoo.
_LLM_RUNTIMES = ("claude", "codex", "gemini")
_UNKNOWN_MODELS = (None, "", "unknown")


def _assert_model_for_root(conn, root: str, model) -> None:
    """Write-time gate for generations.model: a claude/codex/gemini seat may never
    persist ''/'unknown'/NULL (that is the exact class model_reconcile exists to close).
    Non-LLM runtimes (service -> 'n/a') are unconstrained here."""
    lin = conn.execute("SELECT runtime FROM lineages WHERE root=?", (root,)).fetchone()
    runtime = lin["runtime"] if lin else None
    if runtime in _LLM_RUNTIMES and model in _UNKNOWN_MODELS:
        raise InvalidModel(
            f"refusing to write model={model!r} for {root!r} (runtime {runtime!r}): an "
            f"LLM seat must carry a resolved model — resolve by effect (transcript / "
            f"cmdline / runtime meta) or leave the existing value, never persist unknown")


class DirectJsonWriteRefused(Exception):
    """A writer tried to write a managed identity projection directly instead of
    going through the DB shim."""


def is_managed_projection(path: str) -> bool:
    """True iff ``path`` targets one of the three projector-owned identity files.
    Matches by basename (registry.json / agent-sessions.json) or by the
    state/agents/ directory — NOT agent-state/ or agent-handoffs/."""
    norm = path.replace("\\", "/")
    base = os.path.basename(norm)
    if base in ("registry.json", "agent-sessions.json"):
        return True
    if "/state/agents/" in norm or norm.startswith("state/agents/"):
        return norm.endswith(".json")
    return False


def refuse_direct_json(path: str, logger=None) -> None:
    """U5 — refuse + log a direct-JSON write to a managed identity projection.
    Adjacent files pass through untouched (no over-refusal)."""
    if is_managed_projection(path):
        msg = (f"REFUSED direct-JSON write to managed identity projection "
               f"{os.path.basename(path)} ({path}) — use the DB shim")
        (logger or _LOG.warning)(msg)
        raise DirectJsonWriteRefused(msg)
    return None


_LINEAGE_FIELDS = ("tier", "runtime", "reports_to", "always_on", "purpose",
                   "machine", "cwd")


def registry_update(conn, root: str, fields: dict, now: str = None,
                    full_record=None) -> dict:
    """DB shim behind registry-update.py — write lineage config fields +
    canonical.status to the store instead of registry.json. Writes NO JSON. When
    ``full_record`` is given (DP-A2) the FULL agent document is persisted to the
    live document store in the SAME txn (index + document never diverge)."""
    updated = []
    lineage_updates = {k: v for k, v in fields.items() if k in _LINEAGE_FIELDS}
    conn.execute("BEGIN IMMEDIATE")
    try:
        if lineage_updates:
            cols = ", ".join(f"{k}=?" for k in lineage_updates)
            conn.execute(f"UPDATE lineages SET {cols} WHERE root=?",
                         (*lineage_updates.values(), root))
            updated += list(lineage_updates)
        if "status" in fields:
            # item (b): every canonical.status write goes through the closed vocabulary —
            # a non-enum value (the 15-string zoo) can never re-accrete via this path.
            from scripts.identity_store import status_vocab
            status_vocab.assert_valid(fields["status"])
            conn.execute("UPDATE canonical SET status=? WHERE root=?",
                         (fields["status"], root))
            updated.append("status")
        if full_record is not None:
            upsert_document(conn, "registry.json", "agent", root, full_record)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"root": root, "updated": updated}


def sessions_update(conn, root: str, fields: dict, now: str = None,
                   full_record=None) -> dict:
    """DB shim behind sessions-update.py — write session fields for a lineage's
    canonical generation to the store instead of agent-sessions.json. A
    session_id change routes through the same-txn stale-sid take-over. When
    ``full_record`` is given (DP-A2) the FULL session document is persisted to the
    live document store atomically with the mutable-column write."""
    row = conn.execute(
        "SELECT generation_id FROM canonical WHERE root=?", (root,)).fetchone()
    if row is None:
        raise KeyError(f"no canonical generation for root {root!r}")
    gid = row["generation_id"]
    updated = []
    if "session_id" in fields:
        # U8: sid attribution is its own atomic take-over event (kept separate).
        orchestra_db.attribute_session_id(conn, gid, fields["session_id"])
        updated.append("session_id")
    # model_reconcile ADDENDUM: 'model' is a generations column and IS mutable through this
    # sanctioned writer (it was silently dropped before — the model_reconcile no-op OB
    # found). Gate it write-time so an LLM seat can never (re)acquire an unknown model.
    if "model" in fields:
        _assert_model_for_root(conn, root, fields["model"])
    mutable = {k: v for k, v in fields.items()
               if k in ("conversation_path", "note", "resume_command", "model")}
    if mutable or full_record is not None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if mutable:
                cols = ", ".join(f"{k}=?" for k in mutable)
                conn.execute(f"UPDATE generations SET {cols} WHERE id=?",
                             (*mutable.values(), gid))
                updated += list(mutable)
            if full_record is not None:
                upsert_document(conn, "agent-sessions.json", "session", root,
                                full_record)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"root": root, "generation_id": gid, "updated": updated}
