"""What a native call knows at start, and the chat that reaches it during the call (Shaw 2026-11-25: "you can't read
your own messages ... you can't even remember the last call"; one surface, text or voice).

  opening(agent)  the thread above this call: `agent`'s chat (the page the call was placed from; Arturo's dashboard
                  thread is gm's chat, 2026-10-20 ruling) merged with Arturo's own earlier calls, every line stamped,
                  newest verbatim within a token budget. From the API's GET /api/agents/:id/call-context (the full
                  redactor and this install's token), else its Python twin services/arturo/thread_context.py.
  ChatFeed        chat lines newer than the call's start, as stamped notes that each fit one session append.

Neither ever raises into a call: an unreadable source is an empty opening or no notes.
"""
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from services.arturo import thread_context

log = logging.getLogger("arturo.native.call_context")

FLAG = "ARTURO_NATIVE_THREAD_CONTEXT"          # ON unless "0": the escape hatch
FEED_KIND_ENV = "ARTURO_NATIVE_CHAT_FEED_KIND"  # thinking (default: voiced ~5 s after send, probe 2026-11-25
# native-90a7e00cd3d1, uplink audio flowing) | instructions (never completed in that probe) | commentary (untested with audio)
FEED_KINDS = ("instructions", "thinking", "commentary")
CHAT_POLL_S = 3.0
FEED_MAX_NOTES = 60                             # per call: a flood of chat never floods the session


def enabled():
    return os.environ.get(FLAG, "1") != "0"


# The opening's size on a native call. Shaw 2026-11-25 (gm msg_3a7542b2): a call opens with the last 20-25 turns across
# chat and calls, every one stamped, and that overrides the under-3 s greet bar. Measured on the real gm thread:
# 1500 tokens -> 15 verbatim turns, 2000 -> 22, 2500 -> 32, 4000 -> 59 (greet: 1500 -> 2.44 s, 3000 -> 3.01 s).
# When long chat replies leave the default short of MIN_TURNS, the opening is read once more at what MIN_TURNS cost
# at the first read's rate, capped at FLOOR_BUDGET_TOKENS (never the whole floor: ~59 turns on the gm thread).
DEFAULT_BUDGET_TOKENS = 2500
MIN_TURNS = 25
FLOOR_BUDGET_TOKENS = 4000
FLOOR_MAX_ELAPSED_S = 2.0   # a first read slower than this gets no second one (the server waits 6 s in all)
READ_DEADLINE_S = 5.0       # both reads end by this, inside the server's OPENING_WAIT_S (6 s), so a slow second read
#                             never costs the first; the second read gets what is left, from the route alone
SECOND_READ_MIN_S = 0.5     # less time left than this: no second read


def budget_tokens():
    try:
        return max(500, min(int(os.environ.get("ARTURO_NATIVE_CONTEXT_TOKENS") or DEFAULT_BUDGET_TOKENS), 20000))
    except ValueError:
        return DEFAULT_BUDGET_TOKENS


def default_chat_agent():
    return os.environ.get("ARTURO_NATIVE_CHAT_AGENT") or os.environ.get("VOICE_BRAIN_SESSION") or "gm"


def feed_kind():
    k = os.environ.get(FEED_KIND_ENV, "thinking")
    return k if k in FEED_KINDS else "thinking"


def calls_dir():
    env = os.environ.get("ARTURO_VOICE_CALLS_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra")) / "state" / "voice-calls"


def fetch_route(agent, budget, timeout=4.0):
    """GET the API's call-context for `agent`'s chat and Arturo's calls (loopback, as a process on this box)."""
    url = (f"{thread_context.api_base()}/api/agents/{urllib.parse.quote(str(agent), safe='')}/call-context"
           f"?calls=arturo&budget={int(budget)}")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


class Opening(str):
    """The opening text, and how it was read (`info`, for the call report):
      budget         the first read's token budget (0 when the flag is off)
      reread         the second read's budget, or None when there was no second read
      reread_result  kept (more turns, cut to the newest MIN_TURNS) | not_more | failed | None
      source         route | twin | none (nothing could be read) | off (flag off)
      short          still under MIN_TURNS verbatim turns"""

    def __new__(cls, text, info):
        self = super().__new__(cls, text)
        self.info = info
        return self


def opening(agent, load=None, directory=None, route=None):
    """The opening context (an Opening; "" when the flag is off or nothing could be read): the API route first, the
    twin if the API cannot answer. Short of MIN_TURNS at the default size, it is read once more, sized to reach
    MIN_TURNS (an explicit ARTURO_NATIVE_CONTEXT_TOKENS is taken as meant)."""
    if not enabled():
        return _OFF
    return _with_floor(lambda budget, timeout=None: _read(agent, budget, load, directory, route, timeout))


_SECOND_READ_SOURCES = ("route", "thread", "calls")   # not the twin: its transcript fetch would not fit the deadline
_OFF = Opening("", {"budget": 0, "reread": None, "reread_result": None, "source": "off", "short": False})


