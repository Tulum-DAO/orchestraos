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
import sqlite3
import time
from pathlib import Path

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
"""


def derive_title(text, limit=TITLE_MAX):
    """A thread's title is its FIRST user message, trimmed to one line — the operator is
    never asked to name a thread (G20 design). Truncation is on a word boundary when one is
    close enough to the limit, so a title reads as a phrase rather than a cut word."""
    one_line = " ".join((text or "").split())
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
            self.usable = True
        except Exception:  # noqa: BLE001 — unwritable archive must not break a turn
            self.usable = False

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # --- write ---------------------------------------------------------------------

    def record_turn(self, conversation_id, user_text, assistant_text, ts=None):
        """Archive one completed turn (the user's message and Arturo's reply). Called AFTER
        the brain answers, so a failed turn leaves no half-thread. Never raises."""
        if not self.usable or not conversation_id:
            return False
        now = float(ts if ts is not None else time.time())
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT turns FROM threads WHERE id = ?",
                                   (conversation_id,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO threads (id, title, created, updated, turns, snippet)"
                        " VALUES (?, ?, ?, ?, 0, '')",
                        (conversation_id, derive_title(user_text), now, now))
                    seq = 0
                else:
                    seq = int(row["turns"])
                conn.executemany(
                    "INSERT OR REPLACE INTO turns (thread_id, seq, role, content, ts)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [(conversation_id, seq, "user", user_text or "", now),
                     (conversation_id, seq + 1, "assistant", assistant_text or "", now)])
                conn.execute(
                    "UPDATE threads SET updated = ?, turns = ?, snippet = ? WHERE id = ?",
                    (now, seq + 2, (assistant_text or "")[:SNIPPET_MAX], conversation_id))
            return True
        except Exception:  # noqa: BLE001 — the archive is never worth failing a turn over
            return False

    # --- read ----------------------------------------------------------------------

    def list_threads(self, limit=50, offset=0):
        """Newest first, paged. Summaries only — never the turns."""
        if not self.usable:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, title, created, updated, turns, snippet FROM threads"
                    " ORDER BY updated DESC, id DESC LIMIT ? OFFSET ?",
                    (max(1, min(int(limit), 200)), max(0, int(offset)))).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def get_thread(self, conversation_id):
        """One thread with its turns in order, or None when it does not exist."""
        if not self.usable or not conversation_id:
            return None
        try:
            with self._connect() as conn:
                head = conn.execute(
                    "SELECT id, title, created, updated, turns, snippet FROM threads"
                    " WHERE id = ?", (conversation_id,)).fetchone()
                if head is None:
                    return None
                rows = conn.execute(
                    "SELECT role, content, ts FROM turns WHERE thread_id = ? ORDER BY seq",
                    (conversation_id,)).fetchall()
            out = dict(head)
            out["turns"] = [dict(r) for r in rows]
            return out
        except Exception:  # noqa: BLE001
            return None

    def history(self, conversation_id, max_turns=DEFAULT_HISTORY_TURNS):
        """The last `max_turns` messages as role/content pairs — the shape the brain takes.
        This is what lets an OLD thread be continued after a restart: the in-memory history
        is empty, so the archive rehydrates it instead of answering with no context."""
        if not self.usable or not conversation_id:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT role, content FROM turns WHERE thread_id = ?"
                    " ORDER BY seq DESC LIMIT ?",
                    (conversation_id, max(0, int(max_turns)))).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        except Exception:  # noqa: BLE001
            return []
