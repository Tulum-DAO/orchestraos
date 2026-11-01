"""A call with an agent: the work runs in the agent's OWN session, the same path as a typed message, and its reply
is spoken back as it streams.

GPT-Live stays the voice only. When it delegates a turn on a call that has an agent (and the agent's switch is on),
the caller's words go to the agent through the gateway's /agent-message (what the dashboard's typed send uses), and
the agent's reply is read from its headless supervisor's event log as it streams, then spoken sentence by sentence
on the same delegation. Code blocks and tables are not read out ("I've put the details in the chat."). The first
0-3 s are silent (the dashboard shows the thinking dots); past the threshold, or when a tool call starts, ONE short
varied acknowledgement. A turn that runs long says so once and its words are spoken out of band as they come, with
no cut-off. While the call is live the seat's supervisor neither rotates nor restarts its CLI (the call marker).

The switch, per seat, off unless set: <data dir>/state/agent-calls.json
    {"seats": {"<seat>": {"on": true, "ack_after_s": 3.0}}}
Only a headless (streaming) seat can take it: a tmux seat has no reply stream, so its calls stay as they are.
Every turn's timings go to <data dir>/state/voice-calls/agent-turns.jsonl (what fired: silence, the
acknowledgement after the threshold, or at a tool's start).
"""
import json
import os
import re
import threading
import time
from pathlib import Path

ACK_AFTER_S = 3.0           # silent this long, then one acknowledgement (a setting per seat)
LONG_TURN_S = 20.0          # past this, the turn says it is taking a while and speaks the rest out of band
MAX_TURN_S = 30 * 60.0      # a turn followed for this long at most (no 90 s cut-off; a call ends long before)
POLL_S = 0.1
CALL_MARK = "call"          # beside the supervisor's log: a live call (the supervisor reads its mtime)
CALL_MARK_TOUCH_S = 10.0    # refreshed this often while the call runs; the supervisor counts it stale after 90 s

DETAILS = "I've put the details in the chat."
TAKING_A_WHILE = "This one's taking a while. I'll tell you as it comes in."
BUSY = "I'm in the middle of something; I'll pick that up right after."
NO_COMMANDS = "I can't run commands by voice. Type it in the chat and I'll take it from there."

#: Short, varied, never the same twice running.
ACKS = ("One sec.", "Let me check.", "On it.", "Looking now.", "Give me a moment.", "Checking that.")
_ack_lock = threading.Lock()
_last_ack: dict = {}


