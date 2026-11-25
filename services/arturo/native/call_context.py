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
import os
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


def budget_tokens():
    try:
        return max(500, min(int(os.environ.get("ARTURO_NATIVE_CONTEXT_TOKENS") or thread_context.DEFAULT_BUDGET_TOKENS),
                            20000))
    except ValueError:
        return thread_context.DEFAULT_BUDGET_TOKENS


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


def opening(agent, load=None, directory=None, route=None):
    """The opening context text, or "" (flag off, or nothing could be read): the API route first, the twin if the
    API cannot answer."""
    if not enabled():
        return ""
    try:
        text = (route or fetch_route)(agent, budget_tokens())
        if text and text.strip():
            return text
    except Exception as e:  # noqa: BLE001 — the twin reads the same sources
        log.info(f"native: call-context route for {agent!r} unavailable ({e!r}); using the twin")
    try:
        return (load or thread_context.load)(chat_agent=agent, call_owner=None, calls_dir=directory or calls_dir(),
                                             budget_tokens=budget_tokens())
    except Exception as e:  # noqa: BLE001 — a call without its thread is still a call
        log.warning(f"native: opening context for {agent!r} failed: {e!r}")
        return ""


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
