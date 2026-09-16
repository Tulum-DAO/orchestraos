"""Unified transactional identity store — schema, factory, and swap (piece-1/2).

The identity substrate (registry.json + agent-sessions.json + state/agents/*.json)
collapses into ONE ACID SQLite DB, ``state/orchestra-registry.db``. Pieces so far:

* piece-1 — schema DDL + mandatory connection factory.
* piece-2 — the §2 single-transaction swap + reconciler sid attribution +
  post-commit effect labeling. Still NO projector (piece-3), migrator, or
  live-store writes.

What is load-bearing here:

* **Schema** makes the identity-splinter incident classes UNREPRESENTABLE — a
  minimal/partial row (NOT NULL), a same-id corpse (UNIQUE session_id, PRIMARY
  KEY canonical.root, UNIQUE(root, generation)), and an orphaned identity
  (FOREIGN KEY) cannot be written. See ``test_schema_unrepresentable`` (U7).

* **Factory** (``get_connection``) — ``foreign_keys`` and ``busy_timeout`` are
  PER-CONNECTION pragmas, off/zero by default on a raw ``sqlite3.connect``.
  Every shim/daemon/CLI MUST connect through this one factory so FK enforcement
  and lock-waiting are guaranteed. See ``test_connection_factory`` (U13).

* **Swap** (``execute_swap``) — a single ``BEGIN IMMEDIATE`` transaction moves
  the canonical pointer Blue→Green; there is no torn state a reader can observe
  and a crash before COMMIT rolls back to fully-Blue with zero repair. Green's
  ``runtime_state`` is bootstrapped inside the same txn (U14) and a stale sid
  holder is cleared inside it (U8). Identity is coherent from COMMIT onward;
  external effects (tmux rename, Blue reap) are labeled ``effects-incomplete``
  and re-run idempotently, never reconciling identity (U9).
"""
import datetime
import json
import sqlite3

# busy_timeout in milliseconds — a contended writer waits this long for the
# lock instead of failing immediately with "database is locked".
BUSY_TIMEOUT_MS = 10000

