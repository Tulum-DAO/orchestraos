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
import urllib.parse
import urllib.request
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


def mode(seat, orch_dir=None) -> str:
    """How a call with this agent runs. "voice" (the default): GPT-Live answers naturally AS the agent, with its context,
    and work it is asked for is commissioned into the agent's own session. "forced": every caller line goes to the
    agent's own session and GPT-Live only reads its replies (agent-calls.json "mode": "forced")."""
    return "forced" if seat_conf(seat, orch_dir).get("mode") == "forced" else "voice"


VOICE_CONTEXT_CHARS = 8000      # the agent's recent conversation handed to the call (GPT-Live's instructions, the CLM)
_TAIL_BYTES = 400_000
_TAG_RE = re.compile(r"<(page-context|system-reminder|voice-call-live|voice-call-said)>[\s\S]*?</\1>")
# The reaction wake (api lib/reaction-wake-text.ts): one line, the tag and either note (the current one or the one
# older logs carry). The agent reads it; nobody said it, so the mirror never notes it.
_WAKE_LINE_RE = re.compile(r'^[ \t]*<reaction from="user:[^"<>\n]*" emoji="[^"<>\n]*" on="[^"<>\n]*"/>'
                           r'(?:[ \t]*(?:You may reply, react \(`orchestra react`\), or do nothing\.'
                           r'|Reply, react with `orchestra react <emoji>`, or answer exactly NO_REPLY to stay silent\.))?[ \t]*$',
                           re.M)
# What an agent answers to a wake when it has nothing to say (api reaction-wake-text.ts silentReply).
_NO_REPLY_RE = re.compile(r"^\(?\s*no (reply|response)( needed| required)?\s*\)?\.?$", re.I)


def quiet_reply(text) -> bool:
    """NO_REPLY, a "no reply" phrase, or one emoji: an answer to a reaction wake that is not a message (never said)."""
    t = (text or "").strip()
    if not t:
        return False
    if t == "NO_REPLY" or _NO_REPLY_RE.match(t):
        return True
    return len(t) <= 16 and not re.search(r"[\w\s]", t) and any(ord(ch) > 0x7F for ch in t)


def session_log(seat, orch_dir=None):
    """The seat's own Claude session file (headless seat.json: session_id + cwd), or None."""
    try:
        st = json.loads((data_dir(orch_dir) / "state" / "headless" / str(seat) / "seat.json").read_text())
        sid, cwd = str(st.get("session_id") or ""), str(st.get("cwd") or "")
    except (OSError, ValueError, AttributeError):
        return None
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid) or not cwd:
        return None
    root = Path(os.path.expanduser("~/.claude/projects"))
    p = root / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{sid}.jsonl"
    if p.is_file():
        return p
    return next(iter(sorted(root.glob(f"*/{sid}.jsonl"))), None)      # a project dir named some other way


def _log_lines(path, tail_bytes=_TAIL_BYTES):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        raw = f.read()
    lines = raw.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > tail_bytes else lines        # a cut first line is never parsed


