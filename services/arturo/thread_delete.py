"""thread_delete — clearing a deleted chat's traces outside threads.db, and the facts learned in it.

A chat lives in more places than its threads.db rows (thread_store.delete_thread removes those):
  * <ARTURO_STATE>/unified-conversation.jsonl: a copy of what was said on a call, for cross-channel context. Lines
    written since this module carry the chat's `conversation_id`; older ones carry none and are matched by their
    words and time against the deleted chat's own messages.
  * the semantic-recall query log (state/semantic-memory.db, queries_log): the operator's words, kept as the query
    text of a recall run.
  * voice memory (voice-memory.json recent_transcripts, voice-transcripts-arturo/<id>.json), keyed by conversation,
    and the journals of the calls made in the chat (<VOICE_CALLS_DIR>/<call_id>.json).
Facts learned in a chat (operator_store facts, remember_note notes) carry `source_thread`. They are listed for the
delete dialog and removed only when the operator asks. A fact with no source_thread ("source unknown") never is.

Every rewrite is atomic (a temp file, then os.replace) under the same lock the jsonl's writer takes (append_jsonl).
The delete log names the chat, the time and who; never its words.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# A legacy (unstamped) jsonl line is matched only inside the chat's own lifetime, give or take this much.
LEGACY_WINDOW_S = 120
# The jsonl writer keeps at most this much of a message (arturo-proxy.py capture_mid_call_turns).
JSONL_CONTENT_MAX = 300
_SAFE_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}")
_ROLE = {"operator": "user", "system": "assistant", "user": "user", "assistant": "assistant"}


class RecentlyDeleted:
    """Chats deleted in the last `ttl_s` seconds. Work that started before a delete (a background job, a summary
    being written) checks this before writing into its chat, so a deleted chat is not brought back by it."""

    def __init__(self, ttl_s: float = 30 * 60, clock=time.monotonic):
        self._ttl, self._clock = ttl_s, clock
        self._mu = threading.Lock()
        self._at: dict[str, float] = {}

    def mark(self, cid: str) -> None:
        with self._mu:
            now = self._clock()
            self._at = {k: t for k, t in self._at.items() if now - t < self._ttl}
            self._at[cid] = now

    def unmark(self, cid: str) -> None:
        """The delete did not happen (it failed, or the chat was already gone): stop holding the id as deleted."""
        with self._mu:
            self._at.pop(cid, None)

    def has(self, cid: str) -> bool:
        with self._mu:
            t = self._at.get(cid)
            return t is not None and self._clock() - t < self._ttl


@contextlib.contextmanager
def _locked(path: Path):
    """An exclusive lock beside `path` (path + '.lock'): the appender and the rewriter never interleave."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _replace(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def append_jsonl(path, entry: dict) -> None:
    """Append one line to the unified log, under the lock purge_unified_log rewrites it under."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path), open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _epoch(ts) -> float | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def purge_unified_log(path, gone: dict) -> int:
    """Remove the deleted chat's lines; returns how many. `gone` is thread_store.delete_thread's result."""
    path = Path(path)
    if not path.exists():
        return 0
    cid = gone["id"]
    said = {(m["role"], (m["content"] or "")[:JSONL_CONTENT_MAX]) for m in gone.get("messages") or []}
    lo = float(gone.get("created") or 0) - LEGACY_WINDOW_S
    hi = float(gone.get("updated") or 0) + LEGACY_WINDOW_S
    with _locked(path):
        lines = path.read_text().splitlines(keepends=True)
        keep, removed = [], 0
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if not isinstance(e, dict):
                keep.append(line)
                continue
            if "conversation_id" in e:
                drop = e.get("conversation_id") == cid
            else:
                # A line from before lines were stamped: this chat's when it repeats one of the chat's own messages
                # during the chat's lifetime. A short common line ("yes") from another call in that window can match
                # too; for a delete, removing a little too much beats leaving the chat's words behind.
                t = _epoch(e.get("ts"))
                drop = (t is not None and lo <= t <= hi
                        and (_ROLE.get(e.get("role")), (e.get("content") or "")[:JSONL_CONTENT_MAX]) in said)
            if drop:
                removed += 1
            else:
                keep.append(line)
        if removed:
            _replace(path, "".join(keep))
    return removed


def purge_recall_queries(dbpath, gone: dict) -> int:
    """Remove recall query-log rows whose text is one of the chat's own user messages. No database (the optional
    semantic memory is not installed) or no such table is nothing to do."""
    dbpath = Path(dbpath)
    texts = sorted({(m["content"] or "").strip() for m in gone.get("messages") or [] if m["role"] == "user"} - {""})
    if not texts or not dbpath.exists():
        return 0
    conn = sqlite3.connect(str(dbpath), timeout=10)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='queries_log'").fetchone() is None:
            return 0
        n = 0
        with conn:
            for i in range(0, len(texts), 200):
                chunk = texts[i:i + 200]
                cur = conn.execute(f"DELETE FROM queries_log WHERE query_text IN ({','.join('?' * len(chunk))})",
                                   chunk)
                n += cur.rowcount or 0
        # The recall database may be in WAL mode: fold the log back so the deleted text is not left in -wal.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        return n
    finally:
        conn.close()


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def purge_voice_memory(memory_file, transcripts_dir, cid: str) -> int:
    """Remove this conversation's saved call transcript and its recent-transcript entry."""
    n = 0
    tx = Path(transcripts_dir) / f"{cid}.json"
    if _SAFE_NAME.fullmatch(cid) and tx.exists():
        tx.unlink()
        n += 1
    memory_file = Path(memory_file)
    mem = _load_json(memory_file)
    if mem and isinstance(mem.get("recent_transcripts"), list):
        keep = [t for t in mem["recent_transcripts"] if not (isinstance(t, dict) and t.get("conversation_id") == cid)]
        if len(keep) != len(mem["recent_transcripts"]):
            n += len(mem["recent_transcripts"]) - len(keep)
            mem["recent_transcripts"] = keep
            _replace(memory_file, json.dumps(mem, indent=2))
    return n