SCHEMA = """
CREATE TABLE IF NOT EXISTS lineages (      -- one row per canonical seat/lineage
  root         TEXT PRIMARY KEY,           -- 'orchestra-builder'
  tier         TEXT NOT NULL,              -- T0/T1/T2
  runtime      TEXT NOT NULL,              -- claude/codex/gemini
  reports_to   TEXT,
  always_on    INTEGER NOT NULL DEFAULT 0,
  purpose      TEXT,
  machine      TEXT NOT NULL DEFAULT 'vps',
  cwd          TEXT
);

CREATE TABLE IF NOT EXISTS generations (   -- one row per generation, IMMUTABLE identity
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  root              TEXT NOT NULL REFERENCES lineages(root),
  generation        INTEGER NOT NULL,
  session_id        TEXT UNIQUE,           -- runtime sid; NULL until positively attributed
  conversation_path TEXT,
  model             TEXT NOT NULL,
  spawned_at        TEXT,
  spawned_by        TEXT,
  promoted_at       TEXT,
  promoted_by       TEXT,
  retired_at        TEXT,
  resume_command    TEXT,
  note              TEXT,
  UNIQUE(root, generation)
);

CREATE TABLE IF NOT EXISTS canonical (     -- THE identity pointer: exactly one per lineage
  root          TEXT PRIMARY KEY REFERENCES lineages(root),
  generation_id INTEGER NOT NULL REFERENCES generations(id),
  tmux_session  TEXT NOT NULL,             -- external-effect DESIRED state
  status        TEXT NOT NULL DEFAULT 'online'   -- online/quiescent/held
);

CREATE TABLE IF NOT EXISTS runtime_state ( -- mutable operational fields (old state/agents blobs)
  generation_id    INTEGER PRIMARY KEY REFERENCES generations(id),
  status           TEXT,
  current_task     TEXT,
  last_updated     TEXT,
  last_active      TEXT,
  tags_json        TEXT,
  memory_scope_json TEXT,
  extra_json       TEXT                    -- arrays as JSON, typed at the API boundary
);

CREATE TABLE IF NOT EXISTS swaps (         -- one row per swap; tracks post-commit effect completion
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  root                TEXT NOT NULL REFERENCES lineages(root),
  blue_generation_id  INTEGER REFERENCES generations(id),
  green_generation_id INTEGER NOT NULL REFERENCES generations(id),
  committed_at        TEXT NOT NULL,
  effects_status      TEXT NOT NULL DEFAULT 'effects-incomplete'  -- effects-incomplete | complete
);

CREATE TABLE IF NOT EXISTS source_records (  -- migration fidelity backstop (U6): faithful capture of the legacy files
  file        TEXT NOT NULL,               -- 'registry.json' | 'agent-sessions.json' | 'state/agents'
  kind        TEXT NOT NULL,               -- 'meta' | 'agent' | 'session' | 'state_agent'
  key         TEXT NOT NULL,               -- top-level key / agent id / filename
  ordinal     INTEGER NOT NULL DEFAULT 0,  -- source insertion order (byte-fidelity aid; semantic diff ignores it)
  payload_json TEXT NOT NULL,              -- the exact JSON value
  PRIMARY KEY (file, kind, key)
);
"""


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def get_connection(db_path: str) -> sqlite3.Connection:
    """The one mandatory connection factory. foreign_keys and busy_timeout are
    per-connection — a raw sqlite3.connect gets neither. Every caller in the
    fleet must route through here so FK enforcement and lock-waiting hold.

    ``isolation_level=None`` puts the connection in autocommit mode so the swap
    can drive an EXPLICIT ``BEGIN IMMEDIATE ... COMMIT`` (a write-lock-first
    transaction); without it python's sqlite3 wrapper opens its own implicit
    transactions and ``BEGIN IMMEDIATE`` would collide. Individual statements
    outside an explicit BEGIN still auto-commit, and constraint violations still
    raise at execute time."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: str) -> str:
    """Create ``db_path`` (if absent) and apply the schema through the factory.
    Idempotent — ``CREATE TABLE IF NOT EXISTS`` throughout. Returns the path."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
    return db_path


# Optional generation identity columns that a swap may carry for Green.
_GREEN_OPTIONAL_COLS = (
    "session_id", "conversation_path", "spawned_at", "spawned_by",
    "promoted_at", "promoted_by", "resume_command", "note",
)


def _clear_sid_holder(conn, sid: str, keep_generation_id=None) -> None:
    """NULL out any generation currently holding ``sid`` (other than
    ``keep_generation_id``). ``session_id`` is UNIQUE and SQLite enforces it
    immediately (not deferred), so a stale holder MUST be cleared BEFORE the sid
    is assigned to its new owner — this is the U8 trap. Caller runs it inside the
    surrounding transaction."""
    if sid is None:
        return
    if keep_generation_id is None:
        conn.execute("UPDATE generations SET session_id=NULL WHERE session_id=?",
                     (sid,))
    else:
        conn.execute(
            "UPDATE generations SET session_id=NULL "
            "WHERE session_id=? AND id!=?", (sid, keep_generation_id))


