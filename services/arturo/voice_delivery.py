"""voice_delivery.py -- speak async tool results back into a live call (gm msg_b0b4228c).

the operator (apr_4ec0ca94 write-in): "it can't walk and chew gum at the same time". deep_query /
ask_gm already answer instantly and run gm_command in the background, but the result only
ever went to Telegram. Behind ARTURO_VOICE_RESULTS=1 the result is offered here first and
spoken through the relay (Hume assistant_input) when the gates allow:

  (a) the operator has not spoken for user_quiet_s AND Arturo is not speaking (relay.try_speak);
  (b) a short lead-in names the question, so it is not mistaken for the current topic;
  (c) call ended, or the result is older than max_age_s -> Telegram instead;
  (d) at most one result per call in flight; the next waits for the previous one's audio.

Exactly one channel per result: voice OR Telegram, never both, never neither. A result too
long to speak is spoken in short form and the full text goes to Telegram, and Arturo says so.
"""
import logging
import re
import threading
import time

log = logging.getLogger("arturo-voice-delivery")

SPEAK_CAP_CHARS = 600


def _topic(question, cap=80):
    q = " ".join((question or "").split())
    q = re.sub(r"^(deep dive|task)\s*:\s*", "", q, flags=re.IGNORECASE)
    if len(q) > cap:
        q = q[:cap].rsplit(" ", 1)[0] + "..."
    return q


def _short(text, cap=SPEAK_CAP_CHARS):
    t = " ".join((text or "").split())
    if len(t) <= cap:
        return t, False
    cut = t[:cap]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > cap // 3 else cut.rsplit(" ", 1)[0] + "..."), True


def spoken_form(question, text):
    """(spoken_text, also_telegram_full)."""
    body, cut = _short(text)
    line = f"About your question on {_topic(question)}: {body}"
    if cut:
        line += " I've texted you the full answer."
    return line, cut


class VoiceResultDelivery:
    def __init__(self, relay, user_quiet_s=2.5, max_age_s=180.0, tick_s=0.5,
                 clock=time.time, start_thread=True):
        self.relay = relay
        self.user_quiet_s = user_quiet_s
        self.max_age_s = max_age_s
        self.tick_s = tick_s
        self.clock = clock
        self._q = {}                 # cid -> [item, ...] in arrival order
        self._running = {}           # cid -> {token: (summary, started_ts)}  (the operator apr_e18bde55)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self._start_thread = start_thread

    def started(self, cid, summary):
        """Background work began for this call. Returns a token for finished()."""
        if not cid:
            return None
        tok = object()
        with self._lock:
            self._running.setdefault(cid, {})[tok] = (summary, self.clock())
        return tok

    def finished(self, cid, tok):
        if not cid or tok is None:
            return
        with self._lock:
            r = self._running.get(cid) or {}
            r.pop(tok, None)
            if not r:
                self._running.pop(cid, None)

    def context_note(self, cid, now=None):
        """the operator (apr_e18bde55): acknowledge at once, especially when work is already running.
        A per-turn system note so Arturo knows what is in flight for THIS call."""
        if not cid:
            return ""
        now = self.clock() if now is None else now
        with self._lock:
            running = list((self._running.get(cid) or {}).values())
            ready = [it["question"] for it in (self._q.get(cid) or [])]
        if not running and not ready:
            return ""
        lines = ["--- BACKGROUND WORK ON THIS CALL ---"]
        for summary, t0 in running:
            lines.append(f"- RUNNING for {int(now - t0)}s: {_topic(summary)}")
        for q in ready:
            lines.append(f"- ANSWER READY, will be spoken in the next pause: {_topic(q)}")
        lines.append("If the operator asks about any of this, acknowledge immediately in one short sentence: "
                     "say it is still running (or about to be read out) and that you will tell him "
                     "the moment it lands. Do NOT start the same work again. Otherwise just answer "
                     "what he is asking now.")
        return "\n".join(lines)

    def submit(self, cid, question, text, telegram_fn):
        """Offer a finished result. telegram_fn() delivers the full Telegram copy; it is called
        exactly once unless the result is spoken in full."""
        if not cid:
            telegram_fn()
            return "telegram:no-call"
        item = {"cid": cid, "question": question, "text": text, "tg": telegram_fn,
                "ts": self.clock()}
        with self._lock:
            self._q.setdefault(cid, []).append(item)
        self._ensure_thread()
        self._wake.set()
        return "queued"

    def _ensure_thread(self):
        if not self._start_thread:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="voice-result-delivery")
            self._thread.start()

    def _run(self):
        while True:
            self._wake.wait(self.tick_s)
            self._wake.clear()
            try:
                if not self.tick():
                    with self._lock:
                        if not self._q:
                            self._thread = None
                            return
            except Exception as e:  # a gate bug must not strand results: next tick retries
                log.error(f"voice-result tick failed: {e!r}")

    def tick(self, now=None):
        """One pass: for each call, only its OLDEST result is considered (gate d). Returns
        True while anything is still queued."""
        now = self.clock() if now is None else now
        with self._lock:
            heads = [(cid, items[0]) for cid, items in self._q.items() if items]
        for cid, it in heads:
            outcome = self._try(it, now)
            if outcome is None:
                continue
            with self._lock:
                items = self._q.get(cid) or []
                if items and items[0] is it:
                    items.pop(0)
                if not items:
                    self._q.pop(cid, None)
            log.info(f"voice-result {cid[:8]}: {outcome} ({_topic(it['question'], 40)!r})")
        with self._lock:
            return bool(self._q)

    def _try(self, it, now):
        """None = keep waiting; else the final outcome string."""
        if now - it["ts"] > self.max_age_s:
            it["tg"]()
            return "telegram:stale"
        line, also_tg = spoken_form(it["question"], it["text"])
        r = self.relay.try_speak(it["cid"], line, user_quiet_s=self.user_quiet_s, now=now)
        if r == "gone":
            it["tg"]()
            return "telegram:call-ended"
        if r == "spoken":
            if also_tg:
                it["tg"]()
                return "voice+telegram-full"
            return "voice"
        return None