def _with_floor(read):
    """`read(budget, timeout=None)` -> (text, source); with a timeout it reads the route alone. The second read only
    when it can help and cannot cost the thread (Claude reviews of #638): something older exists to fetch (an EARLIER
    block), the first came quickly from the route or a local read (never the twin), and the second fits inside READ_DEADLINE_S. It asks for what
    MIN_TURNS cost at the first read's rate, capped at the floor; kept only with more turns, then cut to the newest
    MIN_TURNS. A junk setting is not an explicit one."""
    budget = budget_tokens()
    t0 = time.monotonic()
    text, source = read(budget)
    turns = describe(text)["turns"]
    reread = result = None
    elapsed = time.monotonic() - t0
    if (not _explicit_budget() and budget < FLOOR_BUDGET_TOKENS and source in _SECOND_READ_SOURCES
            and turns < MIN_TURNS
            and "EARLIER (summarized):" in text and elapsed <= FLOOR_MAX_ELAPSED_S
            and READ_DEADLINE_S - elapsed >= SECOND_READ_MIN_S):
        reread = min(FLOOR_BUDGET_TOKENS, math.ceil(budget * MIN_TURNS / max(turns, 1)))
        more, _ = read(reread, timeout=READ_DEADLINE_S - elapsed)
        more_turns = describe(more)["turns"]
        if not more:
            result = "failed"
        elif more_turns > turns:
            text, turns, result = _newest(more, MIN_TURNS), min(more_turns, MIN_TURNS), "kept"
        else:
            result = "not_more"
    return Opening(text, {"budget": budget, "reread": reread, "reread_result": result, "source": source,
                          "short": turns < MIN_TURNS})


def _newest(text, keep):
    """`text` with only its newest `keep` verbatim turns; the cut ones are counted at the end of EARLIER."""
    lines = text.splitlines()
    verbatim = [i for i, ln in enumerate(lines) if _LINE_RE.match(ln)]
    cut = verbatim[:max(0, len(verbatim) - keep)]
    if not cut:
        return text
    at = lines.index("RECENT (verbatim):")
    drop = set(cut)
    out = [ln for i, ln in enumerate(lines[:at]) if i not in drop]
    out.append(f"- ({len(cut)} older item(s) omitted)")
    out += [ln for i, ln in enumerate(lines[at:], start=at) if i not in drop]
    return "\n".join(out)


def _explicit_budget():
    try:
        return int(os.environ.get("ARTURO_NATIVE_CONTEXT_TOKENS") or "") > 0
    except ValueError:
        return False


def opening_thread(thread_id, load=None, principal="fleet"):
    """A call placed from the standalone Arturo page: that page's own thread (threads.db), never another chat. The
    operator, 2026-11-25 (gm msg_e7402cbb): "one unified conversation between text and call that this call is part
    of"; gm's chat stays reachable through the conversations tool."""
    if not enabled():
        return _OFF
    if not thread_context.thread_readable(thread_id, principal):
        log.warning(f"native: thread {thread_id!r} is not readable by {principal!r}; the call opens without it")
        return Opening("", {"budget": 0, "reread": None, "reread_result": None, "source": "refused",
                            "short": False})

    def read(budget, timeout=None):              # a local read: the deadline cannot be blown by it
        try:
            text = (load or thread_context.load_thread)(thread_id, calls_dir=calls_dir(), budget_tokens=budget)
            return (text, "thread") if text and text.strip() else ("", "none")
        except Exception as e:  # noqa: BLE001 — a call without its thread is still a call
            log.warning(f"native: opening for thread {thread_id!r} failed: {e!r}")
            return "", "none"
    return _with_floor(read)


def opening_calls(load=None):
    """A call that names neither a page thread nor an agent (an older dashboard): Arturo's own recent calls, no chat,
    so no other seat's conversation is read out as if it were the operator's."""
    if not enabled():
        return _OFF

    def read(budget, timeout=None):              # a local read: the deadline cannot be blown by it
        try:
            text = (load or thread_context.load)(chat_agent="arturo", call_owner=None, calls_dir=calls_dir(),
                                                 fetch=lambda a, n: [], budget_tokens=budget)
            return (text, "calls") if text and text.strip() else ("", "none")
        except Exception as e:  # noqa: BLE001
            log.warning(f"native: opening from Arturo's calls failed: {e!r}")
            return "", "none"
    return _with_floor(read)


def _read(agent, budget, load=None, directory=None, route=None, timeout=None):
    """(text, source): source is "route", "twin", or "none" when neither could be read. With a timeout (the second
    read) only the route is asked, within it: the twin's own transcript fetch and file reads would not fit."""
    try:
        text = (route or fetch_route)(agent, budget, timeout=4.0 if timeout is None else timeout)
        if text and text.strip():
            return text, "route"
    except Exception as e:  # noqa: BLE001 — the twin reads the same sources
        log.info(f"native: call-context route for {agent!r} unavailable ({e!r}); using the twin")
    if timeout is not None:
        return "", "none"
    try:
        text = (load or thread_context.load)(chat_agent=agent, call_owner=None, calls_dir=directory or calls_dir(),
                                             budget_tokens=budget)
        return (text, "twin") if text and text.strip() else ("", "none")
    except Exception as e:  # noqa: BLE001 — a call without its thread is still a call
        log.warning(f"native: opening context for {agent!r} failed: {e!r}")
        return "", "none"