def _resolve_or_insert_green(conn, root: str, green: dict) -> int:
    """Return Green's generation id, inserting the immutable identity row if it
    does not already exist. If Green carries a sid a stale row still holds, clear
    that holder first (same-txn take-over). If Green's row ALREADY exists (a
    provisional generation registered pre-swap) attribute the carried sid to it —
    the successor's sid is only known post-spawn, so it arrives at swap time."""
    gen = green["generation"]
    existing = conn.execute(
        "SELECT id, session_id FROM generations WHERE root=? AND generation=?",
        (root, gen)).fetchone()
    if existing:
        # F2 (G7 live finding): rotate_agent registers the successor as a PROVISIONAL
        # generation at Step-3 (session_id NULL); the sid is resolved later and carried
        # in ``green``. Without attributing it here the promoted gen's TYPED session_id
        # stays None, so UNIQUE(session_id) protection + stale-holder clears never cover
        # it. Attribute in-txn with the same U8 take-over (clear a stale holder first).
        # Idempotent: a row already holding the sid is untouched.
        sid = green.get("session_id")
        if sid is not None and existing["session_id"] != sid:
            _clear_sid_holder(conn, sid, keep_generation_id=existing["id"])
            conn.execute("UPDATE generations SET session_id=? WHERE id=?",
                         (sid, existing["id"]))
        # item (b) (gm msg_9d5251c5): a PROMOTED provisional row must also mirror the
        # green's resume_command onto the DB gen row. Fresh inserts get it via
        # _GREEN_OPTIONAL_COLS, but this existing-row branch (rotate/BG provisional ->
        # promote) previously attributed only the sid, leaving generations.resume_command
        # NULL while agent-sessions.json carried it — the DB-first-vs-flat split. Mirror it.
        rc = green.get("resume_command")
        if rc:
            conn.execute("UPDATE generations SET resume_command=? WHERE id=?",
                         (rc, existing["id"]))
        return existing["id"]
    _clear_sid_holder(conn, green.get("session_id"))
    cols = ["root", "generation", "model"]
    vals = [root, gen, green["model"]]
    for k in _GREEN_OPTIONAL_COLS:
        if green.get(k) is not None:
            cols.append(k)
            vals.append(green[k])
    placeholders = ",".join("?" for _ in vals)
    cur = conn.execute(
        f"INSERT INTO generations ({','.join(cols)}) VALUES ({placeholders})",
        tuple(vals))
    return cur.lastrowid


class PromoteInvariantError(Exception):
    """Post-promote invariant violated: after the swap, canonical must point at a LIVE,
    STAMPED generation (retired_at IS NULL AND promoted_at IS NOT NULL). The reuse of a
    RETIRED phantom row at (root, pred_gen+1) previously left canonical -> a retired_at-set
    / promoted_at-null gen that every canonical-live definition reads as DEAD (ios-watch-dev
    g16->17, gm msg_0a37acf3). Raised inside execute_swap so the txn rolls back fully-Blue."""


class SwapCASError(Exception):
    """P0.2 swap-CAS: canonical did NOT still point to the expected blue generation at
    swap time (a concurrent/stale/double swap moved it). Raised inside execute_swap so
    the BEGIN IMMEDIATE txn rolls back fully-Blue — never a last-write-wins clobber of
    write-truth."""