def voice_context(seat, orch_dir=None, max_chars=VOICE_CONTEXT_CHARS) -> str:
    """The agent's recent conversation as plain lines ("Operator: ..." / "<name>: ..."), newest last, at most
    max_chars (the oldest dropped first). Text only: no tool calls or results, no side chains, no meta or command
    lines, no hidden page tags. Any failure: "" (the call still runs, without the context)."""
    try:
        path = session_log(seat, orch_dir)
        if path is None or not path.is_file():
            return ""
        name = display_name(seat, orch_dir)
        out = []
        for line in _log_lines(path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            role = d.get("type")
            if role not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
                continue
            text = _content_text((d.get("message") or {}).get("content"))
            text = _TAG_RE.sub("", text).strip()
            # the operator's own words only: no command/tag lines (<...>), no mail or relayed lines ([Arturo Voice]:,
            # [Voice call]:, [from gm] ...), no compaction summaries
            if not text or text.startswith("<") or (role == "user" and (text.startswith("[")
                                                                        or text.startswith("This session is being continued"))):
                continue
            out.append(f"{'Operator' if role == 'user' else name}: {' '.join(text.split())}")
        kept, n = [], 0
        for row in reversed(out):
            if n + len(row) + 1 > max_chars:
                break
            kept.append(row)
            n += len(row) + 1
        return "\n".join(reversed(kept))
    except Exception:  # noqa: BLE001 — context is best effort, never the call
        return ""


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
            if self.path.stat().st_size < self.pos:      # truncated or replaced: read the new file from its start
                self.pos, self._part = 0, b""
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


# --- the agent's own chat during a "voice"-mode call ------------------------------------------------------------
MIRROR_SAY_MAX = 1200
VOICE_CALL_TAG = "[Voice call]:"


# A request the operator SAID on a "voice"-mode call, handed to the agent's own session (stream_relay _oa_agent_turn):
# the agent knows its reply is spoken as it writes it. The chat hides the line (the call's card holds it); the agent's
# reply stays in the chat. One line, like the page tag.
VOICE_SAID_TAG = ("<voice-call-said>The operator SAID this to you on a live voice call. Your reply is spoken to them "
                  "as you write it: answer in short spoken sentences first, then put any details, lists or code in the "
                  "chat. Do the work they ask for yourself, as you would for a typed message.</voice-call-said>")


def typed_path(seat, orch_dir=None) -> Path:
    """What the API hands a live "voice"-mode call the moment the operator sends a message in the agent's chat
    (api agent-send.ts noteTypedOnCall): {"at", "text"} lines, beside the call marker."""
    return data_dir(orch_dir) / "state" / "headless" / str(seat) / "typed.jsonl"


def typed_note(name, text) -> str:
    return (f"The operator just SENT this in your chat during the call (typed, not said aloud): \"{text[:600]}\". It "
            "is on their screen now. React to it out loud right away, naturally, in a few words (for example that it "
            "just came through); answer it only if it asks you something. If it needs work or a written answer, your "
            "chat side takes it and you will be given what it sends back.")


# --- the agent's own reactions during a "voice"-mode call (spec "Voice calls") --------------------------------------
REACTION_STREAM_TIMEOUT_S = 75.0     # the API pings every 25 s; silence past this means the stream is gone
REACTION_RETRY_S = 3.0
REACTION_ON_MAX = 120


def api_base() -> str:
    """The Node API: $ORCH_API_URL (orchestra child_env), else loopback on $ORCHESTRA_API_PORT (8888)."""
    url = (os.environ.get("ORCH_API_URL") or "").strip()
    if url:
        return url.rstrip("/")
    return f"http://127.0.0.1:{os.environ.get('ORCHESTRA_API_PORT') or 8888}"


def message_text(agent, ref, base=None, limit=300) -> str:
    """The text of the message `ref` names in the agent's chat (the API's transcript items carry server refs), or ""
    when it is not among the newest `limit` items, has no text, or the API cannot be read."""
    url = f"{base or api_base()}/api/agents/{urllib.parse.quote(str(agent), safe='')}/transcript?limit={int(limit)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            doc = json.loads(resp.read() or b"{}")
    except Exception:  # noqa: BLE001 — a note without the words, never a failure
        return ""
    for it in reversed(doc.get("items") or []):
        if isinstance(it, dict) and it.get("ref") == ref:
            return str(it.get("text") or "")
    return ""


def reaction_note(name, emoji, on="") -> str:
    """What the voice is told when the agent reacts in its chat (`orchestra react`): background, never read out."""
    said = " ".join(str(on or "").split())
    if len(said) > REACTION_ON_MAX:
        said = said[:REACTION_ON_MAX - 3] + "..."
    to = f" to '{said}'" if said else ""
    return (f"{name} reacted {emoji}{to} in the chat. It is a reaction, not a reply, and it is on the operator's "
            "screen already. Do not read it out or describe it; just carry on.")


class ReactionTail:
    """The API's reaction events for one agent's chat (GET /api/reactions/stream?surface=agent-chat&conv=<agent>),
    read on a daemon thread and reopened when the stream ends. read() returns what arrived since the last read; an
    agent's new reaction carries "on", the reacted message's text (looked up by ref). close() stops it."""

    def __init__(self, agent, base=None, retry_s=REACTION_RETRY_S):
        self.agent = str(agent)
        self.base = (base or api_base()).rstrip("/")
        self.retry_s = retry_s
        self._q: list = []
        self._lock = threading.Lock()
        self._closed = False
        self._resp = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"reactions-{self.agent[:16]}")
        self._thread.start()

    def _url(self):
        return f"{self.base}/api/reactions/stream?" + urllib.parse.urlencode({"surface": "agent-chat", "conv": self.agent})

    def _run(self):
        while not self._closed:
            try:
                with urllib.request.urlopen(self._url(), timeout=REACTION_STREAM_TIMEOUT_S) as resp:
                    self._resp = resp
                    event, data = None, []
                    for raw in resp:
                        if self._closed:
                            return
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if not line:
                            if event == "reaction" and data:
                                self._take("\n".join(data))
                            event, data = None, []
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
            except Exception:  # noqa: BLE001 — API restarting, network blip: reopen after a pause
                pass
            if not self._closed:
                time.sleep(self.retry_s)

    def _take(self, payload):
        try:
            ch = json.loads(payload)
        except ValueError:
            return
        if not isinstance(ch, dict):
            return
        who = ch.get("reactor")
        if ch.get("emoji") and isinstance(who, dict) and who.get("kind") == "agent":
            ch["on"] = message_text(self.agent, str(ch.get("ref") or ""), base=self.base)
        with self._lock:
            self._q.append(ch)

    def read(self) -> list:
        with self._lock:
            out, self._q = self._q, []
        return out

    def close(self):
        self._closed = True
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass


