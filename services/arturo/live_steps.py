"""What Arturo is doing right now, one short line per seat, for the dashboard's working blurb.

Written to ORCHESTRA_DIR/realtime/steps.json ({"seats": {"<seat>": {"text", "ts"}}}), beside telemetryd's
realtime/status.json; the API's status stream watches it and pushes `event: step` to every open page, so
the line arrives over the live connection instead of being polled. This module is the file's only writer;
the API only reads it.

The line is a plain phrase per tool ("Reading a file", "Messaging dev-client-a"), never the tool's raw
arguments: a query, a path or a command can hold anything. The only argument ever shown is an agent's id,
and only when it looks like one.

At most one write per seat every MIN_GAP_S. A step inside that window waits, and the latest one is written
when the window ends, so a burst of steps shows its last step rather than its first.
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", _data_dir()))
DEFAULT_PATH = ORCHESTRA_DIR / "realtime/steps.json"
MIN_GAP_S = 0.5
MAX_TEXT = 120

_AGENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_AGENT_KEYS = ("agent", "agent_id", "seat", "target", "name")
# {agent} is filled only with a value that looks like an agent id; otherwise the phrase without it is used.
_PHRASES = {
    "read_file": "Reading a file",
    "run_command": "Running a command",
    "research": "Researching",
    "deep_query": "Searching",
    "deep_think": "Thinking it through",
    "knowledge": "Checking what it knows",
    "look_at_image": "Looking at an image",
    "read_screen_context": "Looking at the page",
    "focus_entity": "Opening a page",
    "list_agents": "Checking the agents",
    "get_agent_output": ("Reading {agent}", "Reading an agent's output"),
    "read_agent_conversation": ("Reading {agent}'s conversation", "Reading an agent's conversation"),
    "inject_message": ("Messaging {agent}", "Messaging an agent"),
    "agent_message": ("Messaging {agent}", "Messaging an agent"),
    "spawn_agent": ("Starting {agent}", "Starting an agent"),
    "kill_agent": ("Stopping {agent}", "Stopping an agent"),
    "ask_gm": "Asking the GM",
    "gm_command": "Asking the GM",
    "send_telegram": "Sending a text",
    "query_roadmap": "Checking the roadmap",
    "client_briefing": "Preparing a client briefing",
    "read_thread": "Reading the conversation",
    "list_jobs": "Checking background jobs",
    "cancel_job": "Stopping a background job",
    "async_task": "Starting a background task",
    "remember_note": "Saving a note",
    "set_operator_fact": "Saving a note",
    "pair_device": "Pairing a device",
    "check_paired": "Checking a pairing",
    "ask_choices": "Preparing choices",
    "answer_screen_card": "Answering a card",
    "answer_menu": "Answering a menu",
    "create_starter_team": "Setting up a team",
    "decline_starter_team": "Setting up",
    "finish_onboarding": "Finishing setup",
}


def step_text(name, args=None):
    """A short, safe line for a tool step."""
    name = str(name or "tool")
    phrase = _PHRASES.get(name)
    if isinstance(phrase, tuple):
        with_agent, without = phrase
        agent = None
        if isinstance(args, dict):
            for k in _AGENT_KEYS:
                v = args.get(k)
                if isinstance(v, str) and _AGENT_RE.match(v):
                    agent = v
                    break
        phrase = with_agent.format(agent=agent) if agent else without
    if not phrase:
        phrase = name.replace("_", " ").strip().capitalize() or "Working"
    return phrase[:MAX_TEXT]


class LiveSteps:
    def __init__(self, path=DEFAULT_PATH, clock=time.time, min_gap=MIN_GAP_S, timer=threading.Timer):
        self.path = Path(path)
        self.clock = clock
        self.min_gap = min_gap
        self._timer = timer
        self._lock = threading.Lock()
        self._last = {}       # seat -> when its line was last written
        self._pending = {}    # seat -> the line waiting for the window to end
        self._timers = {}     # seat -> the timer that writes it

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
            seats = d.get("seats") if isinstance(d, dict) else None
            return seats if isinstance(seats, dict) else {}
        except Exception:
            return {}

    def _write(self, seat, text):
        seats = self._read()
        seats[seat] = {"text": text, "ts": self.clock()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seats": seats}))
            tmp.replace(self.path)
        except OSError:
            pass                                      # display only: never breaks the turn
        self._last[seat] = self.clock()

    def step(self, seat, text):
        text = str(text or "")[:MAX_TEXT]
        with self._lock:
            wait = self.min_gap - (self.clock() - self._last.get(seat, -1e9))
            if wait <= 0 and seat not in self._timers:
                self._write(seat, text)
                return
            self._pending[seat] = text                # the latest step wins when the window ends
            if seat not in self._timers:
                t = self._timer(max(wait, 0.0), self._flush, args=(seat,))
                t.daemon = True
                self._timers[seat] = t
                t.start()

    def _flush(self, seat):
        with self._lock:
            self._timers.pop(seat, None)
            text = self._pending.pop(seat, None)
            if text is not None:
                self._write(seat, text)

    def clear(self, seat):
        """The turn (or job) is over: no current step. Written at once, and a waiting step is dropped."""
        with self._lock:
            t = self._timers.pop(seat, None)
            if t is not None:
                t.cancel()
            self._pending.pop(seat, None)
            if (self._read().get(seat) or {}).get("text"):
                self._write(seat, "")


_default = LiveSteps()


def step(seat, name, args=None):
    _default.step(seat, step_text(name, args))


def clear(seat):
    _default.clear(seat)
