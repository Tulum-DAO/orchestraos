"""One turn at a time per conversation (DEC-1791511578959986 W1).

Two tabs, or a phone and a laptop, can send into one conversation at once. Each turn reads the
history and the turn count, takes the conversation's cards off the book, and records its result:
two at once work from the same stale history and race the card book. So a turn takes its
conversation's lock BEFORE any of that, and the code that ENDS the turn releases it, on whatever
thread that is (/text: the request; /text/stream: the heartbeat's pump thread, which runs the whole
turn, fallback included, even after the client has gone).

A plain Lock (an RLock may only be released by its owner thread), behind a token whose release()
is idempotent and safe from any thread. Different conversations never wait for each other.
"""
from __future__ import annotations

import threading

# A second turn in a busy conversation answers 409 "busy" almost at once; the client waits and resends.
# A long wait here outlived the client's own deadline (20 s to the response head), and its fallback
# re-sent the same message, which then ran twice (#312 review B1).
WAIT_S = 1.5


class TurnToken:
    def __init__(self, locks, key, lock):
        self._locks, self._key, self._lock = locks, key, lock
        self._done = False
        self._guard = threading.Lock()
        self.on_release = None

    def release(self):
        """Once, from any thread. `on_release` (the turn's own bookkeeping: store its outcome, settle its turn
        mark) runs first, while the lock is still held, so the next request to take it sees a finished turn;
        a callback that raises still releases the lock (DEC-1791518421640932 v5.3)."""
        with self._guard:
            if self._done:
                return
            self._done = True
        try:
            if self.on_release is not None:
                self.on_release()
        except Exception:  # noqa: BLE001 — never leave a conversation locked over its bookkeeping
            import logging
            logging.getLogger("arturo.turn_lock").exception("turn on_release failed")
        finally:
            self._lock.release()
            self._locks._unref(self._key)

    @property
    def released(self):
        return self._done


class TurnLocks:
    def __init__(self):
        self._mu = threading.Lock()
        self._locks = {}            # key -> [lock, users]

    def acquire(self, key, timeout=WAIT_S):
        """A token once this conversation is free, or None after `timeout` seconds."""
        key = key or ""
        with self._mu:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        if not entry[0].acquire(timeout=timeout):
            self._unref(key)
            return None
        return TurnToken(self, key, entry[0])

    def _unref(self, key):
        with self._mu:
            entry = self._locks.get(key)
            if entry is None:
                return
            entry[1] -= 1
            if entry[1] <= 0:
                del self._locks[key]

    def busy(self, key):
        with self._mu:
            entry = self._locks.get(key or "")
        return bool(entry and entry[0].locked())