class ChatMirror:
    """What happens in the agent's own chat while GPT-Live is on a call as it: fed the seat's event
    log records, returns ("note", context) for something the operator typed there, and ("say", reply, context) when the
    agent answers it, or answers work commissioned on the call ("[Voice call]: ..."). Mail, the hand-off and other
    automation turns are not mirrored."""

    def __init__(self, name, agent=None):
        self.name = name
        self.agent = agent        # the seat id: only ITS reactions are noted (None: any agent's)
        self.kind = None          # "typed" | "commissioned" | None (a turn not mirrored)
        self.prompt = ""
        self.speech = None
        self.sents = []

    def _say(self, out):
        """The turn collected so far, said (at its end, or when a new message lands mid-turn); then ("done", prompt),
        whatever was said, so the relay can let go of a request it handed over itself."""
        if not self.kind:
            return
        prompt = self.prompt
        self._say_text(out)
        out.append(("done", prompt))

    def _say_text(self, out):
        self.sents += self.speech.flush()
        sents = [s for s in self.sents if s]
        self.kind_said, kind = self.kind, self.kind
        self.kind = None
        # whole sentences up to the cap (a cut inside one could split a secret before the relay scrubs it)
        kept, n = [], 0
        for s in sents:
            if kept and n + len(s) + 1 > MIRROR_SAY_MAX:
                kept.append("... the rest is in the chat.")
                break
            kept.append(s)
            n += len(s) + 1
        say = " ".join(kept).strip()
        if not re.search(r"\w", say) or quiet_reply(say):
            return                           # nothing to say: an empty reply, a 👍, or NO_REPLY (a reaction wake's answer)
        p = self.prompt[:300]
        prompt = self.prompt
        note = (f"You ({self.name}) just answered in your chat what the operator typed (\"{p}\"). The answer is on "
                "their screen now: say it briefly and naturally as your follow-up, in your own words, not word for "
                "word." if kind == "typed" else
                f"The work you started on this call (\"{p}\") just came back in your chat; the full result is on "
                "their screen. Tell them where it stands in a sentence or two, as news that just arrived (they did not "
                "just ask).")
        out.append(("say", say, note, prompt))

    def _start(self, text, out):
        self._say(out)                       # a message landing mid-turn: what was collected is still said first
        text = _WAKE_LINE_RE.sub("", _TAG_RE.sub("", text or "")).strip()   # a wake alone: a turn not mirrored
        self.kind, self.prompt, self.speech, self.sents = None, "", Speech(), []
        if text.startswith(VOICE_CALL_TAG):
            self.kind, self.prompt = "commissioned", text[len(VOICE_CALL_TAG):].strip()
        elif text and not text.startswith(("<", "[")):
            self.kind, self.prompt = "typed", text
            # the relay skips this when the API already handed it over on send (typed.jsonl): the third field matches
            out.append(("note", typed_note(self.name, text), text))

    def reaction(self, change) -> list:
        """A reaction in the agent's chat (ReactionTail): the agent's own new emoji becomes a silent note for the voice
        (never spoken; the emoji is on the operator's screen). The operator's reactions (the wake tells the agent),
        removals and other reactors: nothing."""
        if not isinstance(change, dict):
            return []
        who = change.get("reactor")
        if not isinstance(who, dict):
            return []
        if who.get("kind") != "agent" or not change.get("emoji"):
            return []
        if self.agent and who.get("id") != self.agent:
            return []
        return [("note", reaction_note(self.name, str(change["emoji"]), str(change.get("on") or "")))]

    def feed(self, records) -> list:
        out: list = []
        for rec in records or []:
            c = classify(rec)
            if c is None:
                continue
            k, v = c
            if k == "echo":
                self._start(v, out)
            elif k == "text" and self.kind:
                self.sents += self.speech.feed(v)
            elif k == "end":
                self._say(out)
        return out


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