def purge_live_call(path, gone: dict) -> int:
    """The latest call's rolling copy (voice-live-call.json, its last turns word for word): removed when it is this
    chat's (stamped with its id) or, unstamped, when it holds one of the chat's own lines."""
    path = Path(path)
    data = _load_json(path)
    if data is None:
        return 0
    if "conversation_id" in data:
        mine = data.get("conversation_id") == gone["id"]
    else:
        said = {(m["content"] or "")[:JSONL_CONTENT_MAX] for m in gone.get("messages") or []} - {""}
        mine = any(isinstance(t, dict) and (t.get("text") or "") in said for t in data.get("turns") or [])
    if not mine:
        return 0
    path.unlink(missing_ok=True)
    return 1


def purge_call_journals(calls_dir, gone: dict) -> int:
    """Remove the journals of the calls made in this chat (<VOICE_CALLS_DIR>/<call_id>.json, named by the chat's
    call-ended lines)."""
    n = 0
    for m in gone.get("messages") or []:
        call_id = (m.get("meta") or {}).get("call_id") if (m.get("meta") or {}).get("kind") == "call_end" else None
        if not isinstance(call_id, str) or not _SAFE_NAME.fullmatch(call_id):
            continue
        f = Path(calls_dir) / f"{call_id}.json"
        if f.exists():
            f.unlink()
            n += 1
    return n


# --- facts learned in a chat ---------------------------------------------------------------------------------

def thread_facts(state_dir, memory_file, cid: str, onboarding: bool = False) -> list[dict]:
    """What the delete dialog lists: [{store, key, label, value}]. The onboarding chat's operator facts (name, devices,
    ...) are not offered: deleting it never resets onboarding."""
    from services.arturo import operator_store
    out = []
    if not onboarding:
        for field, value in sorted(operator_store.from_thread(state_dir, cid).items()):
            out.append({"store": "operator", "key": field, "label": field, "value": value})
    mem = _load_json(Path(memory_file)) or {}
    for n in mem.get("notes") or []:
        if isinstance(n, dict) and n.get("source_thread") == cid and n.get("note"):
            out.append({"store": "note", "key": str(n.get("created") or ""), "label": "note", "value": n["note"]})
    return out


def forget_facts(state_dir, memory_file, cid: str, onboarding: bool = False) -> int:
    """Remove exactly what thread_facts lists; returns how many."""
    from services.arturo import operator_store
    n = 0 if onboarding else len(operator_store.forget_thread(state_dir, cid))
    memory_file = Path(memory_file)
    mem = _load_json(memory_file)
    if mem and isinstance(mem.get("notes"), list):
        keep = [x for x in mem["notes"] if not (isinstance(x, dict) and x.get("source_thread") == cid)]
        if len(keep) != len(mem["notes"]):
            n += len(mem["notes"]) - len(keep)
            mem["notes"] = keep
            _replace(memory_file, json.dumps(mem, indent=2))
    return n


def log_delete(log_file, cid: str, who: str, forgot: int, now: float | None = None) -> None:
    """One line per delete: which chat, when, who, and how many facts went with it. Never its words or title."""
    entry = {"ts": datetime.fromtimestamp(now or time.time(), timezone.utc).isoformat(), "event": "thread_deleted",
             "thread": cid, "by": who or "unknown", "facts_forgotten": int(forgot)}
    append_jsonl(log_file, entry)