# a verbatim line: "[<UTC>Z <local> <label>] chat|call <id> <who>: <words>"; a summary: "- [<stamp>] call <id>, ..."
_LINE_RE = re.compile(r"^\[(\S+Z) [^\]]*\] (chat|call (\S+)) ")
_SUMMARY_RE = re.compile(r"^- \[[^\]]*\] call ([^,\s]+),")
_OMITTED_RE = re.compile(r"^- \((\d+) older item\(s\) omitted\)$")
_CHAT_ERROR_RE = re.compile(r"^\(\S+ chat could not be read: ")


def describe(text):
    """What an opening carried, for the call report (so "he could not recall X" is answerable from the file): the
    verbatim turns, the summarized items, the first/last verbatim stamp (UTC), chat vs call lines, the call ids."""
    turns, summarized, chat_n, call_n, calls, chat_error = [], 0, 0, 0, set(), False
    for line in (text or "").splitlines():
        m = _OMITTED_RE.match(line)
        if m:
            summarized += int(m.group(1))
            continue
        if _CHAT_ERROR_RE.match(line):
            chat_error = True
            continue
        if line.startswith("- ["):
            summarized += 1
            m = _SUMMARY_RE.match(line)
            if m:
                calls.add(m.group(1))
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        turns.append(m.group(1))
        if m.group(2) == "chat":
            chat_n += 1
        else:
            call_n += 1
            calls.add(m.group(3))
    return {"turns": len(turns), "summarized": summarized, "first": turns[0] if turns else None,
            "last": turns[-1] if turns else None, "chat_lines": chat_n, "call_lines": call_n, "calls": sorted(calls),
            "chat_error": chat_error}


class ChatFeed:
    """Chat lines of `agent` newer than the call's start (and than the last poll), as stamped notes."""

    def __init__(self, agent, since=None, fetch=None, tz=None, tz_label=None, operator=None, max_notes=FEED_MAX_NOTES):
        self.agent = agent
        self.cursor = time.time() if since is None else since
        self.fetch = fetch or thread_context.fetch_transcript
        if tz is None or tz_label is None:
            tz, tz_label = thread_context.shaw_tz()
        self.tz, self.tz_label = tz, tz_label
        self.operator = operator or thread_context.operator_name()
        self.left = max_notes

    def poll(self):
        if self.left <= 0:
            return []
        try:
            items = self.fetch(self.agent, 50)
        except Exception as e:  # noqa: BLE001 — next poll tries again
            log.info(f"native: chat feed for {self.agent!r} unreadable: {e!r}")
            return []
        lines = thread_context.chat_lines(items, agent_label=self.agent, operator_label=self.operator,
                                          since=self.cursor)
        if not lines:
            return []
        self.cursor = max(l.at for l in lines)
        notes = []
        for ln in sorted(lines, key=lambda l: l.at):
            notes.extend(thread_context.live_notes(ln, self.tz, self.tz_label))
        notes = notes[: self.left]
        self.left -= len(notes)
        return notes


class ThreadFeed:
    """New TYPED operator turns of the page's own thread (threads.db) since the call started, as stamped notes. The
    call's own spoken lines and Arturo's replies are not news to the call."""

    def __init__(self, thread_id, db=None, since=None, tz=None, tz_label=None, operator=None, max_notes=FEED_MAX_NOTES,
                 principal="fleet"):
        self.thread_id = thread_id
        self.principal = principal
        self.db = db or thread_context.threads_db_path()
        self.cursor = time.time() if since is None else since
        if tz is None or tz_label is None:
            tz, tz_label = thread_context.shaw_tz()
        self.tz, self.tz_label = tz, tz_label
        self.operator = operator or thread_context.operator_name()
        self.left = max_notes

    def poll(self):
        if self.left <= 0:
            return []
        if not thread_context.thread_readable(self.thread_id, self.principal, self.db):
            return []
        try:
            entries = thread_context.thread_entries(self.thread_id, self.db, operator_label=self.operator)
        except Exception as e:  # noqa: BLE001 — next poll tries again
            log.info(f"native: thread feed for {self.thread_id!r} unreadable: {e!r}")
            return []
        new = [l for role, l in entries if role == "user" and l.channel == "chat" and l.at > self.cursor]
        if not new:
            return []
        self.cursor = max(l.at for l in new)
        notes = []
        for ln in sorted(new, key=lambda l: l.at):
            notes.extend(thread_context.live_notes(ln, self.tz, self.tz_label))
        notes = notes[: self.left]
        self.left -= len(notes)
        return notes