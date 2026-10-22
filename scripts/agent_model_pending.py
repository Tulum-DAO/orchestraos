"""Deferred model switches (the operator, 2026-10-22: "switching a busy agent is fine").

A model switch is typed into an agent's pane (`/model`, or Codex's picker), and typing it into a
WORKING agent queues it: it would run after the switcher restored the host's default model and
let go of its lock, so the CLI would rewrite that default unwatched. So a switch asked for while
the agent is busy is RECORDED here instead, and the gateway applies it the moment the agent is
idle, through the same locked path an idle switch takes. Nothing is ever typed into a turn.

One pending switch per tmux session; a newer pick replaces it, picking the current model cancels
it. The result of every deferred switch (applied, or failed with a reason) is kept as the
session's `last`, so a client can say "switched at 13:42" or "could not switch: <reason>".
The file survives a gateway restart.
"""
import fcntl
import hashlib
import json
import math
import re
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

#: A switch still waiting this long, with the agent still busy, is reported once (never dropped).
STALE_AFTER_S = 15 * 60


def state_path(orch_dir):
    return Path(os.environ.get("AGENT_MODEL_PENDING_FILE")
                or Path(orch_dir) / "state" / "agent-model-pending.json")


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class StateUnreadable(Exception):
    """The record exists but cannot be read. Never treated as empty: a write after that would
    replace every pending pick and outcome with nothing (codex R2 on #475)."""


class LockBusy(Exception):
    """Another switch for this agent (in this or another gateway process) still holds its lock
    after the caller's bounded wait. Nothing was read or changed."""


def _read(path):
    """The record; empty only when the file does not exist yet."""
    try:
        raw = Path(path).read_text()
    except FileNotFoundError:
        return {"pending": {}, "last": {}}
    except OSError as e:
        raise StateUnreadable(f"{path}: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise StateUnreadable(f"{path}: not JSON ({e})") from e
    if not isinstance(data, dict) or not isinstance(data.get("pending", {}), dict) \
            or not isinstance(data.get("last", {}), dict):
        raise StateUnreadable(f"{path}: not a pending-switch record")
    pending, last = dict(data.get("pending") or {}), dict(data.get("last") or {})
    for session, e in pending.items():
        if not _valid_pending(e):
            raise StateUnreadable(f"{path}: pending entry for {session!r} is malformed")
    for session, e in last.items():
        if not _valid_last(e):
            raise StateUnreadable(f"{path}: last entry for {session!r} is malformed")
    return {"pending": pending, "last": last}


def _valid_pending(e):
    """Every field a pending pick is read by: a malformed one is a damaged record, never data."""
    return (isinstance(e, dict) and isinstance(e.get("model"), str) and e["model"]
            and isinstance(e.get("since"), str) and isinstance(e.get("since_ts"), (int, float))
            and not isinstance(e.get("since_ts"), bool) and math.isfinite(e["since_ts"])
            and isinstance(e.get("sids"), list) and all(isinstance(x, str) for x in e["sids"])
            and isinstance(e.get("reported", False), bool)
            and all(e.get(k) is None or isinstance(e.get(k), str) for k in ("by", "from")))


def _valid_last(e):
    return (isinstance(e, dict) and isinstance(e.get("model"), str) and isinstance(e.get("at"), str)
            and e.get("outcome") in ("switched", "failed")
            and (e.get("reason") is None or isinstance(e.get("reason"), str)))


#: The record's own lock is held only for one read-modify-write; a writer waits at most this long
#: for it, then raises LockBusy with nothing written (codex R14 on #475).
RECORD_LOCK_WAIT_S = 5.0


def _flock(fh, timeout, busy):
    """flock(LOCK_EX) on `fh`: blocking when `timeout` is None, else polled up to `timeout`
    seconds, then LockBusy(busy)."""
    if timeout is None:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockBusy(busy)
            time.sleep(0.05)


@contextmanager
def _locked(path, timeout=None):
    """The whole read-modify-write under one flock, written by temp + rename. The wait is bounded
    (RECORD_LOCK_WAIT_S unless given): past it LockBusy, nothing read or written (codex R14)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lk:
        _flock(lk, RECORD_LOCK_WAIT_S if timeout is None else timeout,
               f"{path}: the record is locked by another writer")
        data = _read(path)
        yield data
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)


def put(path, session, model, *, current=None, sids=(), by=None, now=None):
    """Record (or replace) the session's pending switch. Returns the entry."""
    ts = time.time() if now is None else now
    entry = {"model": model, "from": current, "since": _iso(ts), "since_ts": ts,
             "sids": [s for s in sids if s], "by": by}
    with _locked(path) as data:
        data["pending"][session] = entry
    return entry


def cancel(path, session):
    """Drop the session's pending switch (the operator picked the model it is already on).
    Returns the dropped entry, or None."""
    with _locked(path) as data:
        return data["pending"].pop(session, None)


def get(path, session):
    return _read(path)["pending"].get(session)


def last(path, session):
    return _read(path)["last"].get(session)


def pending_sessions(path):
    return dict(_read(path)["pending"])


def view(entry, now=None):
    """The client's form of a pending entry: {model, since, stale}."""
    if not entry:
        return None
    ts = time.time() if now is None else now
    since_ts = entry.get("since_ts")
    since_ts = ts if since_ts is None else float(since_ts)      # 0 is a time, not "missing"
    return {"model": entry.get("model"), "since": entry.get("since"),
            "stale": ts - since_ts >= STALE_AFTER_S}


def resolve(path, session, since, outcome, *, model=None, reason=None, now=None):
    """Close the pending switch recorded at `since` with outcome 'switched' or 'failed'. A switch
    the operator replaced meanwhile (a different `since`) is left alone: returns False."""
    ts = time.time() if now is None else now
    with _locked(path) as data:
        cur = data["pending"].get(session)
        if not cur or cur.get("since") != since:
            return False
        data["pending"].pop(session, None)
        rec = {"model": model or cur.get("model"), "at": _iso(ts), "outcome": outcome}
        if reason:
            rec["reason"] = reason
        data["last"][session] = rec
        return True


def mark_reported(path, session, since):
    """Remember that a stale switch was reported, so it is reported once."""
    with _locked(path) as data:
        cur = data["pending"].get(session)
        if cur and cur.get("since") == since:
            cur["reported"] = True
            return True
        return False


@contextmanager
def session_lock(path, session, timeout=None):
    """ONE lock per tmux session for every model-switch decision, across threads AND processes (an
    old gateway still finishing a pass while a new one starts; codex R12 on #475): an flock on a
    file next to the record, opened afresh per acquisition so threads of one process exclude each
    other too. Held across the read, the identity check, the switch and the resolve.

    `timeout` bounds the wait (seconds): past it LockBusy is raised and nothing ran. Every gateway
    caller passes one, so no executor thread waits on another process forever and a shutdown that
    joins its threads never hangs on a lock (codex R13 on #475). None waits without a bound."""
    name = str(session)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"
    if safe != name or safe.startswith("."):
        safe = f"{safe}~{hashlib.sha256(name.encode()).hexdigest()[:12]}"
    d = Path(path).with_name(Path(path).name + ".locks")
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{safe}.lock", "a") as fh:
        _flock(fh, timeout, f"another model switch for {name} is still in progress")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
