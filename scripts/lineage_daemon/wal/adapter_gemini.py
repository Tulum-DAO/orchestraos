"""gemini/agy WAL adapter — a NET-NEW sqlite-WAL POLL tailer (Build A).

gemini's live store is NOT a jsonl append stream (the stale
adapters/gemini_adapter.py -> transcript.jsonl is CONFIRMED WRONG). A0 D1 (by
effect on 491 live dbs):
  * store   = ~/.gemini/antigravity-cli/conversations/<uuid>.db
  * journal = wal
  * cursor  = MAX(idx) on `steps` (idx INTEGER PRIMARY KEY, rowid alias)
  * bodies  = PROTOBUF-wire blobs in steps.step_payload (magic 08..), plus
              native steps.status + a permissions blob (a store-side semantic
              signal for the hooks-absent providers).

Unlike the append-tail adapters, this POLLS: open read-only, select rows with
idx > cursor, advance the cursor to the new MAX(idx). The byte-offset cursor slot
(`wal_cursors.last_off`) is reused to hold the last idx.

RO-probe safety (HARD, A0 by effect — a WAL-mode reader never blocks the writer):
  * open with `file:<db>?mode=ro` URI, a small busy_timeout;
  * NEVER `immutable=1` (that assumes no writer -> stale/corrupt reads vs a LIVE
    db); read the live db;
  * never copy a bare `.db` mid-checkpoint (torn read) — we read live via the
    SQLite WAL reader, which correctly sees uncheckpointed -wal rows.

COURT / normalize discipline: step_payload is protobuf model-voice — the
contagion vector. At INDEX time it stays BY-REFERENCE (body_ref = db#idx); the
structural summary carries only the discriminator (step_type/status/size + a
permission marker), never decoded text. protobuf-decode happens ONLY in the
normalize path (normalize.py), BEFORE any scrub/flag check — never here, and
never on provider-raw bytes.

CLASSIFICATION HONESTY: the antigravity step_type enum (14/15/17/23/101/132…) is
proprietary and not carried by any A0 fixture, so a precise step_type ->
{prompt,response,tool_call} map cannot be asserted without fabrication. Every
step is indexed as `marker` with the raw step_type/status preserved in the
summary (the WAL's contract is order + integrity + reference + discriminator).
Precise semantic reclassification is a documented, deferred schema-aware
enrichment — mirroring adapter_claude's unknown-type -> marker discipline.
"""
import hashlib
import os
import sqlite3

_BUSY_TIMEOUT_MS = 250


def ro_connect(db_path):
    """Open a gemini db READ-ONLY per the A0 safety rules.

    Raises FileNotFoundError if the db is absent (mode=ro never creates it).
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    # mode=ro: never creates, never writes; NEVER immutable=1 (live writer).
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


class GeminiWalAdapter:
    runtime = "gemini"

    def __init__(self, store, lineage_root, generation):
        self._store = store
        self._lineage_root = lineage_root
        self._generation = generation

    def _sid(self, db_path):
        return os.path.splitext(os.path.basename(db_path))[0] or "?"

    def tail(self, db_path):
        """Poll new steps (idx > cursor) from the live gemini db; return #events.

        A missing db is 0 (the RO open never creates it). The cursor advances to
        the new MAX(idx), so a restart re-reads no already-indexed step."""
        cur = self._store.get_cursor(db_path)
        last_idx = int(cur["last_off"]) if cur and cur["last_off"] is not None else 0
        try:
            conn = ro_connect(db_path)
        except FileNotFoundError:
            return 0
        except sqlite3.OperationalError:
            return 0
        sid = self._sid(db_path)
        appended = 0
        max_idx = last_idx
        try:
            try:
                rows = conn.execute(
                    "SELECT idx, step_type, status, permissions, step_payload"
                    " FROM steps WHERE idx > ? ORDER BY idx", (last_idx,)
                ).fetchall()
            except sqlite3.OperationalError:
                return 0  # schema not the antigravity shape -> nothing to do
            for idx, step_type, status, permissions, payload in rows:
                body = payload if isinstance(payload, (bytes, bytearray)) else b""
                size = len(body)
                perm = bool(permissions)
                summary = (f"gemini_step type={step_type} status={status} "
                           f"({size} bytes)" + ("+perm" if perm else ""))
                integrity = hashlib.sha256(body).hexdigest()
                self._store.append(
                    ts=0.0, lineage_root=self._lineage_root,
                    generation=self._generation, sid=sid, runtime=self.runtime,
                    kind="marker", summary=summary,
                    body_ref=f"gemini:{db_path}#idx={idx}",
                    source_path=db_path, source_off=int(idx), integrity=integrity)
                appended += 1
                if idx is not None and int(idx) > max_idx:
                    max_idx = int(idx)
        finally:
            conn.close()
            # Condition B (gemini = simple finally-lift): max_idx already tracks
            # the last SUCCESSFULLY-appended idx (advanced only after the append),
            # so persisting the cursor here — even when a mid-loop append raises
            # and the lane contains it — points at the committed prefix, never
            # past a failed row. Skipping this on a raise (the old bug) left the
            # cursor at the old idx -> the committed rows re-captured next tick.
            if appended:
                self._store.set_cursor(db_path, self._lineage_root,
                                       last_off=max_idx,
                                       last_seq=self._store.max_seq())
        return appended
