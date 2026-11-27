"""Steered sends: a person's words the gateway typed into a RUNNING Claude Code turn (outcome "delivered-steer").

The CLI holds such a message in its own queue until the turn reaches a boundary. Send-now (POST /agent-send-now)
submits it at once with the CLI's own chord, and needs to know which message the person means: this ledger is that
memory. One row per delivered-steer send: {id, session, text (exactly as typed, so a long message is its chip-dodge
banner), at}. The id goes back on the /agent-message 200 as message_id.

The rows hold a person's words, so: the file is 0600, it is capped by count AND age (MAX_ROWS, MAX_AGE_S), a row is
dropped once its message is sent now or seen leaving the queue, and nothing here logs text. Pure over a data dir
passed on every call (never a path frozen at import), so tests drive it with a temp dir.
"""
import fcntl
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager

LEDGER = ("state", "steered-sends.jsonl")
MAX_ROWS = 200
MAX_AGE_S = 24 * 3600
ID_PREFIX = "stq_"


def ledger_path(data_dir):
    return os.path.join(str(data_dir), *LEDGER)


def valid_id(message_id) -> bool:
    """The shape record() mints: stq_ + 16 hex. Anything else is never looked up."""
    s = message_id if isinstance(message_id, str) else ""
    return (s.startswith(ID_PREFIX) and len(s) == len(ID_PREFIX) + 16
            and all(c in "0123456789abcdef" for c in s[len(ID_PREFIX):]))


@contextmanager
def _locked(data_dir):
    path = ledger_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and valid_id(row.get("id")):
            out.append(row)
    return out


def _write(path, rows):
    """Atomic and 0600 from creation: a temp file in the same directory, then rename."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".steered-", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(rows, now):
    fresh = [r for r in rows if now - float(r.get("at") or 0) <= MAX_AGE_S]
    return fresh[-MAX_ROWS:]


def record(data_dir, session, text, now=None):
    """Keep one steered send; returns its id."""
    now = time.time() if now is None else now
    mid = ID_PREFIX + secrets.token_hex(8)
    with _locked(data_dir) as path:
        rows = _prune(_read(path), now)
        rows.append({"id": mid, "session": str(session), "text": str(text), "at": now})
        _write(path, _prune(rows, now))
    return mid


def get(data_dir, message_id, now=None):
    """The row for this id, or None (unknown, malformed, or older than MAX_AGE_S)."""
    if not valid_id(message_id):
        return None
    now = time.time() if now is None else now
    for r in _read(ledger_path(data_dir)):
        if r["id"] == message_id and now - float(r.get("at") or 0) <= MAX_AGE_S:
            return r
    return None


def drop(data_dir, message_id, now=None):
    """Forget a row (its message was sent now, or left the queue). Prunes on the way."""
    now = time.time() if now is None else now
    with _locked(data_dir) as path:
        rows = _read(path)
        keep = _prune([r for r in rows if r["id"] != message_id], now)
        if keep != rows:
            _write(path, keep)


def _set_chorded(data_dir, message_id, value):
    with _locked(data_dir) as path:
        rows = _read(path)
        hit = False
        for r in rows:
            if r["id"] == message_id:
                hit = True
                if value is None:
                    r.pop("chorded_at", None)
                else:
                    r["chorded_at"] = value
        if not hit:
            raise OSError("no such steered send")
        _write(path, rows)


def mark_chorded(data_dir, message_id, now=None):
    """The send-now keys are about to go for this message: a later send-now never sends them again. Raises OSError
    when the mark cannot be written (the caller then sends nothing)."""
    _set_chorded(data_dir, message_id, time.time() if now is None else now)


def unmark_chorded(data_dir, message_id):
    """The keys never reached the pane: the message may be sent now again."""
    try:
        _set_chorded(data_dir, message_id, None)
    except OSError:
        pass
