"""thread_store.py — G20: an Arturo conversation is a durable, server-side object.

Before this, the Ask-Arturo pill kept ONE conversation in localStorage
(`orchestra.arturo.pill.thread` / `.conversation`) and the home kept its own
(`orchestra.arturo.conversation`). "New thread" minted a fresh conversation id and the
previous thread became unreachable: the turns existed server-side, but nothing listed them.

So the archive belongs next to the service, not in a browser:
  * the phone and the web then show the SAME threads (one thread space, two surfaces);
  * a thread survives a service restart and a browser reload;
  * continuing an old thread can re-thread its context, because `history()` rehydrates the
    in-memory turn history the brain is given.

Storage is sqlite next to the other Arturo state. Two tables, because a thread's summary
(title, updated, turn count, last snippet) is read far more often than its turns: the list
view never loads a transcript.

Degraded, never fatal: a turn must not fail because the archive is unwritable. A store that
cannot open its file reports `usable = False` and answers empty for every read, rather than
raising into the operator's turn or silently pretending a thread was saved.
"""
import logging
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("arturo.thread_store")

TITLE_MAX = 80
SNIPPET_MAX = 160
DEFAULT_HISTORY_TURNS = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL DEFAULT '',
    created  REAL NOT NULL,
    updated  REAL NOT NULL,
    turns    INTEGER NOT NULL DEFAULT 0,
    snippet  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS turns (
    thread_id TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    ts        REAL NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_threads_updated ON threads(updated DESC);
CREATE TABLE IF NOT EXISTS turn_marks (
    thread_id TEXT NOT NULL,
    turn_id   TEXT NOT NULL,
    owner     TEXT NOT NULL,
    principal TEXT NOT NULL DEFAULT '',
    state     TEXT NOT NULL,
    boot      TEXT NOT NULL DEFAULT '',
    reply_seq INTEGER,
    ts        REAL NOT NULL,
    PRIMARY KEY (thread_id, turn_id)
);
CREATE INDEX IF NOT EXISTS idx_turn_marks_ts ON turn_marks(ts);
"""

# A turn mark outlives any client still waiting on its turn by far; older ones are pruned.
TURN_MARK_TTL_S = 7 * 24 * 3600

# Columns added after the tables first shipped. `CREATE TABLE IF NOT EXISTS` never alters an
# existing threads.db, so each is added by an idempotent ALTER on open. '' = the default brain.
_MIGRATIONS = {
    "threads": [("last_brain_provider", "TEXT NOT NULL DEFAULT ''"),
                ("last_brain_model", "TEXT NOT NULL DEFAULT ''"),
                # Who started the thread (the gateway-stamped principal of its first turn). '' = a row from
                # before this column, which only the dashboard could have written: read as "fleet".
                ("started_by", "TEXT NOT NULL DEFAULT ''")],
    "turns": [("brain_provider", "TEXT NOT NULL DEFAULT ''"),
              ("brain_model", "TEXT NOT NULL DEFAULT ''")],
}
_THREAD_COLS = "id, title, created, updated, turns, snippet, last_brain_provider, last_brain_model"

# A turn with no stamped principal still starts a thread someone may continue; never "" (that is "fleet").
ANONYMOUS = "anonymous"


def _migrate(conn):
    for table, cols in _MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _brain(provider, model):
    """A stored brain pair as the API shape: None for the default brain."""
    return {"provider": provider, "model": model or ""} if provider else None


def _thread_row(r):
    out = dict(r)
    out["last_brain"] = _brain(out.pop("last_brain_provider"), out.pop("last_brain_model"))
    return out


# The proxy's page-context line (arturo-proxy.py _context_line), first line of a stored user turn.
# Kept in storage (the model saw it); stripped from everything a client reads. A stored turn has it
# on its own line; a title made by older code had it collapsed onto the same line.
_CONTEXT_LINE = re.compile(r"^\[Context: route=[^\n]*\]\n")
_CONTEXT_TITLE = re.compile(r"^\[Context: route=[^\]\n]*\] ")
# The page's hidden opener (services/arturo/onboarding.py OPENER_SENTINEL): never a title.
_OPENER = "(first run:"


def strip_context_line(text):
    text = text or ""
    return _CONTEXT_TITLE.sub("", _CONTEXT_LINE.sub("", text, count=1), count=1)


def _is_opener(text):
    return strip_context_line(text).strip().startswith(_OPENER)


def derive_title(text, limit=TITLE_MAX):
    """A thread's title is its FIRST user message, trimmed to one line — the operator is
    never asked to name a thread (G20 design). Truncation is on a word boundary when one is
    close enough to the limit, so a title reads as a phrase rather than a cut word. The page's
    opener is not the operator's words: it titles nothing (""), and the first real message does."""
    if _is_opener(text):
        return ""
    one_line = " ".join(strip_context_line(text).split())
    if len(one_line) <= limit:
        return one_line
    cut = one_line[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip()


class ThreadStore:
    """Durable per-conversation turn archive. Safe to construct per call: it holds no
    connection between operations, so a restart (or a second process) sees the same rows."""

    def __init__(self, path):
        self.path = Path(path)
        self.usable = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                _migrate(conn)
            self.usable = True
        except Exception:  # noqa: BLE001 — unwritable archive must not break a turn
            self.usable = False

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # --- write ---------------------------------------------------------------------

    def record_turn(self, conversation_id, user_text, assistant_text, ts=None, brain=None,
                    effective=None, turn_id=None, principal=None):
        """Archive one completed turn (the user's message and Arturo's reply). Called AFTER
        the brain answers, so a failed turn leaves no half-thread. Never raises.
        `brain` = {provider, model} when the operator chose one for this turn, None = default —
        that is what `last_brain` keeps, and what the picker restores. `effective` = the brain
        that actually WROTE the reply (a default turn names its model): that is what the turn
        row keeps, so a later turn on another model can be told which replies were not its own.
        Omitted, the turn row keeps `brain`, as before. `principal` = who sent the turn; the first
        turn's is kept as the thread's `started_by` (see started_by())."""
        if not self.usable or not conversation_id:
            return False
        now = float(ts if ts is not None else time.time())
        bp, bm = ((brain or {}).get("provider") or "", (brain or {}).get("model") or "")
        wrote = effective if effective is not None else brain
        tp, tm = ((wrote or {}).get("provider") or "", (wrote or {}).get("model") or "")
        conn = None
        try:
            conn = self._connect()
            # One write transaction from the count to the insert: two turns on one thread at once
            # (two tabs, a phone and a laptop) must not both take the same seq and overwrite.
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                row = conn.execute("SELECT turns, title FROM threads WHERE id = ?",
                                   (conversation_id,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO threads (id, title, created, updated, turns, snippet, started_by)"
                        " VALUES (?, ?, ?, ?, 0, '', ?)",
                        (conversation_id, derive_title(user_text), now, now, principal or ANONYMOUS))
                    seq = 0
                else:
                    seq = int(row["turns"])
                    if not row["title"] and derive_title(user_text):
                        conn.execute("UPDATE threads SET title = ? WHERE id = ?",
                                     (derive_title(user_text), conversation_id))
                conn.executemany(
                    "INSERT OR REPLACE INTO turns (thread_id, seq, role, content, ts, brain_provider, brain_model)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(conversation_id, seq, "user", user_text or "", now, "", ""),
                     (conversation_id, seq + 1, "assistant", assistant_text or "", now, tp, tm)])
                conn.execute(
                    "UPDATE threads SET updated = ?, turns = ?, snippet = ?,"
                    " last_brain_provider = ?, last_brain_model = ? WHERE id = ?",
                    (now, seq + 2, (assistant_text or "")[:SNIPPET_MAX], bp, bm, conversation_id))
                if turn_id:
                    # In the same transaction as the turn: a crash between the two can never show a
                    # recorded reply as a turn that started and was lost (DEC-1791518421640932 v5).
                    conn.execute("UPDATE turn_marks SET state = 'recorded', reply_seq = ?"
                                 " WHERE thread_id = ? AND turn_id = ?", (seq + 1, conversation_id, turn_id))
            return True
        except Exception as e:  # noqa: BLE001 — the archive is never worth failing a turn over,
            # but a lost turn is never silent either
            log.warning(f"thread store: could not record a turn for {conversation_id}: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()

    # --- turn marks (DEC-1791518421640932) -------------------------------------------
    # One row per client-minted turn id: 'started' once the turn holds its conversation's lock and before
    # anything runs, 'recorded' with the turn's own reply, deleted when the turn ended having done nothing
    # (so a resend may run it). These RAISE: a caller that cannot mark a turn must not run it.

    def mark_started(self, conversation_id, turn_id, owner, boot, principal=None):
        if not self.usable:
            raise RuntimeError("thread store unusable")
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM turn_marks WHERE ts < ?", (now - TURN_MARK_TTL_S,))
            conn.execute("INSERT INTO turn_marks (thread_id, turn_id, owner, principal, state, boot, ts)"
                         " VALUES (?, ?, ?, ?, 'started', ?, ?)",
                         (conversation_id, turn_id, owner, principal or "", boot, now))

    def get_mark(self, conversation_id, turn_id):
        """The mark as a dict (with `reply` once recorded), or None."""
        if not self.usable:
            raise RuntimeError("thread store unusable")
        with self._connect() as conn:
            row = conn.execute("SELECT owner, principal, state, boot, reply_seq FROM turn_marks"
                               " WHERE thread_id = ? AND turn_id = ?", (conversation_id, turn_id)).fetchone()
            if row is None:
                return None
            out = {"owner": row["owner"], "principal": row["principal"] or None, "state": row["state"],
                   "boot": row["boot"]}
            if row["state"] == "recorded" and row["reply_seq"] is not None:
                r = conn.execute("SELECT content FROM turns WHERE thread_id = ? AND seq = ?",
                                 (conversation_id, row["reply_seq"])).fetchone()
                out["reply"] = r["content"] if r else ""
            return out

    def clear_mark(self, conversation_id, turn_id):
        """Only a 'started' mark: a recorded turn keeps its mark for good."""
        if not self.usable:
            return
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM turn_marks WHERE thread_id = ? AND turn_id = ? AND state = 'started'",
                             (conversation_id, turn_id))
        except Exception as e:  # noqa: BLE001 — a mark left behind reads as lost: safe, never a re-run
            log.warning(f"thread store: could not clear a turn mark for {conversation_id}: {e}")

    # --- read ----------------------------------------------------------------------

    def started_by(self, conversation_id):
        """Who started this thread: a principal ("fleet", "device:<id>", ANONYMOUS), or None when the
        archive does not hold it (not started yet, or an unusable archive, which holds no history either).
        A row from before the column reads as "fleet". A read that fails reads as "fleet" too: whoever is
        not the dashboard is then refused, never let in."""
        if not conversation_id or not self.usable:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT started_by FROM threads WHERE id = ?", (conversation_id,)).fetchone()
            return None if row is None else (row["started_by"] or "fleet")
        except Exception:  # noqa: BLE001
            return "fleet"

    def list_threads(self, limit=50, offset=0, started_by=None):
        """Newest first, paged. Summaries only — never the turns. `started_by` = only the threads that
        principal started (a non-fleet reader's list); None = all of them."""
        if not self.usable:
            return []
        try:
            with self._connect() as conn:
                where, args = ("", ()) if started_by is None else (" WHERE started_by = ?", (started_by,))
                rows = conn.execute(
                    f"SELECT {_THREAD_COLS} FROM threads{where}"
                    " ORDER BY updated DESC, id DESC LIMIT ? OFFSET ?",
                    (*args, max(1, min(int(limit), 200)), max(0, int(offset)))).fetchall()
                return [self._clean_title(conn, _thread_row(r)) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def get_thread(self, conversation_id):
        """One thread with its turns in order, or None when it does not exist."""
        if not self.usable or not conversation_id:
            return None
        try:
            with self._connect() as conn:
                head = conn.execute(
                    f"SELECT {_THREAD_COLS} FROM threads WHERE id = ?", (conversation_id,)).fetchone()
                if head is None:
                    return None
                rows = conn.execute(
                    "SELECT role, content, ts, brain_provider, brain_model FROM turns"
                    " WHERE thread_id = ? ORDER BY seq", (conversation_id,)).fetchall()
                out = self._clean_title(conn, _thread_row(head))
            # What a client reads: the page-context line is the model's, not the operator's words.
            out["turns"] = [{"role": r["role"],
                             "content": strip_context_line(r["content"]) if r["role"] == "user" else r["content"],
                             "ts": r["ts"],
                             "brain": _brain(r["brain_provider"], r["brain_model"])} for r in rows]
            return out
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _clean_title(conn, out):
        """A title as a client reads it. Rows from before the opener/context fix may carry the
        page's opener or a context line; read them as the first real operator message."""
        title = out.get("title") or ""
        if title and not _is_opener(title):
            out["title"] = strip_context_line(title)
            return out
        out["title"] = ""
        for r in conn.execute("SELECT content FROM turns WHERE thread_id = ? AND role = 'user' ORDER BY seq",
                              (out["id"],)):
            t = derive_title(r["content"])
            if t:
                out["title"] = t
                break
        return out

    def has_opener(self, conversation_id):
        """True when this thread holds the page's first-run opener: it is the onboarding thread,
        and a later opener on it is the operator coming BACK, not arriving."""
        if not self.usable or not conversation_id:
            return False
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT content FROM turns WHERE thread_id = ? AND role = 'user'"
                                    " ORDER BY seq LIMIT 50", (conversation_id,)).fetchall()
            return any(_is_opener(r["content"]) for r in rows)
        except Exception:  # noqa: BLE001
            return False

    def history(self, conversation_id, max_turns=DEFAULT_HISTORY_TURNS):
        """The last `max_turns` messages as role/content pairs — the shape the brain takes.
        This is what lets an OLD thread be continued after a restart: the in-memory history
        is empty, so the archive rehydrates it instead of answering with no context."""
        if not self.usable or not conversation_id:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT role, content, brain_provider, brain_model FROM turns"
                    " WHERE thread_id = ? ORDER BY seq DESC LIMIT ?",
                    (conversation_id, max(0, int(max_turns)))).fetchall()
            out = []
            for r in reversed(rows):
                turn = {"role": r["role"], "content": r["content"]}
                wrote = _brain(r["brain_provider"], r["brain_model"])
                if wrote:
                    turn["brain"] = wrote
                out.append(turn)
            return out
        except Exception:  # noqa: BLE001
            return []

    def turn_count(self, conversation_id):
        """How many messages this thread has ever had — the conversation's VERSION. It only
        grows, and it survives restarts, so a warm CLI session stamped with it can tell that
        the conversation moved on without it. 0 when unknown."""
        if not self.usable or not conversation_id:
            return 0
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT turns FROM threads WHERE id = ?",
                                   (conversation_id,)).fetchone()
            return int(row["turns"]) if row else 0
        except Exception:  # noqa: BLE001
            return 0
