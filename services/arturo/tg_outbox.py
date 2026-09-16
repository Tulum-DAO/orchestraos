# tg_outbox.py — BUG-1 (P0): hard cap + dedupe on Arturo's outbound Telegram path.
#
# Live call vc_2fb009f82028b2f3: the model looped send_telegram 26x in one call — mostly the SAME
# "[Forwarded to GM]" text replayed 20+ times — fanning a single quick lookup into ~73 texts to
# the operator. A trivial request must NEVER spam. This is a defense-in-depth OUTBOUND guard (independent
# of prompt discipline): dedupe near-identical messages within a window, and hard-cap the number of
# sends in a rolling window. Single-user system → module-global limiter is correct.
import threading
import time as _time
import re


def _norm(msg):
    s = (msg if isinstance(msg, str) else str(msg)).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s[:200]


class TelegramOutbox:
    def __init__(self, max_per_window=6, window_s=60.0, dedupe_s=300.0):
        # max_per_window sends allowed per rolling window_s; an identical (normalized) message is
        # blocked for dedupe_s regardless of the rate cap. Defaults: >6 texts/min OR any repeat
        # within 5 min is almost certainly a loop, not intent.
        self.max_per_window = max_per_window
        self.window_s = window_s
        self.dedupe_s = dedupe_s
        self._sent_ts = []               # timestamps of allowed sends (rolling window)
        self._last_by_hash = {}          # normalized message -> last allowed ts
        self._lock = threading.Lock()

    def allow(self, message, now=None):
        """Return (allowed: bool, reason: str). Call BEFORE actually POSTing to Telegram; only POST
        when allowed. Records the send internally when allowed."""
        now = now if now is not None else _time.time()
        h = _norm(message)
        with self._lock:
            # dedupe: identical recent message → block (the [Forwarded to GM] replay)
            last = self._last_by_hash.get(h)
            if last is not None and (now - last) < self.dedupe_s:
                return False, "duplicate"
            # prune the rolling window
            self._sent_ts = [t for t in self._sent_ts if now - t < self.window_s]
            if len(self._sent_ts) >= self.max_per_window:
                return False, "rate-cap"
            # allow + record
            self._sent_ts.append(now)
            self._last_by_hash[h] = now
            # opportunistic cleanup of stale dedupe entries
            if len(self._last_by_hash) > 256:
                self._last_by_hash = {k: v for k, v in self._last_by_hash.items()
                                      if now - v < self.dedupe_s}
            return True, "ok"