def data_dir(orch_dir=None) -> Path:
    return Path(orch_dir or os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra"))


_switch_cache = {"key": None, "doc": {}}


def _switch(orch_dir=None) -> dict:
    p = data_dir(orch_dir) / "state" / "agent-calls.json"
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _switch_cache["key"] != key:
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            doc = {}
        _switch_cache.update(key=key, doc=doc if isinstance(doc, dict) else {})
    seats = _switch_cache["doc"].get("seats")
    return seats if isinstance(seats, dict) else {}


def seat_conf(seat, orch_dir=None) -> dict:
    row = _switch(orch_dir).get(str(seat or ""))
    return row if isinstance(row, dict) else {}


def ack_after_s(seat, orch_dir=None) -> float:
    try:
        v = float(seat_conf(seat, orch_dir).get("ack_after_s", os.environ.get("ARTURO_AGENT_ACK_AFTER_S", ACK_AFTER_S)))
    except (TypeError, ValueError):
        v = ACK_AFTER_S
    return max(0.5, min(30.0, v))


def headless_seat(seat, orch_dir=None, include_dev=False):
    try:
        from scripts import headless_switch as HS
        return HS.headless_seat(seat, orch_dir=orch_dir, include_dev=include_dev)
    except Exception:  # noqa: BLE001 — unreadable: not streaming
        return None


def enabled(seat, orch_dir=None) -> bool:
    """The agent's own session takes this call's turns: its switch is on and it is a streaming (headless) seat. A seat
    in Dev counts: its call is the agent's own, and each turn asks it to go back to chat first (in_dev)."""
    if not seat or seat_conf(seat, orch_dir).get("on") is not True:
        return False
    return bool(headless_seat(seat, orch_dir, include_dev=True))


DEV_LINE = "I'm open in the terminal right now. Tap Back to chat and I'll take this myself."


def in_dev(seat, orch_dir=None) -> bool:
    """The seat's TUI has the pane (Dev): it is never forced out of it; the caller is asked to tap Back to chat."""
    try:
        from scripts import headless_switch as HS
        return bool(HS.in_dev(seat, orch_dir=orch_dir))
    except Exception:  # noqa: BLE001
        return False


def display_name(seat, orch_dir=None) -> str:
    try:
        reg = json.loads((data_dir(orch_dir) / "registry.json").read_text()).get("agents") or {}
        row = reg.get(seat) or {}
        name = str(row.get("display_name") or row.get("name") or "").strip()
        return name or str(seat)
    except Exception:  # noqa: BLE001
        return str(seat)


def next_ack(seat) -> str:
    with _ack_lock:
        last = _last_ack.get(seat)
        i = (ACKS.index(last) + 1) % len(ACKS) if last in ACKS else int(time.time()) % len(ACKS)
        _last_ack[seat] = ACKS[i]
        return ACKS[i]


STARTING = "I'm just starting up, so this first one may take a few seconds."


def warm_line(seat, orch_dir=None, status=None):
    """Said once, at the call's first turn, when the seat isn't idle-and-warm (its CLI up and booted); else None.
    A seat mid-turn says so through the held path instead."""
    try:
        if status is None:
            from scripts import headless_switch as HS
            status = HS.status(seat, orch_dir=orch_dir)
    except Exception:  # noqa: BLE001 — no supervisor answering: the send itself says so
        return None
    boot = status.get("boot") or {}
    up = bool(status.get("pid")) and status.get("state") in ("idle", "working", "waiting")
    booted = boot.get("booted", True)          # a supervisor older than the warm start: no boot report, assume up
    return None if (up and booted) else STARTING


def page_context_tag(page) -> str:
    """The API's quiet page line, exactly (the API's contract: POST /api/agents/:id/send page_context): the agent gets it
    on the same line as the words, and the transcript hides it."""
    if not isinstance(page, dict):
        return ""
    title, path = str(page.get("title") or "").strip(), str(page.get("path") or "").strip()
    if not (title or path):
        return ""
    return (f"<page-context>The person is looking at this page in the dashboard as they send this: {title} ({path}). "
            f"Use it only if they ask about the page; do not mention it otherwise.</page-context>")


def deliver(seat, text, status=None):
    """The typed-message path: the gateway's /agent-message, as the person's own words (a person's call, never an
    automation actor). A headless seat mid-turn queues them for its CLI's next turn: "busy" says so."""
    from scripts import gateway_deliver
    busy = False
    try:
        if status is None:
            from scripts import headless_switch as HS
            status = HS.status(seat)
        busy = status.get("state") in ("working", "waiting")
    except Exception:  # noqa: BLE001 — unknown: the send says what it can
        pass
    r = gateway_deliver.deliver(seat, text, source="person", accepts_held=True, actor="operator:voice-call")
    return {**r, "busy": busy and r.get("ok")}


# --- speech from a streaming reply -------------------------------------------------------------------------------
_SENT_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKS = re.compile(r"(\*\*|__|\*|`|~~)")


def _plain(s: str) -> str:
    s = _LINK.sub(r"\1", s)
    s = re.sub(r"^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)", "", s)
    return _MARKS.sub("", s).strip()


class Speech:
    """Feed the reply's text deltas; get back what to say, one complete sentence at a time. A fenced code block or a
    table becomes DETAILS (once per reply); markdown marks are dropped."""

    def __init__(self):
        self.raw = ""
        self.fence = False
        self.prose = ""
        self.details_said = False

    def _details(self, out):
        if not self.details_said:
            self.details_said = True
            out.append(DETAILS)

    def _prose_sentences(self, final=False):
        out = []
        while True:
            m = _SENT_END.search(self.prose)
            if not m:
                break
            sent, self.prose = self.prose[:m.start()], self.prose[m.end():]
            sent = _plain(sent)
            if sent:
                out.append(sent)
        if final and self.prose.strip():
            sent = _plain(self.prose)
            self.prose = ""
            if sent:
                out.append(sent)
        return out

    def _line(self, line, out):
        s = line.strip()
        if s.startswith("```"):
            if not self.fence:
                out += self._prose_sentences(final=True)
                self._details(out)
            self.fence = not self.fence
            return
        if self.fence:
            return
        if s.startswith("|"):
            out += self._prose_sentences(final=True)
            self._details(out)
            return
        if not s:
            out += self._prose_sentences(final=True)        # a paragraph ends a sentence
            return
        if re.match(r"^\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s)", line):
            out += self._prose_sentences(final=True)        # a heading or list item stands on its own
            self.prose = line.strip() + " "
            return
        self.prose += line.strip() + " "

    def feed(self, delta: str) -> list:
        self.raw += delta or ""
        out: list = []
        while "\n" in self.raw:
            line, self.raw = self.raw.split("\n", 1)
            self._line(line, out)
        # the open last line: its finished sentences can go now (never inside a fence or a table row)
        tail = self.raw
        if tail and not self.fence and not tail.lstrip().startswith(("|", "```", "`")):
            m = None
            for m in _SENT_END.finditer(tail):
                pass
            if m is not None:
                self.prose += tail[:m.end()]
                self.raw = tail[m.end():]
                out += self._prose_sentences()
        return out

    def flush(self) -> list:
        out: list = []
        if self.raw:
            line, self.raw = self.raw, ""
            self._line(line, out)
        if not self.fence:
            out += self._prose_sentences(final=True)
        return out


# --- the seat's event log ----------------------------------------------------------------------------------------
class EventTail:
    """New lines of the supervisor's events.jsonl, from where it stood when the turn began."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.pos = self.path.stat().st_size
        except OSError:
            self.pos = 0
        self._part = b""

    def read(self) -> list:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.pos)
                data = fh.read()
        except OSError:
            return []
        self.pos += len(data)
        data = self._part + data
        lines = data.split(b"\n")
        self._part = lines.pop()                    # a line still being written
        out = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
        return out


def events_path(seat, orch_dir=None) -> Path:
    return data_dir(orch_dir) / "state" / "headless" / seat / "events.jsonl"


def _content_text(c) -> str:
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c if isinstance(c, str) else ""


def classify(rec: dict):
    """('echo', text) | ('text', delta) | ('tool', name) | ('end', None) | None for one log record (main chain only)."""
    if rec.get("kind") != "out":
        return None
    ev = rec.get("ev") or {}
    t = ev.get("type")
    if t == "user" and ev.get("isReplay"):
        return "echo", _content_text((ev.get("message") or {}).get("content"))
    if t == "result":
        return "end", None
    if t == "stream_event" and ev.get("parent_tool_use_id") is None:
        e = ev.get("event") or {}
        if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
            return "text", (e.get("delta") or {}).get("text") or ""
        if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "tool_use":
            return "tool", (e.get("content_block") or {}).get("name") or ""
    return None


# --- one delegated turn ------------------------------------------------------------------------------------------
def run_turn(seat, question, *, deliver, tail, say, say_out_of_band, page=None, ack_after=None, ended=lambda: False,
             clock=time.monotonic, sleep=time.sleep, long_turn_s=LONG_TURN_S, max_turn_s=MAX_TURN_S):
    """The caller's words into the agent's own session; its reply spoken as it streams. `say(text)` speaks on the
    delegation, `say_out_of_band(text)` at the next pause. Returns the turn's record (timings and what fired)."""
    t0 = clock()
    rec = {"seat": seat, "ack": None, "tool_started": False, "first_speech_s": None, "turn_s": None,
           "long": False, "outcome": None, "spoken": ""}
    q = (question or "").strip()
    spoken: list = []

    def speak(text, *, out_of_band=False):
        if not text:
            return
        if rec["first_speech_s"] is None and text not in ACKS and text not in (TAKING_A_WHILE, BUSY, STARTING, DEV_LINE):
            rec["first_speech_s"] = round(clock() - t0, 2)
        spoken.append(text)
        (say_out_of_band if out_of_band else say)(text)

    if not q:
        rec["outcome"] = "empty"
        return rec
    if q.startswith("/"):
        speak(NO_COMMANDS)
        rec["outcome"] = "slash_refused"
        return rec
    tag = page_context_tag(page)
    text = f"{q} {tag}" if tag else q
    r = deliver(seat, text)
    if not r.get("ok"):
        speak(f"I couldn't get that to my session ({r.get('reason') or 'it refused'}).")
        rec["outcome"] = "not_delivered"
        return rec
    if r.get("held") or r.get("busy"):
        speak(BUSY)
        rec["ack"] = "busy"
    ack_after = ACK_AFTER_S if ack_after is None else ack_after
    words = Speech()
    started = done = False
    while not done and not ended():
        for ev in tail.read():
            kind = classify(ev)
            if kind is None:
                continue
            what, val = kind
            if not started:
                if what == "echo" and val.strip() == text.strip():
                    started = True
                continue
            if what == "echo":
                continue
            if what == "text":
                for s in words.feed(val):
                    speak(s, out_of_band=rec["long"])
            elif what == "tool":
                rec["tool_started"] = True
                if rec["ack"] is None and rec["first_speech_s"] is None:
                    rec["ack"] = "tool"
                    speak(next_ack(seat))
            elif what == "end":
                done = True
                break
        if done:
            break
        el = clock() - t0
        if rec["ack"] is None and rec["first_speech_s"] is None and el >= ack_after:
            rec["ack"] = "threshold"
            speak(next_ack(seat))
        if not rec["long"] and el >= long_turn_s:
            rec["long"] = True
            speak(TAKING_A_WHILE)
        if el >= max_turn_s:
            rec["outcome"] = "followed_max"
            break
        sleep(POLL_S)
    for s in words.flush():
        speak(s, out_of_band=rec["long"])
    rec["turn_s"] = round(clock() - t0, 2)
    rec["outcome"] = rec["outcome"] or ("answered" if done else "call_ended")
    if rec["ack"] is None:
        rec["ack"] = "silence"          # the reply's first words came inside the threshold
    rec["spoken"] = " ".join(spoken)
    return rec


def record_turn(rec, cid, orch_dir=None):
    """One line per turn (counts per 20 turns: silence / acknowledgement after the threshold / at a tool's start)."""
    p = data_dir(orch_dir) / "state" / "voice-calls" / "agent-turns.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        row = {k: v for k, v in rec.items() if k != "spoken"}
        with open(p, "a") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cid": cid, **row}) + "\n")
    except OSError:
        pass


def counts(orch_dir=None, last=20) -> dict:
    """What fired over the last `last` turns: {"turns", "silence", "threshold", "tool", "busy"}."""
    p = data_dir(orch_dir) / "state" / "voice-calls" / "agent-turns.jsonl"
    try:
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()][-last:]
    except (OSError, ValueError):
        rows = []
    out = {"turns": len(rows), "silence": 0, "threshold": 0, "tool": 0, "busy": 0}
    for r in rows:
        if r.get("ack") in out:
            out[r["ack"]] += 1
    return out


# --- the live-call marker the supervisor reads ------------------------------------------------------------------
def mark_call(seat, cid, orch_dir=None):
    d = data_dir(orch_dir) / "state" / "headless" / seat
    try:
        if d.is_dir():
            (d / CALL_MARK).write_text(json.dumps({"cid": cid, "at": time.time()}))
    except OSError:
        pass


def touch_call(seat, orch_dir=None):
    try:
        os.utime(data_dir(orch_dir) / "state" / "headless" / seat / CALL_MARK)
    except OSError:
        pass


def clear_call(seat, cid=None, orch_dir=None):
    p = data_dir(orch_dir) / "state" / "headless" / seat / CALL_MARK
    try:
        if cid is not None:
            cur = json.loads(p.read_text()).get("cid")
            if cur not in (None, cid):
                return                    # another call holds it now
        p.unlink()
    except (OSError, ValueError):
        pass

