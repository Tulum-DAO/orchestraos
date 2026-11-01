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

Provider-agnostic rotation (Release 1) adds, all created by ``connect()``:
  provider_sessions  the seat's provider sessions under its one thread: many per seat, each active, inactive or
                     failed, keyed by the provider's own session id; at most one active per seat. A forked check copy
                     is recorded too (reason "check copy", never active); a session that was never active is never
                     the seat's conversation: its transcript is not ingested and every reader ignores its rows.
                     prompt_chars / cost_tokens: the rolling context's size and a rotation's token cost.
  seen_only          transcript entries a session SAW but that are not the conversation: the entry that carried a
                     briefing. Readers remove the ``<earlier-conversation>`` block from THAT entry only (``strip_seen``),
                     so a person typing the same words elsewhere is never stripped. What the model is given and what
                     gets saved are kept apart by id, never by "the whole input" (the idea of CopilotKit/OpenBot's
                     run-turn.ts, MIT). "Never saved" is enforced by OUR readers only (chat route, this index,
                     library attribution, summaries skip seen-only blocks and seed rows): the CLI writes the briefing
                     into its own session file, and nothing in that file is altered.
  seed_boundary      a session's seed rows (seeded-file mode, Release 2): rows at or before ``through_uuid`` are
                     skipped by every reader.
  compaction_notices the chat's "Chat compacting…" / "Chat compacted" notice, kept here so it survives a restart;
                     never written to a transcript or into any model's context.
  ingest_state       per seat: transcript bytes not yet indexed and the last tick time (``ingest_tick``).
  compaction_attempts every attempt to compact a seat's chat, automatic or a person's /compact: when (the clock),
                     how it was started, what happened (committed | failed | waiting) and why, in plain words a person
                     understands (redacted like everything indexed). The agent reads it with
                     ``orchestra memory compaction-status`` and ``orchestra memory search``.