def execute_swap(conn, root: str, green: dict, blue_generation_id=None,
                 now: str = None, _fail_after: int = None, documents=None,
                 sync_effects_owner: bool = False, blue_resume_builder=None) -> dict:
    """Swap the canonical pointer Blue→Green in ONE ``BEGIN IMMEDIATE`` txn.

    Steps (identity is fully Blue until COMMIT, fully Green after):
      1. resolve/insert Green's immutable identity (+ U8 stale-sid clear);
      2. U14 — UPSERT Green's runtime_state (status online) inside the txn;
      3. repoint canonical → Green;
      4. retire Blue;
      5. record the swap. ``sync_effects_owner`` (F1): the SYNC caller
         (promote_successor / rotate_agent) runs its OWN effects choreography
         synchronously in-process and is NOT managed by the async resume-driver, so
         its swap is recorded TERMINAL ('complete') — the resume-driver must hand off
         and monitors must not false-flag it forever. DEFAULT False keeps the async
         (r-a-b) contract: 'effects-incomplete' until run_post_commit_effects flips it;
      6. DP-A2 — upsert the promote's FULL documents (successor doc under root +
         predecessor ``<root>-gen<N>`` archive + sessions/state) so index and
         document never diverge (gm msg_23f94e56). ``documents`` is a list of
         ``(file, kind, key, record)``.

    A crash before COMMIT rolls the whole txn back to fully-Blue (documents
    included), zero repair. ``_fail_after`` is a TEST-ONLY seam: raise after
    logical step N (1..5) to prove that rollback. Returns
    ``{green_generation_id, swap_id}``.
    """
    now = now or _utcnow()

    def _maybe_fail(step: int) -> None:
        if _fail_after is not None and step >= _fail_after:
            raise RuntimeError(f"injected swap crash after step {step}")

    conn.execute("BEGIN IMMEDIATE")
    try:
        green_id = _resolve_or_insert_green(conn, root, green)
        _maybe_fail(1)

        conn.execute(
            "INSERT INTO runtime_state (generation_id, status, last_updated) "
            "VALUES (?, 'online', ?) "
            "ON CONFLICT(generation_id) DO UPDATE SET "
            "status='online', last_updated=excluded.last_updated",
            (green_id, now))
        _maybe_fail(2)

        # P0.4 (leg-(ii)): the canonical tmux binding is the green's ACTUAL execution
        # binding, carried in green["tmux_session"] (the bg path sets {root}-g{N}).
        # Default to root so promote_successor/rotate_agent (which pass no
        # tmux_session) stay byte-identical. Writing root here made canonical point at
        # blue's now-dead pane after a bg swap (the leg-(i) hand-rename stopgap).
        canonical_tmux = green.get("tmux_session") or root
        if blue_generation_id is not None:
            # P0.2 swap-CAS: repoint canonical Blue->Green ONLY if it STILL points to the
            # EXPECTED blue generation. A compare-and-swap (UPDATE ... WHERE) that touches
            # 0 rows means canonical already moved (concurrent/stale/double swap) -> RAISE
            # so the txn rolls back fully-Blue, never a last-write-wins clobber. Under a
            # single driver this is always a 1-row update; it hardens the multi-driver path
            # (MUST land before any autonomous arm).
            cur = conn.execute(
                "UPDATE canonical SET generation_id=?, tmux_session=?, status='online' "
                "WHERE root=? AND generation_id=?",
                (green_id, canonical_tmux, root, blue_generation_id))
            if cur.rowcount != 1:
                raise SwapCASError(
                    f"swap-CAS failed for {root!r}: canonical did not point to the "
                    f"expected blue generation {blue_generation_id} "
                    f"(rows_updated={cur.rowcount}) — a concurrent or stale swap moved "
                    f"canonical; aborting (txn rolls back fully-Blue)")
            # DEFECT FIX (gm msg_0a37acf3): the promote may REUSE a RETIRED phantom
            # generation row at (root, pred_gen+1) (the reuse branch of _resolve_or_insert_green
            # only re-attributes session_id), or a fresh insert that never carried promoted_at.
            # Either leaves canonical -> a retired_at-set / promoted_at-null gen that every
            # canonical-live definition (fleet guard: retired_at IS NULL) reads as DEAD. Stamp
            # the promote as LIVE in THIS same txn: un-retire + stamp promoted_at/promoted_by.
            conn.execute(
                "UPDATE generations SET retired_at=NULL, promoted_at=?, "
                "promoted_by=COALESCE(?, promoted_by) WHERE id=?",
                (now, green.get("promoted_by"), green_id))
        else:
            # No expected blue (fresh promote/adopt path) => unconditional upsert
            # (back-compat: SYNC callers that do not name a blue keep the old behavior).
            conn.execute(
                "INSERT INTO canonical (root, generation_id, tmux_session, status) "
                "VALUES (?, ?, ?, 'online') "
                "ON CONFLICT(root) DO UPDATE SET "
                "generation_id=excluded.generation_id, "
                "tmux_session=excluded.tmux_session",
                (root, green_id, canonical_tmux))
        _maybe_fail(3)

        if blue_generation_id is not None:
            conn.execute("UPDATE generations SET retired_at=? WHERE id=?",
                         (now, blue_generation_id))
            # item (a) REAP-SAFETY (gm msg_9d5251c5): promote derives resume for the
            # GREEN but never backfilled the retired BLUE — reap policy iii needs
            # resume_command in the DB before reaping a parked predecessor, else the
            # blue is stranded un-reapable. Backfill it here (only when a builder is
            # injected and the blue lacks one), keyed by the blue's own runtime + sid.
            if blue_resume_builder is not None:
                brow = conn.execute(
                    "SELECT g.session_id AS sid, g.resume_command AS rc, "
                    "l.runtime AS runtime FROM generations g "
                    "LEFT JOIN lineages l ON l.root = g.root "
                    "WHERE g.id=?", (blue_generation_id,)).fetchone()
                if brow is not None and not brow["rc"] and brow["sid"]:
                    try:
                        blue_rc = blue_resume_builder(brow["runtime"], brow["sid"])
                    except Exception:
                        blue_rc = None      # non-resumable runtime -> leave NULL, never guess
                    if blue_rc:
                        conn.execute(
                            "UPDATE generations SET resume_command=? WHERE id=?",
                            (blue_rc, blue_generation_id))

        # SHARED alias-retire (gm msg_6eda7033): the successor is spawned under a temp
        # `<root>-g<green_gen>` lineage that carries its OWN canonical row; after the swap
        # folds the green into `root`, that alias canonical row is redundant and, left
        # online, is a phantom-canonical leak. Item-A wired this only into the SYNC promote
        # caller (_db_promote_swap); doing it HERE, in the swap txn of the shared primitive,
        # covers the BG/autonomous make_swap_fn path too — neither path can miss it. DB-first:
        # drop the alias canonical pointer + stamp its generation(s) retired_at, KEEP the
        # lineage row (resumable). Guarded: never touch `root` itself; no-op if absent.
        alias_root = f"{root}-g{green.get('generation')}"
        if alias_root != root:
            arow = conn.execute("SELECT generation_id FROM canonical WHERE root=?",
                                (alias_root,)).fetchone()
            if arow is not None:
                conn.execute("UPDATE generations SET retired_at=COALESCE(retired_at,?) "
                             "WHERE id=?", (now, arow["generation_id"]))
                conn.execute("DELETE FROM canonical WHERE root=?", (alias_root,))
        _maybe_fail(4)

        swap_id = conn.execute(
            "INSERT INTO swaps (root, blue_generation_id, green_generation_id, "
            "committed_at, effects_status) VALUES (?, ?, ?, ?, ?)",
            (root, blue_generation_id, green_id, now,
             "complete" if sync_effects_owner else "effects-incomplete")).lastrowid
        _maybe_fail(5)

        for file, kind, key, record in (documents or []):
            conn.execute(
                "INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                "VALUES (?, ?, ?, 0, ?) ON CONFLICT(file, kind, key) DO UPDATE SET "
                "payload_json=excluded.payload_json",
                (file, kind, key, json.dumps(record)))

        # POST-PROMOTE INVARIANT (gm msg_0a37acf3), fail-closed LOUD: on the rotate/promote
        # path canonical MUST now point at a LIVE, STAMPED generation. If it does not, the
        # promote produced a corrupt canonical (the retired-phantom-reuse class) — RAISE so
        # the whole txn rolls back fully-Blue rather than committing a DEAD-looking canonical.
        if blue_generation_id is not None:
            chk = conn.execute(
                "SELECT g.retired_at, g.promoted_at FROM canonical c "
                "JOIN generations g ON g.id=c.generation_id WHERE c.root=?", (root,)).fetchone()
            if chk is None or chk["retired_at"] is not None or chk["promoted_at"] is None:
                raise PromoteInvariantError(
                    f"post-promote invariant violated for {root!r}: canonical -> gen "
                    f"(retired_at={chk['retired_at'] if chk else '??'}, "
                    f"promoted_at={chk['promoted_at'] if chk else '??'}) is not live+stamped")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"green_generation_id": green_id, "swap_id": swap_id}


