"""call_threads.py — a voice call lives INSIDE an Arturo thread, not beside it.

Before this, a dashboard call's lines existed only in the open pane (and in the call journal): close the pane,
come back, and even the call still in progress had lost what was said; the next typed turn had no idea a call
happened. Now the browser names its thread when the call starts (the bridge's X-Arturo-Thread), and every line
the caller SEES become final is archived into that thread as it happens: the operator's words, Arturo's spoken
words (as spoken: cut off with "--", never the unsaid rest), then the call's end marker (the card's grammar).

Hooked on the relay's event buffer, so the thread holds exactly what the browser was shown, in order. The
`on_line` callback lets the proxy keep its in-memory brain history in step (a turn on this thread, typed or
spoken, then sees the call). Never raises into the call: a lost line is logged, the call goes on.
"""
import logging
import re
import threading

log = logging.getLogger("arturo.call_threads")

THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


def valid_thread_id(tid):
    return isinstance(tid, str) and bool(THREAD_ID_RE.match(tid))


class CallThreads:
    def __init__(self, store, on_line=None, end_marker=None):
        self.store = store
        self.on_line = on_line              # (thread_id, role, brain_content) -> None
        self.end_marker = end_marker        # (cid) -> (text, meta) for the call's end row
        self._lock = threading.Lock()
        self._by_call = {}                  # relay cid -> thread id
        self._by_thread = {}                # thread id -> relay cid (the live call in that thread)
        self._principal = {}                # relay cid -> who started it (the thread's owner if it is new)

    def bind(self, cid, thread_id, principal=None):
        """First audio chunk of a call that named its thread. Later chunks are no-ops; a call never moves."""
        if not cid or not valid_thread_id(thread_id):
            return False
        with self._lock:
            if cid in self._by_call:
                return self._by_call[cid] == thread_id
            self._by_call[cid] = thread_id
            self._by_thread[thread_id] = cid
            self._principal[cid] = principal
        log.info(f"call {cid[:8]} is in thread {thread_id[:24]}")
        return True

    def thread(self, cid):
        with self._lock:
            return self._by_call.get(cid)

    def call(self, thread_id):
        """The live call in this thread, or None."""
        with self._lock:
            return self._by_thread.get(thread_id)

    def _write(self, cid, thread_id, role, text, meta=None):
        try:
            seq = self.store.append_message(thread_id, role, text, mode="voice", meta=meta,
                                            principal=self._principal.get(cid))
            if seq is None:
                log.warning(f"call {cid[:8]}: a line was not archived to {thread_id[:24]}")
            if self.on_line is not None:
                from services.arturo.thread_store import brain_content
                self.on_line(thread_id, role, brain_content(role, text, "voice", meta))
        except Exception as e:  # noqa: BLE001 — the archive is never worth breaking a call
            log.warning(f"call {cid[:8]}: archiving a line failed: {e!r}")

    def observe(self, cid, ev):
        """One relay event, as the browser will get it. Only FINAL lines are archived."""
        thread_id = self.thread(cid)
        if thread_id is None or not isinstance(ev, dict):
            return
        t = ev.get("type")
        text = (ev.get("text") or "").strip()
        if not text:
            return
        if t == "user_transcript":
            self._write(cid, thread_id, "user", text)
        elif t == "agent_response" and not ev.get("partial") and not ev.get("echo"):
            # `echo`: the voice reading out a reply the thread already holds (a typed turn's answer).
            self._write(cid, thread_id, "assistant", text)

    def end(self, cid):
        """The call ended: its end marker (the call card) closes the stretch, and the thread is free."""
        with self._lock:
            thread_id = self._by_call.pop(cid, None)
            if thread_id is not None and self._by_thread.get(thread_id) == cid:
                del self._by_thread[thread_id]
        if thread_id is None:
            return False
        if self.end_marker is not None:
            try:
                text, meta = self.end_marker(cid)
                self._write(cid, thread_id, "assistant", text, meta=meta)
            except Exception as e:  # noqa: BLE001
                log.warning(f"call {cid[:8]}: end marker failed: {e!r}")
        with self._lock:
            self._principal.pop(cid, None)
        return True