Every text that goes in passes ``redact`` (secrets by their shape: vendor keys, tokens, passwords in URLs, a value
named like a secret), the twin of api/src/lib/redactSecrets.ts.

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
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE OF text ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO entries_fts(rowid, text) VALUES (new.id, new.text);
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
CREATE TABLE IF NOT EXISTS provider_sessions (
  id                  INTEGER PRIMARY KEY,
  agent               TEXT NOT NULL,
  runtime             TEXT,
  model               TEXT,
  provider_session_id TEXT UNIQUE NOT NULL,
  state               TEXT NOT NULL CHECK (state IN ('active', 'inactive', 'failed')),
  generation          INTEGER,
  seed_mode           TEXT,
  started_at          TEXT,
  ended_at            TEXT,
  reason              TEXT,
  prompt_chars        INTEGER,
  cost_tokens         INTEGER,
  was_active          INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS provider_sessions_one_active ON provider_sessions(agent) WHERE state = 'active';
CREATE INDEX IF NOT EXISTS provider_sessions_agent ON provider_sessions(agent, started_at);
CREATE TABLE IF NOT EXISTS seen_only (
  provider_session_id TEXT NOT NULL,
  uuid                TEXT NOT NULL,
  marker              TEXT,
  PRIMARY KEY (provider_session_id, uuid)
);
CREATE TABLE IF NOT EXISTS seed_boundary (
  provider_session_id TEXT PRIMARY KEY,
  through_uuid        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS compaction_notices (
  id             INTEGER PRIMARY KEY,
  agent          TEXT NOT NULL,
  state          TEXT NOT NULL CHECK (state IN ('compacting', 'compacted', 'failed')),
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  before_tokens  INTEGER,
  after_tokens   INTEGER,
  from_session   TEXT,
  to_session     TEXT,
  trigger        TEXT NOT NULL DEFAULT 'auto' CHECK (trigger IN ('auto', 'manual'))
);
CREATE INDEX IF NOT EXISTS compaction_notices_agent ON compaction_notices(agent, started_at);
CREATE TABLE IF NOT EXISTS compaction_attempts (
  id            INTEGER PRIMARY KEY,
  agent         TEXT NOT NULL,
  at            TEXT NOT NULL,
  trigger       TEXT NOT NULL CHECK (trigger IN ('auto', 'manual')),
  outcome       TEXT NOT NULL CHECK (outcome IN ('committed', 'failed', 'waiting')),
  reason        TEXT,
  notice_id     INTEGER,
  from_session  TEXT,
  to_session    TEXT
);
CREATE INDEX IF NOT EXISTS compaction_attempts_agent ON compaction_attempts(agent, at);
CREATE TABLE IF NOT EXISTS ingest_state (
  agent      TEXT PRIMARY KEY,
  lag_bytes  INTEGER NOT NULL DEFAULT 0,
  last_tick  TEXT,
  last_error TEXT
);
"""

ROW_COLS = "id, agent, session_id, generation, uuid, part, tool_id, role, kind, ts, text"
SESSION_STATES = ("active", "inactive", "failed")
NOTICE_STATES = ("compacting", "compacted", "failed")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class UnsupportedTranscript(ValueError):
    """The file is not a transcript this index can read yet (Codex, Gemini, or not a transcript at all)."""


class NeverActive(ValueError):
    """The session is registered but was never the seat's active one (a forked check copy): never ingested."""


# Rows of a registered session that was never active (a check copy) are never the seat's conversation.
NOT_CHECK_COPY = "session_id NOT IN (SELECT provider_session_id FROM provider_sessions WHERE was_active = 0)"


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
    # added with rotation: whether a file's seed rows (seeded-file mode) have all been passed
    if "seed_done" not in {r[1] for r in conn.execute("PRAGMA table_info(cursors)")}:
        conn.execute("ALTER TABLE cursors ADD COLUMN seed_done INTEGER NOT NULL DEFAULT 0")
    if "trigger" not in {r[1] for r in conn.execute("PRAGMA table_info(compaction_notices)")}:
        conn.execute("ALTER TABLE compaction_notices ADD COLUMN trigger TEXT NOT NULL DEFAULT 'auto'")
    have = {r[1] for r in conn.execute("PRAGMA table_info(provider_sessions)")}
    for col, decl in (("prompt_chars", "INTEGER"), ("cost_tokens", "INTEGER"), ("was_active", "INTEGER NOT NULL DEFAULT 0")):
        if col not in have:
            conn.execute(f"ALTER TABLE provider_sessions ADD COLUMN {col} {decl}")
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            os.chmod(p, 0o600)
        except FileNotFoundError:
            pass
    return conn


# ── Seen, not saved: the briefing block and seed rows ────────────────────────────────────────────────────────────

# The blocks a session is given but that are not the conversation: the earlier conversation a new session starts
# from (the fallback path), and the short note after a person's /compact did not go through. Same rule for both.
MARKERS = (("<earlier-conversation>", "</earlier-conversation>"), ("<compact-note>", "</compact-note>"))
BLOCK_OPEN, BLOCK_CLOSE = MARKERS[0]
_BLOCKS = [(o, re.compile(re.escape(o) + r".*?" + re.escape(c) + r"[ \t]*\n?", re.S)) for o, c in MARKERS]


def _has_block(text: str) -> bool:
    return any(o in (text or "") for o, _ in MARKERS)


def strip_block(text: str) -> str:
    """The text without its ``<earlier-conversation>`` and ``<compact-note>`` blocks (an unclosed one runs to the end),
    trimmed of the blank lines they leave. Pure; ``strip_seen`` decides whether an entry gets it at all."""
    if not _has_block(text):
        return text
    out = text
    for o, rx in _BLOCKS:
        out = rx.sub("", out)
        k = out.find(o)
        if k >= 0:
            out = out[:k]
    return out.strip("\n")


def _seen_set(conn: sqlite3.Connection, provider_session_id: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT uuid FROM seen_only WHERE provider_session_id = ?", (provider_session_id,))}


def _boundary(conn: sqlite3.Connection, provider_session_id: str) -> str | None:
    r = conn.execute("SELECT through_uuid FROM seed_boundary WHERE provider_session_id = ?", (provider_session_id,)).fetchone()
    return r[0] if r else None


def strip_seen(provider_session_id: str, uuid: str, text: str, db_path: Path | str | None = None) -> str:
    """THE rule every reader uses: the blocks (earlier conversation, compact note) are removed from the one entry
    marked seen-only in that session, and from nothing else (the same words typed by a person elsewhere stay)."""
    if not _has_block(text):
        return text
    conn = connect(db_path)
    try:
        hit = conn.execute("SELECT 1 FROM seen_only WHERE provider_session_id = ? AND uuid = ?", (provider_session_id, uuid)).fetchone()
    finally:
        conn.close()
    return strip_block(text) if hit else text


def mark_seen_only(provider_session_id: str, uuid: str, db_path: Path | str | None = None) -> None:
    """Record that this entry carried a briefing. Rows already indexed for it are stripped now (and dropped when
    nothing else was in them), so the index never holds what the session only saw."""
    if not provider_session_id or not uuid:
        raise ValueError("provider_session_id and uuid are required")
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("INSERT OR IGNORE INTO seen_only(provider_session_id, uuid, marker) VALUES (?, ?, ?)",
                         (provider_session_id, uuid, "earlier-conversation"))
            for r in conn.execute("SELECT id, text FROM entries WHERE session_id = ? AND uuid = ?", (provider_session_id, uuid)).fetchall():
                t = strip_block(r["text"])
                if t.strip():
                    if t != r["text"]:
                        conn.execute("UPDATE entries SET text = ? WHERE id = ?", (t, r["id"]))
                else:
                    conn.execute("DELETE FROM entries WHERE id = ?", (r["id"],))
    finally:
        conn.close()


def set_seed_boundary(provider_session_id: str, through_uuid: str, db_path: Path | str | None = None) -> None:
    """Rows at or before ``through_uuid`` in that session are seed (seeded-file mode): every reader skips them."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO seed_boundary(provider_session_id, through_uuid) VALUES (?, ?) "
                         "ON CONFLICT(provider_session_id) DO UPDATE SET through_uuid = excluded.through_uuid",
                         (provider_session_id, through_uuid))
            # rows already indexed from the seed go (ingest inserts in file order): seed is never the conversation
            cut = conn.execute("SELECT max(id) FROM entries WHERE session_id = ? AND uuid = ?", (provider_session_id, through_uuid)).fetchone()[0]
            if cut is not None:
                conn.execute("DELETE FROM entries WHERE session_id = ? AND id <= ?", (provider_session_id, cut))
    finally:
        conn.close()


# ── Secrets out, by shape (the twin of api/src/lib/redactSecrets.ts) ─────────────────────────────────────────────

_NAME = r"[A-Za-z0-9_.-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY)[A-Za-z0-9_.-]*"
_REDACT = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), None),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), None),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"), None),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"), None),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"), None),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), None),
    ("AWS key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), None),
    ("Google key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"), None),
    ("Google OAuth secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"), None),
    ("Google OAuth token", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"), None),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), None),
    ("bearer token", re.compile(r"\b(Bearer)\s+[A-Za-z0-9._~+/=-]{16,}", re.I), lambda m: f"{m.group(1)} "),
    ("URL password", re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@", re.I), lambda m: f"{m.group(1)}:"),
    ("URL token", re.compile(r"([?&](?:access_?token|token|api_?key|key|secret|password|sig|signature)=)[^\s&#\"']+", re.I), lambda m: m.group(1)),
    ("named secret", re.compile(r"([\"']?\b" + _NAME + r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}{]{4,})", re.I), lambda m: m.group(1)),
]


def redact(text: str) -> str:
    """Secrets replaced by ``[redacted: <kind>]``, by their SHAPE only (a known key format, or a value whose name
    says secret); hashes, uuids and commit ids stay readable. Applied to everything indexed and every attempt reason."""
    out = str(text or "")
    for kind, rx, keep in _REDACT:
        out = rx.sub(lambda m: m.group(0) if "[redacted:" in m.group(0) else f"{keep(m) if keep else ''}[redacted: {kind}]", out)
    return out


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
        ps = conn.execute("SELECT state, was_active, reason FROM provider_sessions WHERE provider_session_id = ?", (session_id,)).fetchone()
        if ps is not None and not ps["was_active"]:
            raise NeverActive(f"{session_id} was never the seat's active session ({ps['reason'] or ps['state']}): not its conversation")
        cur = conn.execute("SELECT offset, inode, size, seed_done FROM cursors WHERE path = ?", (str(p),)).fetchone()
        same = bool(cur and cur["inode"] == st.st_ino and st.st_size >= cur["offset"])
        start = cur["offset"] if same else 0
        seen = _seen_set(conn, session_id)
        boundary = _boundary(conn, session_id)
        # seed rows sit at the start of a seeded file; once the boundary line has been read, the rest is real
        seed_done = (not boundary) or bool(same and cur["seed_done"])
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
                if not seed_done:
                    if obj.get("uuid") == boundary:
                        seed_done = True
                    continue
                for pt in parts_of(obj):
                    if pt["uuid"] in seen:
                        pt = {**pt, "text": strip_block(pt["text"])}
                        if not pt["text"].strip():
                            continue
                    pt = {**pt, "text": redact(pt["text"])}
                    c = conn.execute(
                        "INSERT OR IGNORE INTO entries(agent, session_id, generation, uuid, part, tool_id, role, kind, ts, text) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (agent, session_id, generation, pt["uuid"], pt["part"], pt["tool_id"], pt["role"], pt["kind"], pt["ts"], pt["text"]))
                    added += c.rowcount
            if parsed and not claude_like:
                raise UnsupportedTranscript(f"{p.name} is not a Claude Code transcript: the episodic index reads only those for now")
            offset = start + end + 1
            conn.execute(
                "INSERT INTO cursors(path, agent, session_id, generation, offset, inode, size, updated, seed_done) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET agent=excluded.agent, session_id=excluded.session_id, generation=excluded.generation, "
                "offset=excluded.offset, inode=excluded.inode, size=excluded.size, updated=excluded.updated, seed_done=excluded.seed_done",
                (str(p), agent, session_id, generation, offset, st.st_ino, st.st_size, _now(), 1 if seed_done else 0))
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
            f"SELECT {ROW_COLS} FROM entries WHERE agent = ? AND session_id = ? AND instr(text, ?) > 0 AND {NOT_CHECK_COPY} ORDER BY id DESC LIMIT 1",
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
    where, args = ["entries_fts MATCH ?", "e." + NOT_CHECK_COPY], [fts]
    if agent:
        where.append("e.agent = ?"); args.append(agent)
    if session:
        where.append("e.session_id = ?"); args.append(session)
    if generation is not None:
        where.append("e.generation = ?"); args.append(int(generation))
    limit = max(1, min(int(limit or 20), 200))
    # A question about compacting also gets the attempts (when, how started, what happened, why), first.
    extra: list[dict] = []
    if about_compacting(q) and session is None and generation is None:
        for a in attempts(agent, limit=limit, db_path=db_path):
            extra.append({"id": a["id"], "agent": a["agent"], "session_id": a["from_session"], "generation": None,
                          "uuid": f"attempt-{a['id']}", "part": 0, "tool_id": None, "role": "system",
                          "kind": "compaction_attempt", "ts": a["at"], "snippet": attempt_line(a), "rank": 0.0,
                          "trigger": a["trigger"], "outcome": a["outcome"], "reason": a["reason"]})
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT e.id, e.agent, e.session_id, e.generation, e.uuid, e.part, e.tool_id, e.role, e.kind, e.ts, "
            f"snippet(entries_fts, 0, '[', ']', ' … ', 24) AS snippet, bm25(entries_fts) AS rank "
            f"FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid WHERE {' AND '.join(where)} "
            f"ORDER BY rank LIMIT ?", (*args, limit)).fetchall()
        return (extra + [dict(r) for r in rows])[:limit] if extra else [dict(r) for r in rows]
    finally:
        conn.close()


def stats(db_path: Path | str | None = None) -> dict:
    """Counts, for the CLI and checks: entries per kind, sessions, embeddings, and per seat the ingest lag
    (transcript bytes not yet indexed at the last tick, and when that tick ran)."""
    conn = connect(db_path)
    try:
        kinds = {r["kind"]: r["n"] for r in conn.execute("SELECT kind, count(*) n FROM entries GROUP BY kind")}
        lag = {r["agent"]: {"lag_bytes": r["lag_bytes"], "last_tick": r["last_tick"], "last_error": r["last_error"]}
               for r in conn.execute("SELECT agent, lag_bytes, last_tick, last_error FROM ingest_state")}
        return {"entries": sum(kinds.values()), "kinds": kinds,
                "sessions": conn.execute("SELECT count(DISTINCT session_id) FROM entries").fetchone()[0],
                "embeddings": conn.execute("SELECT count(*) FROM embeddings").fetchone()[0],
                "ingest_lag": lag}
    finally:
        conn.close()


# ── Provider sessions: many per seat, at most one active ─────────────────────────────────────────────────────────

def register_session(agent: str, runtime: str, model: str | None, provider_session_id: str, generation: int | None,
                     seed_mode: str | None, db_path: Path | str | None = None, *, state: str = "active",
                     reason: str | None = None, prompt_chars: int | None = None, cost_tokens: int | None = None) -> int:
    """Record a provider session under the seat's thread, by default as its ACTIVE one (any other active session of
    the seat becomes inactive, reason "replaced"). ``state="failed"`` or ``"inactive"`` records a session that must
    never serve, a forked check copy (``reason="check copy"``): no other session is touched and, never having been
    active, it is never read as the seat's conversation. Registering the same provider session again updates it and
    returns the same id. Returns the row id."""
    if not agent or not provider_session_id:
        raise ValueError("agent and provider_session_id are required")
    if state not in SESSION_STATES:
        raise ValueError(f"state must be one of {SESSION_STATES}")
    conn = connect(db_path)
    try:
        with conn:
            now = _now()
            if state != "active":
                conn.execute(
                    "INSERT INTO provider_sessions(agent, runtime, model, provider_session_id, state, generation, seed_mode, started_at, "
                    "ended_at, reason, prompt_chars, cost_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(provider_session_id) DO UPDATE SET state=excluded.state, ended_at=coalesce(provider_sessions.ended_at, excluded.ended_at), "
                    "reason=excluded.reason, prompt_chars=coalesce(excluded.prompt_chars, provider_sessions.prompt_chars), "
                    "cost_tokens=coalesce(excluded.cost_tokens, provider_sessions.cost_tokens)",
                    (agent, runtime, model, provider_session_id, state, generation, seed_mode, now, now, reason, prompt_chars, cost_tokens))
                return int(conn.execute("SELECT id FROM provider_sessions WHERE provider_session_id = ?", (provider_session_id,)).fetchone()[0])
            conn.execute("UPDATE provider_sessions SET state = 'inactive', ended_at = ?, reason = 'replaced' "
                         "WHERE agent = ? AND state = 'active' AND provider_session_id != ?", (now, agent, provider_session_id))
            conn.execute(
                "INSERT INTO provider_sessions(agent, runtime, model, provider_session_id, state, generation, seed_mode, started_at, "
                "reason, prompt_chars, cost_tokens, was_active) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(provider_session_id) DO UPDATE SET agent=excluded.agent, runtime=excluded.runtime, model=excluded.model, "
                "state='active', generation=excluded.generation, seed_mode=excluded.seed_mode, ended_at=NULL, reason=excluded.reason, "
                "prompt_chars=coalesce(excluded.prompt_chars, provider_sessions.prompt_chars), "
                "cost_tokens=coalesce(excluded.cost_tokens, provider_sessions.cost_tokens), was_active=1",
                (agent, runtime, model, provider_session_id, generation, seed_mode, now, reason, prompt_chars, cost_tokens))
            return int(conn.execute("SELECT id FROM provider_sessions WHERE provider_session_id = ?", (provider_session_id,)).fetchone()[0])
    finally:
        conn.close()


def set_session_state(provider_session_id: str, state: str, reason: str | None = None,
                      db_path: Path | str | None = None) -> None:
    """Move a session to active, inactive or failed. Making one active makes the seat's other active one inactive;
    leaving active stamps ``ended_at``. Unknown session: ``KeyError``; unknown state: ``ValueError``."""
    if state not in SESSION_STATES:
        raise ValueError(f"state must be one of {SESSION_STATES}")
    conn = connect(db_path)
    try:
        with conn:
            r = conn.execute("SELECT agent FROM provider_sessions WHERE provider_session_id = ?", (provider_session_id,)).fetchone()
            if r is None:
                raise KeyError(provider_session_id)
            now = _now()
            if state == "active":
                conn.execute("UPDATE provider_sessions SET state = 'inactive', ended_at = ?, reason = 'replaced' "
                             "WHERE agent = ? AND state = 'active' AND provider_session_id != ?", (now, r["agent"], provider_session_id))
                conn.execute("UPDATE provider_sessions SET state = 'active', ended_at = NULL, reason = ?, was_active = 1 WHERE provider_session_id = ?",
                             (reason, provider_session_id))
            else:
                conn.execute("UPDATE provider_sessions SET state = ?, ended_at = coalesce(ended_at, ?), reason = ? WHERE provider_session_id = ?",
                             (state, now, reason, provider_session_id))
    finally:
        conn.close()


def set_session_cost(provider_session_id: str, prompt_chars: int | None = None, cost_tokens: int | None = None,
                     db_path: Path | str | None = None) -> None:
    """Fill in a session's rolling-context size (characters) and the rotation's token cost, when known."""
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("UPDATE provider_sessions SET prompt_chars = coalesce(?, prompt_chars), cost_tokens = coalesce(?, cost_tokens) "
                             "WHERE provider_session_id = ?", (prompt_chars, cost_tokens, provider_session_id))
            if not c.rowcount:
                raise KeyError(provider_session_id)
    finally:
        conn.close()


def active_session(agent: str, db_path: Path | str | None = None) -> dict | None:
    """The seat's active provider session, or None."""
    conn = connect(db_path)
    try:
        return _row(conn.execute("SELECT * FROM provider_sessions WHERE agent = ? AND state = 'active'", (agent,)).fetchone())
    finally:
        conn.close()


def sessions_of(agent: str, db_path: Path | str | None = None) -> list[dict]:
    """Every provider session of the seat, oldest first."""
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM provider_sessions WHERE agent = ? ORDER BY started_at, id", (agent,))]
    finally:
        conn.close()


def locate_cached(session_id: str, db_path: Path | str | None = None) -> Path | None:
    """A session's transcript: the path its cursor already holds while that file is still there, else the full
    search (find_transcript). A session indexed once is never searched for again until its file moves."""
    if not session_id:
        return None
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT path FROM cursors WHERE session_id = ? ORDER BY updated DESC", (session_id,)).fetchall()
    finally:
        conn.close()
    for r in rows:
        p = Path(r["path"])
        if p.is_file():
            return p
    return find_transcript(session_id)


def _lock_path(agent: str, db_path: Path | str | None) -> Path:
    base = (Path(db_path).parent if db_path else default_db_path().parent) / "ingest-locks"
    base.mkdir(parents=True, exist_ok=True)
    return base / (re.sub(r"[^A-Za-z0-9._-]", "_", agent) + ".lock")


def ingest_tick(agent: str, db_path: Path | str | None = None,
                locate: "callable | None" = None, wait_s: float | None = 30.0) -> dict:
    """One pass under the seat's ingest lock (the minute beat, a supervisor's save after each message and a
    rotation's export all take it, so two never read one seat at once). ``wait_s``: how long to wait for it (0: do
    not wait; the pass is then reported as ``{"skipped": "busy"}``)."""
    import fcntl
    fh = open(_lock_path(agent, db_path), "a+")
    try:
        deadline = time.monotonic() + (wait_s or 0)
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    return {"agent": agent, "sessions": [], "added": 0, "lag_bytes": None, "at": _now(), "skipped": "busy"}
                time.sleep(0.05)
        return _ingest_tick(agent, db_path, locate)
    finally:
        fh.close()


def _ingest_tick(agent: str, db_path: Path | str | None = None,
                 locate: "callable | None" = None) -> dict:
    """One automatic ingest pass for a seat: its ACTIVE session's transcript, and the tail of the session before it
    (the last lines it wrote before it was replaced). Forward from each file's cursor; seed rows skipped and
    seen-only blocks stripped (as ``ingest_session`` always does). Records the lag (transcript bytes not yet indexed
    after this pass) and the tick time, readable through ``stats()["ingest_lag"]``. ``locate(provider_session_id)``
    finds a session's file (default: ``find_transcript``). Returns ``{"agent", "sessions": [...], "added",
    "lag_bytes", "at"}``; a session whose file is missing or unreadable is reported in its entry, never raised."""
    locate = locate or (lambda sid: locate_cached(sid, db_path))
    rows = sessions_of(agent, db_path=db_path)
    active = [r for r in rows if r["state"] == "active"]
    previous = [r for r in rows if r["state"] != "active" and r["ended_at"] and r["was_active"]]
    previous.sort(key=lambda r: (r["ended_at"] or "", r["id"]))
    todo = active[:1] + previous[-1:]
    out, added, lag, errors = [], 0, 0, []
    for r in todo:
        sid = r["provider_session_id"]
        entry = {"provider_session_id": sid, "state": r["state"]}
        path = locate(sid)
        if not path:
            entry["error"] = "transcript not found"
            errors.append(f"{sid}: transcript not found")
            out.append(entry)
            continue
        try:
            got = ingest_session(agent, sid, r["generation"], path, db_path=db_path)
            entry.update(got)
            added += got["added"]
            lag += max(0, Path(path).stat().st_size - got["offset"])
        except (UnsupportedTranscript, NeverActive, OSError) as e:
            entry["error"] = str(e)
            errors.append(f"{sid}: {e}")
        out.append(entry)
    at = _now()
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO ingest_state(agent, lag_bytes, last_tick, last_error) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(agent) DO UPDATE SET lag_bytes=excluded.lag_bytes, last_tick=excluded.last_tick, last_error=excluded.last_error",
                         (agent, lag, at, "; ".join(errors) or None))
    finally:
        conn.close()
    return {"agent": agent, "sessions": out, "added": added, "lag_bytes": lag, "at": at}


# ── The compaction notice (survives a restart; never in any model's context) ─────────────────────────────────────

def notice_start(agent: str, from_session: str | None, before_tokens: int | None, trigger: str = "auto",
                 db_path: Path | str | None = None) -> int:
    """"Chat compacting…": a new notice for the seat, made once the switch is committed (never for an automatic
    attempt that fails). ``trigger`` 'auto' or 'manual' (a person's /compact): only a manual notice that ends
    'failed' is shown ("Couldn't compact the chat right now."). Returns its id (the chat shows ``cmp-<id>``)."""
    if trigger not in ("auto", "manual"):
        raise ValueError("trigger is 'auto' or 'manual'")
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("INSERT INTO compaction_notices(agent, state, started_at, before_tokens, from_session, trigger) "
                             "VALUES (?, 'compacting', ?, ?, ?, ?)", (agent, _now(), before_tokens, from_session, trigger))
            return int(c.lastrowid)
    finally:
        conn.close()


def notice_finish(notice_id: int, state: str, to_session: str | None = None, after_tokens: int | None = None,
                  db_path: Path | str | None = None) -> None:
    """The notice ends: "compacted" (the new session took over) or "failed" (the old one kept serving). The same
    notice updates in place."""
    if state not in ("compacted", "failed"):
        raise ValueError("a notice finishes as 'compacted' or 'failed'")
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("UPDATE compaction_notices SET state = ?, finished_at = ?, to_session = ?, after_tokens = ? WHERE id = ?",
                             (state, _now(), to_session, after_tokens, int(notice_id)))
            if not c.rowcount:
                raise KeyError(notice_id)
    finally:
        conn.close()


def notice_set_tokens(notice_id: int, after_tokens: int | None, db_path: Path | str | None = None) -> None:
    """The context size after the switch, filled in from the first turn's usage. The notice already reads
    "compacted" from the moment the new session was bound (``notice_finish``); this only adds the number."""
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("UPDATE compaction_notices SET after_tokens = ? WHERE id = ?", (after_tokens, int(notice_id)))
            if not c.rowcount:
                raise KeyError(notice_id)
    finally:
        conn.close()


def notices(agent: str, db_path: Path | str | None = None) -> list[dict]:
    """The seat's notices, oldest first."""
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM compaction_notices WHERE agent = ? ORDER BY started_at, id", (agent,))]
    finally:
        conn.close()


# ── Every attempt to compact, and why it did or didn't go through ────────────────────────────────────────────────

ATTEMPT_OUTCOMES = ("committed", "failed", "waiting")


def record_attempt(agent: str, trigger: str, outcome: str, reason: str | None, notice_id: int | None = None,
                   from_session: str | None = None, to_session: str | None = None,
                   db_path: Path | str | None = None) -> int:
    """One attempt to compact the seat's chat, automatic ('auto') or a person's /compact ('manual'): what happened
    (committed | failed | waiting) and why, in plain words (redacted). The time is the clock's. Returns its id."""
    if trigger not in ("auto", "manual"):
        raise ValueError("trigger is 'auto' or 'manual'")
    if outcome not in ATTEMPT_OUTCOMES:
        raise ValueError(f"outcome is one of {ATTEMPT_OUTCOMES}")
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("INSERT INTO compaction_attempts(agent, at, trigger, outcome, reason, notice_id, from_session, to_session) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (agent, _now(), trigger, outcome, redact(reason) if reason else None, notice_id, from_session, to_session))
            return int(c.lastrowid)
    finally:
        conn.close()


def update_attempt(attempt_id: int, outcome: str, reason: str | None = None, to_session: str | None = None,
                   db_path: Path | str | None = None) -> None:
    """A waiting attempt ends (committed or failed), or its reason changes. Its time stays when it started."""
    if outcome not in ATTEMPT_OUTCOMES:
        raise ValueError(f"outcome is one of {ATTEMPT_OUTCOMES}")
    conn = connect(db_path)
    try:
        with conn:
            c = conn.execute("UPDATE compaction_attempts SET outcome = ?, reason = coalesce(?, reason), "
                             "to_session = coalesce(?, to_session) WHERE id = ?",
                             (outcome, redact(reason) if reason else None, to_session, int(attempt_id)))
            if not c.rowcount:
                raise KeyError(attempt_id)
    finally:
        conn.close()


def attempts(agent: str | None, limit: int = 20, db_path: Path | str | None = None) -> list[dict]:
    """The latest attempts, newest first (every seat's when ``agent`` is None)."""
    limit = max(1, min(int(limit or 20), 500))
    conn = connect(db_path)
    try:
        if agent:
            rows = conn.execute("SELECT * FROM compaction_attempts WHERE agent = ? ORDER BY at DESC, id DESC LIMIT ?", (agent, limit))
        else:
            rows = conn.execute("SELECT * FROM compaction_attempts ORDER BY at DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
    finally:
        conn.close()


_HOW = {"auto": "started on its own", "manual": "you typed /compact"}
_WHAT = {"committed": "compacted", "failed": "didn't go through", "waiting": "waiting for a quiet moment"}


def attempt_line(a: dict, with_agent: bool = False) -> str:
    """One attempt in plain words: "2026-10-31 07:12Z · you typed /compact · didn't go through · <why>"."""
    when = (a.get("at") or "")[:16].replace("T", " ") + ("Z" if a.get("at") else "")
    bits = [when] + ([a["agent"]] if with_agent else []) + [_HOW.get(a.get("trigger"), a.get("trigger") or ""),
                                                            _WHAT.get(a.get("outcome"), a.get("outcome") or "")]
    if a.get("reason"):
        bits.append(a["reason"])
    return " · ".join(b for b in bits if b)


_ABOUT_COMPACTING = re.compile(r"(?:^|\s)/?compact(?:s|ed|ing|ion|ions)?\b", re.I)


def about_compacting(q: str) -> bool:
    """Is a question about compacting the chat ("compact", "compacting", "compaction", "/compact")?"""
    return bool(_ABOUT_COMPACTING.search(q or ""))


# ── What a briefing is built from (briefings read only through these) ──────────────────────────────────────────────────

# Lines a person did not type: mail, notifications, hooks, command echoes and the CLI's own wrappers.
_NOT_TYPED = re.compile(r"^\s*(\[MSG from |\[mail\]|<task-notification>|<command-|<local-command|<system-reminder>|"
                        r"Caveat: |\[DECISION|\[Request interrupted|<bash-|<user-prompt-submit-hook>|\[FILE: )", re.I)


def recent_window(agent: str, budget_chars: int, db_path: Path | str | None = None) -> list[dict]:
    """The seat's latest entries across its sessions (newest kept first) whose text fits ``budget_chars``, returned
    oldest first. Seed rows are never indexed and seen-only blocks are already stripped, so neither is here. The
    newest entry is always included, even when it alone is over the budget (the packet cuts it head-and-tail)."""
    conn = connect(db_path)
    try:
        out, used = [], 0
        rows = conn.execute(f"SELECT {ROW_COLS} FROM entries WHERE agent = ? AND {NOT_CHECK_COPY} ORDER BY coalesce(ts, ''), id", (agent,)).fetchall()
        for r in reversed(rows):
            n = len(r["text"])
            if out and used + n > budget_chars:
                break
            out.append(dict(r))
            used += n
        return out[::-1]
    finally:
        conn.close()


def operator_messages(agent: str, db_path: Path | str | None = None) -> list[dict]:
    """Every message the operator typed to this seat, across its sessions, oldest first: user-role text entries that
    are not tool results, decisions, mail, notifications or the CLI's own wrappers."""
    conn = connect(db_path)
    try:
        rows = conn.execute(f"SELECT {ROW_COLS} FROM entries WHERE agent = ? AND role = 'user' AND kind = 'message' AND {NOT_CHECK_COPY} "
                            "ORDER BY coalesce(ts, ''), id", (agent,)).fetchall()
        return [dict(r) for r in rows if not _NOT_TYPED.match(r["text"])]
    finally:
        conn.close()


WAKEUP_TOOLS = ("CronCreate", "ScheduleWakeup", "Monitor")
_WAKEUP_ENDS = ("CronDelete", "CancelWakeup", "ScheduleWakeupCancel", "MonitorStop", "MonitorCancel")


def wakeups(agent: str, db_path: Path | str | None = None) -> list[dict]:
    """What will wake the seat itself later and is still open, from its ACTIVE session's tool calls: each
    CronCreate, ScheduleWakeup or Monitor not since deleted or cancelled (a CronDelete, or a cancel naming its id or
    the tool call). These live in the CLI process and end with it, so Release 1 lists them (the briefing names them by
    id); re-creating them natively after a switch is Release 2. Each: {"tool_id", "tool", "schedule", "prompt",
    "id", "created_at"}."""
    act = active_session(agent, db_path=db_path)
    if not act:
        return []
    conn = connect(db_path)
    try:
        calls = conn.execute("SELECT tool_id, text, ts FROM entries WHERE agent = ? AND session_id = ? AND kind = 'tool_call' "
                             "ORDER BY coalesce(ts, ''), id", (agent, act["provider_session_id"])).fetchall()
        results = {r["tool_id"]: r["text"] for r in conn.execute(
            "SELECT tool_id, text FROM entries WHERE agent = ? AND session_id = ? AND kind = 'tool_result'", (agent, act["provider_session_id"]))}
    finally:
        conn.close()
    open_: dict[str, dict] = {}
    for c in calls:
        name, data = _tool_payload(c["text"])
        if name in WAKEUP_TOOLS:
            # the tool's own id for it (a cron job id) when its result names one, so a later delete can find it
            m = re.search(r"\b(?:id|job(?:_id)?|monitor(?:_id)?)\W+([A-Za-z0-9_-]{4,})", results.get(c["tool_id"]) or "", re.I)
            own = str(data.get("id") or (m.group(1) if m else "") or "")
            open_[c["tool_id"]] = {"tool_id": c["tool_id"], "tool": name, "id": own or None,
                                   "schedule": data.get("cron") or data.get("schedule") or data.get("delaySeconds") or data.get("delay")
                                   or data.get("interval") or data.get("at"),
                                   "prompt": data.get("prompt") or data.get("description") or data.get("command"),
                                   "created_at": c["ts"]}
        elif name in _WAKEUP_ENDS:
            ref = str(data.get("id") or data.get("jobId") or data.get("job_id") or data.get("monitorId") or data.get("tool_id") or "")
            for k, w in list(open_.items()):
                if ref and (ref == w["id"] or ref == k):
                    del open_[k]
    return list(open_.values())


def _tool_payload(text: str) -> tuple[str, dict]:
    name, _, rest = text.partition(" ")
    try:
        data = json.loads(rest) if rest else {}
    except json.JSONDecodeError:
        data = {}
    return name, data if isinstance(data, dict) else {}


def open_records(agent: str, db_path: Path | str | None = None, approvals_db: Path | str | None = None) -> dict:
    """What is still open for the seat, each with an id to cite:
      tasks:     from its task/todo tool calls: the latest TodoWrite list's items not completed, and tasks made with
                 TaskCreate whose latest TaskUpdate is not completed/deleted ({"id", "text", "status", "source"});
      approvals: its approval cards still pending, read only from the approvals ledger (<data dir>/state/tasks.db)
                 ({"id", "question", "status", "created_at"})."""
    conn = connect(db_path)
    try:
        calls = conn.execute(f"SELECT uuid, tool_id, text, ts FROM entries WHERE agent = ? AND kind = 'tool_call' AND {NOT_CHECK_COPY} "
                             "ORDER BY coalesce(ts, ''), id", (agent,)).fetchall()
    finally:
        conn.close()
    todo: list[dict] = []
    created: dict[str, dict] = {}
    for c in calls:
        name, data = _tool_payload(c["text"])
        if name == "TodoWrite" and isinstance(data.get("todos"), list):
            todo = [{"id": f"{c['tool_id']}#{i}", "text": str(t.get("content") or t.get("activeForm") or ""),
                     "status": str(t.get("status") or "pending"), "source": "TodoWrite"}
                    for i, t in enumerate(data["todos"]) if isinstance(t, dict)]
        elif name == "TaskCreate":
            key = str(data.get("id") or data.get("taskId") or c["tool_id"])
            created[key] = {"id": key, "text": str(data.get("subject") or data.get("description") or data.get("title") or ""),
                            "status": "pending", "source": "TaskCreate"}
        elif name == "TaskUpdate":
            key = str(data.get("id") or data.get("taskId") or "")
            if key in created and data.get("status"):
                created[key]["status"] = str(data["status"])
    tasks = [t for t in todo if t["status"] != "completed"] + \
            [t for t in created.values() if t["status"] not in ("completed", "deleted")]
    approvals: list[dict] = []
    adb = Path(approvals_db) if approvals_db else data_dir() / "state" / "tasks.db"
    if adb.exists():
        try:
            a = sqlite3.connect(f"file:{adb}?mode=ro", uri=True)
            try:
                for r in a.execute("SELECT id, question, status, created_at FROM approval_requests WHERE from_agent = ? "
                                   "AND status = 'pending' ORDER BY created_at", (agent,)):
                    approvals.append({"id": r[0], "question": r[1], "status": r[2], "created_at": r[3]})
            finally:
                a.close()
        except sqlite3.Error:
            pass
    return {"tasks": tasks, "approvals": approvals}


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



# ── Purge: a throwaway seat leaves nothing behind ───────────────────────────────────────────────────────────────

class StillRegistered(ValueError):
    """The agent is still in the registry: its rows are its conversation, not leftovers."""


def _registered(agent: str, registry: Path | None = None) -> bool:
    reg = Path(registry) if registry else data_dir() / "registry.json"
    try:
        agents = (json.loads(reg.read_text()) or {}).get("agents") or {}
    except (OSError, ValueError, AttributeError):
        raise StillRegistered(f"the registry ({reg}) could not be read, so {agent} cannot be shown to be gone")
    return agent in agents or any(isinstance(r, dict) and r.get("tmux_session") == agent for r in agents.values())


def purge(agent: str, db_path: Path | str | None = None, registry: Path | str | None = None,
          dry_run: bool = False) -> dict:
    """Delete everything the index holds for an agent that is no longer in the registry: its entries (and their
    search rows and embeddings), cursors, provider sessions with their seen-only marks and seed boundaries, notices,
    attempts and ingest state. Refuses (StillRegistered) for a registered agent. Idempotent. Returns the row counts
    per table (what was deleted, or with dry_run what would be)."""
    if not agent:
        raise ValueError("agent is required")
    if _registered(agent, registry):
        raise StillRegistered(f"{agent} is still in the registry: retire it first")
    conn = connect(db_path)
    try:
        sids = [r[0] for r in conn.execute("SELECT provider_session_id FROM provider_sessions WHERE agent = ?", (agent,))]
        sids += [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM entries WHERE agent = ?", (agent,))
                 if r[0] not in sids]
        marks = ",".join("?" * len(sids)) or "NULL"
        plan = [
            ("embeddings", "DELETE FROM embeddings WHERE entry_id IN (SELECT id FROM entries WHERE agent = ?)", (agent,)),
            ("entries", "DELETE FROM entries WHERE agent = ?", (agent,)),
            ("cursors", "DELETE FROM cursors WHERE agent = ?", (agent,)),
            ("seen_only", f"DELETE FROM seen_only WHERE provider_session_id IN ({marks})", tuple(sids)),
            ("seed_boundary", f"DELETE FROM seed_boundary WHERE provider_session_id IN ({marks})", tuple(sids)),
            ("provider_sessions", "DELETE FROM provider_sessions WHERE agent = ?", (agent,)),
            ("compaction_notices", "DELETE FROM compaction_notices WHERE agent = ?", (agent,)),
            ("compaction_attempts", "DELETE FROM compaction_attempts WHERE agent = ?", (agent,)),
            ("ingest_state", "DELETE FROM ingest_state WHERE agent = ?", (agent,)),
        ]
        counts = {}
        with conn:
            for table, sql, args in plan:
                count_sql = sql.replace("DELETE FROM", "SELECT COUNT(*) FROM", 1)
                counts[table] = conn.execute(count_sql, args).fetchone()[0]
                if not dry_run and counts[table]:
                    conn.execute(sql, args)
        return counts
    finally:
        conn.close()


def counts_for(agent: str, db_path: Path | str | None = None) -> dict:
    """How many rows each per-agent table holds for an agent (read only; for a check after a retire)."""
    conn = connect(db_path)
    try:
        sids = [r[0] for r in conn.execute("SELECT provider_session_id FROM provider_sessions WHERE agent = ?", (agent,))]
        marks = ",".join("?" * len(sids)) or "NULL"
        out = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE agent = ?", (agent,)).fetchone()[0]
               for t in ("entries", "cursors", "provider_sessions", "compaction_notices", "compaction_attempts",
                         "ingest_state")}
        out["seen_only"] = conn.execute(f"SELECT COUNT(*) FROM seen_only WHERE provider_session_id IN ({marks})",
                                        tuple(sids)).fetchone()[0]
        out["seed_boundary"] = conn.execute(f"SELECT COUNT(*) FROM seed_boundary WHERE provider_session_id IN ({marks})",
                                            tuple(sids)).fetchone()[0]
        return out
    finally:
        conn.close()
