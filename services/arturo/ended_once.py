"""P6 fix — per-call "Voice call ended" once-ledger (fold-in msg_9b31854d, build-and-hold).

One real call could reach gm as "Voice call ended" up to 3x: the watchdog/server-journal
finalize, the client durable inject (re-notify/sweeper), and a second ASR-fork server shard.
Each path has per-JOURNAL guards; nothing spanned journals belonging to the same CALL. This
ledger is that cross-journal guard: a tiny flock-serialized JSON map {call_key: claimed_ts}
next to the voice journals. claim() is an atomic test-and-set consulted immediately before
inject_to_gm; release() rolls a claim back when the inject FAILS so the retry path still
delivers (success-tied semantics — a claim only sticks when the inject landed).

Key = the journal's conv_id when stamped (A.1 — shared by the client journal and every server
shard of the same call), else the journal's own call_id (legacy traffic: no cross-journal
linkage exists, behavior unchanged). Flag ARTURO_ENDED_ONCE=1; unset/0 = guard_enabled()
False and the proxy call sites skip the ledger entirely (today's behavior, byte-identical).
"""
import fcntl
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("arturo-ended-once")

FLAG = "ARTURO_ENDED_ONCE"
TTL_DAYS = 7


def guard_enabled():
    return os.environ.get(FLAG, "") == "1"


def call_key(journal_dict):
    """The cross-journal identity of the CALL this journal belongs to."""
    return journal_dict.get("conv_id") or journal_dict.get("call_id") or ""


def _locked(path, fn):
    """Run fn(ledger_dict) -> (new_dict_or_None, result) under an exclusive flock on a stable
    sidecar lock inode (rename-safe: the lock file is never replaced, per fleet doctrine)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            new, result = fn(data)
            if new is not None:
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(json.dumps(new))
                os.replace(tmp, path)
            return result
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def claim(key, ledger_path):
    """Atomic test-and-set: True exactly once per key (until released). Prunes old entries
    opportunistically. Fails OPEN (True) on any ledger error — a broken ledger must degrade
    to today's behavior (a possible duplicate), never to a swallowed transcript."""
    if not key:
        return True
    try:
        def _fn(data):
            cutoff = time.time() - TTL_DAYS * 86400
            data = {k: v for k, v in data.items() if v >= cutoff}
            if key in data:
                return data, False
            data[key] = time.time()
            return data, True
        return _locked(ledger_path, _fn)
    except Exception as e:
        log.error(f"ended-once claim error (fail-open): {e}")
        return True


def release(key, ledger_path):
    """Roll back a claim after a FAILED inject so the retry path can deliver."""
    if not key:
        return
    try:
        def _fn(data):
            data.pop(key, None)
            return data, None
        _locked(ledger_path, _fn)
    except Exception as e:
        log.error(f"ended-once release error: {e}")