def attribute_session_id(conn, target_generation_id: int, sid: str) -> None:
    """Positively attribute ``sid`` to a generation (fd-proof from the observer/
    reconciler). ``session_id`` is UNIQUE and enforced immediately, so if a stale
    generation row still holds ``sid`` (post-crash/resume — exactly when
    attribution matters most) a naive single UPDATE is REJECTED. This clears the
    stale holder and assigns the sid in the SAME transaction (U8)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        _clear_sid_holder(conn, sid, keep_generation_id=target_generation_id)
        conn.execute("UPDATE generations SET session_id=? WHERE id=?",
                     (sid, target_generation_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


class SidAttributionError(Exception):
    """A promote committed but the green generation's TYPED session_id is not the
    expected sid — the null/wrong-sid rotation landmine (canonical would resolve to
    the wrong transcript). Raised by verify_session_id_attributed (M3, leg-(ii))."""


def read_generation_session_id(conn, root: str, generation: int):
    """Return the TYPED session_id of the (root, generation) row, or None if the row
    is absent or its sid is NULL. Read-only; used by the M3 post-commit verify."""
    row = conn.execute(
        "SELECT session_id FROM generations WHERE root=? AND generation=?",
        (root, generation)).fetchone()
    return row["session_id"] if row else None


def verify_session_id_attributed(conn, root: str, generation: int, expected_sid) -> None:
    """DELIVERY-CRITICAL post-commit check (M3): after a swap, the green generation's
    TYPED session_id MUST equal the sid we carried in ``green``. Raises
    SidAttributionError otherwise — a swap that commits but leaves canonical's sid
    null/wrong is the rotation landmine (canonical resolves the WRONG transcript).
    No-op when ``expected_sid`` is None (nothing to attribute — e.g. the green had
    not booted/written its .sid yet)."""
    if expected_sid is None:
        return
    got = read_generation_session_id(conn, root, generation)
    if got != expected_sid:
        raise SidAttributionError(
            f"{root} g{generation}: session_id attribution FAILED — canonical "
            f"session_id={got!r} != green sid={expected_sid!r} (null/wrong-sid landmine)")


def _document_session_id(conn, root: str):
    """The sid carried in ``root``'s live document — the authoritative session doc
    (agent-sessions.json) first, then the registry agent doc as a fallback. Returns
    None if neither carries a sid."""
    for file, kind in (("agent-sessions.json", "session"), ("registry.json", "agent")):
        row = conn.execute(
            "SELECT payload_json FROM source_records WHERE file=? AND kind=? AND key=?",
            (file, kind, root)).fetchone()
        if row:
            sid = json.loads(row["payload_json"]).get("session_id")
            if sid:
                return sid
    return None


def _sid_holder(conn, sid: str):
    """The (id, root, is_protectable) of the generation currently holding ``sid``, or
    None. ``is_protectable`` = the holder is itself a VALID canonical, non-retired gen —
    a live identity the backfill must NOT disturb."""
    row = conn.execute(
        "SELECT g.id, g.root, g.retired_at, "
        "(SELECT 1 FROM canonical ca WHERE ca.generation_id = g.id) AS is_canon "
        "FROM generations g WHERE g.session_id = ?", (sid,)).fetchone()
    if row is None:
        return None
    protectable = row["is_canon"] is not None and row["retired_at"] is None
    return row["id"], row["root"], protectable


def backfill_canonical_session_ids(conn, now: str = None):
    """ONE-TIME idempotent backfill (F2 retroactive). The typed ``session_id`` column
    was NULL fleet-wide (migration captured documents faithfully but never populated the
    typed column for most canonical gens), so UNIQUE(session_id) protection was absent
    fleet-wide — including the G7-promoted live gen6. For every CANONICAL, non-retired
    generation whose typed ``session_id`` IS NULL but whose live document carries a sid,
    attribute that sid to the typed column — restoring UNIQUE protection fleet-wide.

    IDEMPOTENT + NON-DESTRUCTIVE (the key correctness property):
      * attribute ONLY when the sid is FREE (no current holder), or held by a STALE row
        (retired / non-canonical) — a legitimate U8 take-over;
      * if the sid is already held by a VALID canonical, non-retired gen (a live
        identity — e.g. a migration archive doc `<root>__retired-genN` whose registry
        payload still points at a LIVE lineage's sid), SKIP + record a CONFLICT. Never
        clear a valid holder (that corruption is what churned gemini-gm-gen5/gen7 and
        broke idempotency). A second run therefore attributes 0 (free ones are now typed;
        conflicts remain skipped; stale take-overs already done and never re-selected).

    Returns ``{"attributed": [(root, generation, sid), ...],
               "conflicts": [(root, sid, holder_root), ...],
               "skipped_no_doc_sid": N}`` for the live-run report / spot-check."""
    rows = conn.execute(
        "SELECT g.id, g.root, g.generation FROM canonical ca "
        "JOIN generations g ON ca.generation_id = g.id "
        "WHERE g.session_id IS NULL AND g.retired_at IS NULL "
        "ORDER BY g.root, g.generation").fetchall()
    attributed = []
    conflicts = []
    skipped_no_doc_sid = 0
    for r in rows:
        sid = _document_session_id(conn, r["root"])
        if not sid:
            skipped_no_doc_sid += 1
            continue
        holder = _sid_holder(conn, sid)
        if holder is not None and holder[2]:  # held by a VALID canonical non-retired gen
            conflicts.append((r["root"], sid, holder[1]))
            continue  # do NOT clear a live holder — surface the collision instead
        # free, or held only by a stale/retired/non-canonical row -> legit take-over
        attribute_session_id(conn, r["id"], sid)
        attributed.append((r["root"], r["generation"], sid))
    return {"attributed": attributed, "conflicts": conflicts,
            "skipped_no_doc_sid": skipped_no_doc_sid}


class IdentityAdoptionError(Exception):
    """Raised when an identity-establishing foreign write cannot be safely
    adopted (missing a NOT NULL field). The caller HARD-FAILS rather than
    silent-reverting the write (which would orphan a live agent)."""


_REQUIRED_ADOPT = ("root", "generation", "model", "tier", "runtime")


def adopt_identity(conn, ident: dict, now: str = None) -> int:
    """U16 ADOPT — ingest an identity-establishing foreign write into the DB via
    its OWN transaction, so an unmigrated spawn/recovery write is captured rather
    than silently reverted (which would orphan the live agent). Reuses the
    same-txn stale-sid take-over (an adopted identity may carry a sid a stale row
    still holds — ob rider). Raises IdentityAdoptionError if a required NOT NULL
    field is absent (caller then HARD-FAILS). Returns the generation id."""
    missing = [k for k in _REQUIRED_ADOPT if not ident.get(k)]
    if missing:
        raise IdentityAdoptionError(
            f"cannot adopt identity for {ident.get('root')!r}: missing {missing}")
    now = now or _utcnow()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO lineages (root, tier, runtime, machine) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(root) DO NOTHING",
            (ident["root"], ident["tier"], ident["runtime"],
             ident.get("machine") or "vps"))
        existing = conn.execute(
            "SELECT id FROM generations WHERE root=? AND generation=?",
            (ident["root"], ident["generation"])).fetchone()
        if existing:
            gid = existing["id"]
        else:
            _clear_sid_holder(conn, ident.get("session_id"))
            gid = conn.execute(
                "INSERT INTO generations (root, generation, session_id, model) "
                "VALUES (?, ?, ?, ?)",
                (ident["root"], ident["generation"], ident.get("session_id"),
                 ident["model"])).lastrowid
        conn.execute(
            "INSERT INTO runtime_state (generation_id, status, last_updated) "
            "VALUES (?, 'online', ?) "
            "ON CONFLICT(generation_id) DO UPDATE SET last_updated=excluded.last_updated",
            (gid, now))
        conn.execute(
            "INSERT INTO canonical (root, generation_id, tmux_session, status) "
            "VALUES (?, ?, ?, 'online') ON CONFLICT(root) DO NOTHING",
            (ident["root"], gid, ident["root"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return gid


def run_post_commit_effects(conn, swap_id: int, effects) -> None:
    """Run the injected post-commit effect seams (tmux rename, Blue reap, WAL
    markers) in order. Effects are NOT identity: this function NEVER writes
    canonical/generations. If any effect raises, the swap stays labeled
    ``effects-incomplete`` (a resume driver re-runs it) and identity is
    untouched — there is no identity-reconcile state, ever (U9). On full success
    the label becomes ``complete``. Effects must be idempotent (re-run safe)."""
    for fn in effects:
        fn()
    conn.execute("UPDATE swaps SET effects_status='complete' WHERE id=?",
                 (swap_id,))
