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
    def __init__(self, store, on_line=None, end_marker=None, tool_turns=None):
        self.store = store
        self.on_line = on_line              # (thread_id, role, brain_content) -> None
        self.end_marker = end_marker        # (cid) -> (text, meta) for the call's end row
        self.tool_turns = tool_turns        # (cid, turns) -> the call's tool runs kept outside its journal (archive)
        self._lock = threading.Lock()
        self._by_call = {}                  # relay cid -> thread id
        self._by_thread = {}                # thread id -> relay cid (the live call in that thread)
        self._principal = {}                # relay cid -> who started it (the thread's owner if it is new)
        self.archived = (0, False)          # archive's last call: (lines that landed, whether its card landed)

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

    def _write(self, cid, thread_id, role, text, meta=None, ts=None, principal=None):
        """True when the line landed in the thread."""
        try:
            seq = self.store.append_message(thread_id, role, text, mode="voice", meta=meta,
                                            principal=principal or self._principal.get(cid), ts=ts)
            if seq is None:
                log.warning(f"call {cid[:8]}: a line was not archived to {thread_id[:24]}")
            if self.on_line is not None:
                from services.arturo.thread_store import brain_content
                self.on_line(thread_id, role, brain_content(role, text, "voice", meta))
            return seq is not None
        except Exception as e:  # noqa: BLE001 — the archive is never worth breaking a call
            log.warning(f"call {cid[:8]}: archiving a line failed: {e!r}")
            return False

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

    def archive(self, cid, thread_id, turns, principal=None):
        """A call that was never bound live (the dashboard's native GPT-Live engine journals client-side): once it
        has ended, its lines and its end marker go into its thread at once, as the live path would have written
        them (the operator's words as "user", Arturo's as "assistant", mode "voice", then the call card). `turns`
        are call-journal turns ({role: user|arturo|tool|surface, text, ts}). Every tool the call ran is a line too,
        where it happened (call_tool_lines: the journal's own tool turns, and the runs `tool_turns` finds outside
        it); a surface turn is not a line anyone saw. Like the live path, the card is written even for a call with
        no lines. Never raises. What landed is left in `archived`: a write can fail without raising."""
        self.archived = (0, False)
        if not cid or not valid_thread_id(thread_id):
            return False
        landed = 0
        from services.arturo import call_tool_lines
        turns = [t for t in turns or () if isinstance(t, dict)]
        if self.tool_turns is not None:
            # The source of truth for the call's tool runs; a journal's own tool turns come from whichever server
            # journal the merge linked to it (a time-window fallback can link another call's), so they are dropped.
            turns = [t for t in turns if t.get("role") != "tool"]
            try:
                turns = call_tool_lines.merge(turns, self.tool_turns(cid, turns))
            except Exception as e:  # noqa: BLE001 — the call's words still go in
                log.warning(f"call {cid[:8]}: its tool runs could not be read: {e!r}")
        for t in turns:
            if t.get("role") == "tool":
                try:
                    ts = call_tool_lines._ts(t)
                    landed += self._write(cid, thread_id, "assistant", call_tool_lines.line_for(t), ts=ts, principal=principal,
                                meta={"kind": call_tool_lines.KIND, "tool": str(t.get("tool") or t.get("name") or "tool"),
                                      "ok": call_tool_lines.line_ok(t), "tool_call_id": t.get("tool_call_id") or ""})
                except Exception as e:  # noqa: BLE001
                    log.warning(f"call {cid[:8]}: a tool line failed: {e!r}")
                continue
            role = {"user": "user", "arturo": "assistant"}.get(t.get("role"))
            text = (t.get("text") or "").strip() if isinstance(t.get("text"), str) else ""
            if role is None or not text:
                continue
            ts = t.get("ts")
            ts = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0 else None
            landed += self._write(cid, thread_id, role, text, ts=ts, principal=principal)
        card = False
        if self.end_marker is not None:
            try:
                text, meta = self.end_marker(cid)
                card = self._write(cid, thread_id, "assistant", text, meta=meta, principal=principal)
            except Exception as e:  # noqa: BLE001
                log.warning(f"call {cid[:8]}: end marker failed: {e!r}")
        self.archived = (landed, card)
        return True
