#!/usr/bin/env python3
"""episodic_memory.py — the conversation index: every message, tool call, tool result and decision an agent's
session holds, searchable by words and tagged with who, which session and which generation (rotation plan R4).

Why: a seat that rotates to a fresh session keeps nothing of the old one in its context. Before a rotation the
outgoing session is exported here and checked (``verify``: a token from its last turn must be found); after it, the
new session looks things up (``query``) instead of relying on a summary.

Store: ``<data dir>/state/episodic-memory.db`` (SQLite, WAL, mode 0600: tool output can be sensitive). The data dir
is ``$ORCHESTRA_DIR`` (else ``~/.orchestra``); every function also takes ``db_path`` so tests use a temp dir.

  entries            one row per part: (session_id, uuid, part) is unique, so ingesting a line twice changes
                     nothing. Tags: agent, session_id, generation, uuid (the transcript line), part (its block),
                     tool_id, role, kind (message | tool_call | tool_result | decision), ts.
  entries_fts        FTS5 over entries.text (external content, kept in step by triggers); ranked by BM25.
  cursors            per transcript file: the byte offset read up to (plus inode and size), so a growing session
                     is read forward from where the last ingest stopped, never re-parsed whole.
  embeddings         one vector per entry, with the model and dimension that made it. Created now and empty: a
                     semantic library can fill it later without a migration (no new dependency today).
  meta               schema version.

Transcripts: Claude Code JSONL. Codex and Gemini logs are refused with a clear error until their readers land.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

SCHEMA_VERSION = 1
DB_REL = Path("state") / "episodic-memory.db"
MAX_TEXT = 32 * 1024            # a part's stored text; a huge tool result is kept to its start
KINDS = ("message", "tool_call", "tool_result", "decision")
# The operator's answer to a card arrives in the agent's transcript as a line of its own.
DECISION = re.compile(r"^\s*\[(?:DECISION ANSWERED|DECISION|APPROVAL (?:APPROVED|DENIED|ANSWERED))\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  generation  INTEGER,
  uuid        TEXT NOT NULL,
  part        INTEGER NOT NULL DEFAULT 0,
  tool_id     TEXT,
  role        TEXT,
  kind        TEXT NOT NULL,
  ts          TEXT,
  text        TEXT NOT NULL,
  UNIQUE (session_id, uuid, part)
);
CREATE INDEX IF NOT EXISTS entries_agent ON entries(agent, session_id, generation);
CREATE INDEX IF NOT EXISTS entries_tool ON entries(tool_id);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(text, content='entries', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
  INSERT INTO entries_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS cursors (
  path        TEXT PRIMARY KEY,
  agent       TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  generation  INTEGER,
  offset      INTEGER NOT NULL,
  inode       INTEGER,
  size        INTEGER,
  updated     TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
  entry_id    INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  model       TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  vector      BLOB NOT NULL,
  created     TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

ROW_COLS = "id, agent, session_id, generation, uuid, part, tool_id, role, kind, ts, text"


class UnsupportedTranscript(ValueError):
    """The file is not a transcript this index can read yet (Codex, Gemini, or not a transcript at all)."""


def data_dir() -> Path:
    env = (os.environ.get("ORCHESTRA_DIR") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".orchestra"


def default_db_path() -> Path:
    return data_dir() / DB_REL


def _guard(path: Path) -> None:
    """Under pytest, refuse to write a real data dir (the suite's shared fence; a no-op otherwise)."""
    try:
        from orchestra_cli.settings import guard_test_write
    except Exception:  # noqa: BLE001  (the CLI package is optional for this module)
        return
    guard_test_write(path, "the episodic memory index")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the index. The file and its WAL are the owner's only (0600)."""
    path = Path(db_path) if db_path else default_db_path()
    _guard(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    try:
        conn = sqlite3.connect(str(path))
    finally:
        os.umask(old)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            os.chmod(p, 0o600)
        except FileNotFoundError:
            pass
    return conn


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _clip(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "\n[cut: the rest was not indexed]"


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and isinstance(c.get("text"), str))
    return ""


def _sniff(line: dict) -> None:
    """Refuse a log this reader does not know, clearly, rather than indexing nothing silently."""
    t = line.get("type")
    if t in ("session_meta", "response_item", "event_msg", "turn_context"):
        raise UnsupportedTranscript("this is a Codex rollout log: the episodic index reads Claude Code transcripts only for now")
    if ("messages" in line and "sessionId" in line and "uuid" not in line) or t in ("gemini", "model") or "candidates" in line:
        raise UnsupportedTranscript("this is a Gemini log: the episodic index reads Claude Code transcripts only for now")


def parts_of(line: dict) -> list[dict]:
    """The indexable parts of one Claude Code transcript line, numbered by their block. Thinking, sidechain
    (subagent) and meta lines are not indexed: they are not the conversation."""
    if line.get("type") not in ("user", "assistant") or line.get("isSidechain") or line.get("isMeta"):
        return []
    uuid = line.get("uuid")
    if not uuid:
        return []
    msg = line.get("message") or {}
    role = msg.get("role") or line.get("type")
    content = msg.get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content if isinstance(content, list) else [])
    out = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        base = {"uuid": uuid, "part": i, "role": role, "ts": line.get("timestamp")}
        if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
            kind = "decision" if role == "user" and DECISION.match(b["text"]) else "message"
            out.append({**base, "kind": kind, "tool_id": None, "text": _clip(b["text"])})
        elif b.get("type") == "tool_use" and role == "assistant":
            payload = json.dumps(b.get("input") or {}, ensure_ascii=False, sort_keys=True)
            out.append({**base, "kind": "tool_call", "tool_id": b.get("id"), "text": _clip(f"{b.get('name') or 'tool'} {payload}")})
        elif b.get("type") == "tool_result":
            text = _result_text(b.get("content"))
            if text.strip():
                out.append({**base, "kind": "tool_result", "tool_id": b.get("tool_use_id"), "text": _clip(text)})
    return out


def ingest_session(agent: str, session_id: str, generation: int | None, path: str | Path,
                   db_path: Path | str | None = None) -> dict:
    """Index one session's transcript, reading forward from where the last ingest of this file stopped.

    Idempotent: a line read twice adds nothing ((session_id, uuid, part) is unique). A file that was replaced or cut
    shorter is read again from its start. Returns ``{"added": n, "offset": bytes_read_to, "lines": n}``.
    Raises ``UnsupportedTranscript`` for a Codex or Gemini log, ``FileNotFoundError`` for a missing file."""
    if not agent or not session_id:
        raise ValueError("agent and session_id are required")
    p = Path(path)
    st = p.stat()
    conn = connect(db_path)
    try:
        cur = conn.execute("SELECT offset, inode, size FROM cursors WHERE path = ?", (str(p),)).fetchone()
        start = cur["offset"] if cur and cur["inode"] == st.st_ino and st.st_size >= cur["offset"] else 0
        with open(p, "rb") as f:
            f.seek(start)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:                                    # no complete line since the last read
            return {"added": 0, "offset": start, "lines": 0}
        lines = chunk[: end + 1].decode("utf-8", errors="replace").splitlines()
        added = 0
        sniffed = start > 0
        claude_like = start > 0                        # a file read before already passed
        parsed = 0
        with conn:
            for raw in lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                parsed += 1
                if obj.get("uuid") and obj.get("type") in ("user", "assistant", "system", "attachment") or obj.get("type") == "summary":
                    claude_like = True
                if not sniffed:
                    _sniff(obj)
                    sniffed = True
                for pt in parts_of(obj):
                    c = conn.execute(
                        "INSERT OR IGNORE INTO entries(agent, session_id, generation, uuid, part, tool_id, role, kind, ts, text) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (agent, session_id, generation, pt["uuid"], pt["part"], pt["tool_id"], pt["role"], pt["kind"], pt["ts"], pt["text"]))
                    added += c.rowcount
            if parsed and not claude_like:
                raise UnsupportedTranscript(f"{p.name} is not a Claude Code transcript: the episodic index reads only those for now")
            offset = start + end + 1
            conn.execute(
                "INSERT INTO cursors(path, agent, session_id, generation, offset, inode, size, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET agent=excluded.agent, session_id=excluded.session_id, generation=excluded.generation, "
                "offset=excluded.offset, inode=excluded.inode, size=excluded.size, updated=excluded.updated",
                (str(p), agent, session_id, generation, offset, st.st_ino, st.st_size, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        return {"added": added, "offset": offset, "lines": len(lines)}
    finally:
        conn.close()


def verify(agent: str, session_id: str, token: str, db_path: Path | str | None = None) -> tuple[bool, dict | None]:
    """Is ``token`` in this agent's session, as indexed? The rotation's export check: it does not go on until the
    token from the outgoing session's last turn is found. Returns ``(True, row)`` (the newest row holding it) or
    ``(False, None)``. Exact substring match, so a token with punctuation is found as written."""
    if not token:
        return False, None
    conn = connect(db_path)
    try:
        r = conn.execute(
            f"SELECT {ROW_COLS} FROM entries WHERE agent = ? AND session_id = ? AND instr(text, ?) > 0 ORDER BY id DESC LIMIT 1",
            (agent, session_id, token)).fetchone()
        return (r is not None), _row(r)
    finally:
        conn.close()


def _fts_query(q: str) -> str:
    """Each word quoted, so FTS syntax in a question (a dash, a colon, AND/OR, quotes) is read as words."""
    words = [w for w in re.split(r"\s+", q.strip()) if w]
    return " ".join('"' + w.replace('"', '""') + '"' for w in words)


def query(q: str, agent: str | None = None, session: str | None = None, generation: int | None = None,
          limit: int = 20, db_path: Path | str | None = None) -> list[dict]:
    """Rows matching every word of ``q`` (BM25 order, best first), narrowed by agent, session and generation.
    Each row carries its tags and ids plus a ``snippet`` around the match and its ``rank``."""
    fts = _fts_query(q or "")
    if not fts:
        return []
    where, args = ["entries_fts MATCH ?"], [fts]
    if agent:
        where.append("e.agent = ?"); args.append(agent)
    if session:
        where.append("e.session_id = ?"); args.append(session)
    if generation is not None:
        where.append("e.generation = ?"); args.append(int(generation))
    limit = max(1, min(int(limit or 20), 200))
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT e.id, e.agent, e.session_id, e.generation, e.uuid, e.part, e.tool_id, e.role, e.kind, e.ts, "
            f"snippet(entries_fts, 0, '[', ']', ' … ', 24) AS snippet, bm25(entries_fts) AS rank "
            f"FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid WHERE {' AND '.join(where)} "
            f"ORDER BY rank LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stats(db_path: Path | str | None = None) -> dict:
    """Counts, for the CLI and checks: entries per kind, sessions, embeddings."""
    conn = connect(db_path)
    try:
        kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, count(*) n FROM entries GROUP BY kind")}
        return {"entries": sum(kinds.values()), "kinds": kinds,
                "sessions": conn.execute("SELECT count(DISTINCT session_id) FROM entries").fetchone()[0],
                "embeddings": conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]}
    finally:
        conn.close()


# ── Finding a session's transcript and generation (for the CLI) ──────────────────────────────────────────────────

def find_transcript(session_id: str) -> Path | None:
    """A Claude Code session's JSONL under ``$CLAUDE_CONFIG_DIR`` (else ``~/.claude``) ``/projects/*/<sid>.jsonl``."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session_id or ""):
        return None
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    hits = sorted(root.glob(f"*/{session_id}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if root.is_dir() else []
    return hits[0] if hits else None


def generation_of(session_id: str) -> int | None:
    """The generation the identity store records for a session id (read only); None when it has none."""
    db = data_dir() / "state" / "orchestra-registry.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            r = conn.execute("SELECT generation FROM generations WHERE session_id = ?", (session_id,)).fetchone()
            return int(r[0]) if r else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


if __name__ == "__main__":  # pragma: no cover - the CLI lives in orchestra_cli/memory_cmd.py
    from orchestra_cli.memory_cmd import main
    sys.exit(main(sys.argv[1:]))
