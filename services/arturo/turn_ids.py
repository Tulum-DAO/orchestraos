"""One operator send runs at most once, and the reply shown is THAT send's (DEC-1791518421640932).

The client mints a turn id per send and sends the SAME id on every attempt of it: the stream, the
whole-reply fallback, each busy retry, and the read-back. The server keeps, per (conversation, turn id):

  * a REQUEST registry, in memory: each request holding the id registers at first sight (queued), is
    running once it holds the conversation's lock, and leaves in its own finally. A duplicate request
    never downgrades or clears another's entry.
  * the FINAL body, in memory (15 min, 500 entries): what the turn answered, cards and pairing code
    included. Never persisted: a pairing code must not reach disk.
  * a MARK, in the thread store: 'started' before anything runs, 'recorded' with the turn's own reply
    (in record_turn's transaction), deleted when the turn ended having done nothing.

After the lock, `decide` answers in this order: recorded -> replay; a final body -> replay; a mark that
is still 'started' (a restart, or a body that expired) -> lost, never re-run; no mark -> run.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid

TURN_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
FINAL_TTL_S = 15 * 60
FINAL_CAP = 500
BOOT = uuid.uuid4().hex[:12]          # this process; on the mark for diagnostics only


def valid(turn_id):
    return isinstance(turn_id, str) and bool(TURN_ID_RE.fullmatch(turn_id))


def owner(principal, raw_text):
    """Who sent which words: the raw request `text` field as received, marker and preamble included, the
    same on /text and /text/stream, so a stream re-asked over /text is still the same send."""
    return hashlib.sha256(f"{principal or ''}\n{raw_text or ''}".encode()).hexdigest()[:16]


class Registry:
    def __init__(self, ttl_s=FINAL_TTL_S, cap=FINAL_CAP, clock=time.monotonic):
        self._mu = threading.Lock()
        self._reqs = {}               # (cid, tid) -> {request token: "queued" | "running"}
        self._finals = {}             # (cid, tid) -> (at, status, body, principal, owner)
        self._recorded = set()        # (cid, tid) recorded by THIS process during the turn
        self._ttl, self._cap, self._clock = ttl_s, cap, clock

    # --- requests ---------------------------------------------------------------------------------
    def register(self, cid, tid):
        tok = uuid.uuid4().hex
        with self._mu:
            self._reqs.setdefault((cid, tid), {})[tok] = "queued"
        return tok

    def promote(self, cid, tid, tok):
        with self._mu:
            entry = self._reqs.get((cid, tid))
            if entry is not None and tok in entry:
                entry[tok] = "running"

    def drop(self, cid, tid, tok):
        with self._mu:
            entry = self._reqs.get((cid, tid))
            if entry is None:
                return
            entry.pop(tok, None)
            if not entry:
                del self._reqs[(cid, tid)]

    def live(self, cid, tid):
        with self._mu:
            return bool(self._reqs.get((cid, tid)))

    # --- outcomes ---------------------------------------------------------------------------------
    def note_recorded(self, cid, tid):
        with self._mu:
            self._recorded.add((cid, tid))

    def was_recorded(self, cid, tid):
        with self._mu:
            return (cid, tid) in self._recorded

    def put_final(self, cid, tid, status, body, principal, own):
        with self._mu:
            self._prune()
            self._finals[(cid, tid)] = (self._clock(), status, body, principal, own)
            self._recorded.discard((cid, tid))

    def forget_recorded(self, cid, tid):
        with self._mu:
            self._recorded.discard((cid, tid))

    def final(self, cid, tid):
        with self._mu:
            self._prune()
            hit = self._finals.get((cid, tid))
            return None if hit is None else hit[1:]

    def _prune(self):
        now = self._clock()
        for k in [k for k, v in self._finals.items() if now - v[0] > self._ttl]:
            del self._finals[k]
        while len(self._finals) > self._cap:
            del self._finals[min(self._finals, key=lambda k: self._finals[k][0])]


def decide(store, registry, cid, tid, own):
    """After the conversation's lock is held. Returns ("run", None) | ("replay", (status, body)) |
    ("conflict", None) | ("lost", None). Raises when the thread store cannot be read."""
    mark = store.get_mark(cid, tid)
    if mark is not None and mark["owner"] != own:
        return "conflict", None
    fin = registry.final(cid, tid)
    if fin is not None and fin[3] != own:
        return "conflict", None
    if mark is not None and mark["state"] == "recorded":
        if fin is not None:
            return "replay", (fin[0], fin[1])
        return "replay", (200, {"ok": True, "reply_text": mark.get("reply", ""), "conversation_id": cid,
                                "tools_called": [], "spawned": [], "recorded_only": True})
    if fin is not None:
        return "replay", (fin[0], fin[1])
    if mark is not None:
        return "lost", None
    return "run", None


def readback(store, registry, cid, tid, principal):
    """GET /threads/<cid>?turn=<tid>: {state[, result]}. `result` only to the principal that ran it."""
    if registry.live(cid, tid):
        return {"state": "running"}
    try:
        mark = store.get_mark(cid, tid)
    except Exception:  # noqa: BLE001 — the client keeps waiting on an unreadable store, never re-sends
        return {"state": "running"}
    fin = registry.final(cid, tid)
    if fin is not None:
        status, body, who, _own = fin
        out = {"state": "done"}
        if who == principal:
            out["result"] = {**body, "status": status}
        return out
    if mark is not None and mark["state"] == "recorded":
        out = {"state": "done"}
        if mark.get("principal") == principal:
            out["result"] = {"ok": True, "status": 200, "reply_text": mark.get("reply", ""),
                             "conversation_id": cid, "tools_called": [], "spawned": [], "recorded_only": True}
        return out
    if mark is not None:
        return {"state": "lost"}
    return {"state": "unknown"}
