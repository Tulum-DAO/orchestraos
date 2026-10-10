"""Menu instance identity (DEC-1791405753559307, phase 0: emit only, nothing enforced).

A menu's identity used to be its question text, so two identical prompts were one menu at every
layer. The Claude CLI already mints a per-call id: every tool call fires PreToolUse with a
`tool_use_id`, an AskUserQuestion menu IS a tool call, and a permission prompt is raised for the
call whose PreToolUse just fired. state-event-hook.py keeps each pane's OPEN calls in
state/agent-events/calls/<pane>.json; this module picks the one call a menu on screen belongs to.

Rules (from 74 measured menus on Claude Code 2.1.284):
  - exactly ONE matching open call -> that call's id is the instance; zero or several -> None.
    Never guess: no instance only means "cannot be proven", which later phases treat explicitly.
  - STICKY per on-screen menu: the first decision (an id OR None) holds while the same menu is up,
    because clients use the instance as the card's identity and a change rebuilds the card.
  - ...except that an id whose call has CLOSED (PostToolUse dropped it) is never served again: answer A then identical B within one poll gets B's new id.
  - an AskUserQuestion id stays the instance across its whole tab walk and review screen while
    that call is open, even though the review screen's question matches no part.
Non-Claude runtimes (agy, codex) fire no such hook, so their menus carry no instance.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time

import sys
# The ONE data-dir default is orchestra_cli.settings.data_dir (data-dir sweep S5); orchestra_cli
# lives in this file's checkout, appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402


# The same data dir the detector (agent-status.py) and the gateway read pane events from: `orchestra
# up` sets ORCH_DIR for both; the calls/ files live next to the hook's panes/ files.
ORCH_DIR = os.environ.get("ORCH_DIR") or os.environ.get("ORCHESTRA_DIR") or str(_data_dir())

# The hook's open-call TTL: a call lost from the map can have been open this long at most.
LOSSY_TTL_S = 1800

_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_NEVER_PERMISSION_OWNER = ("AskUserQuestion", "Agent", "Task")
_TOOL_SUFFIX = re.compile(r"\[([A-Za-z_][\w.\-]*)\]\s*$")


def calls_dir():
    events = os.environ.get("ORCH_EVENTS_DIR") or os.path.join(ORCH_DIR, "state", "agent-events", "panes")
    return os.environ.get("ORCH_CALLS_DIR") or os.path.join(os.path.dirname(events.rstrip("/")), "calls")


def read_open_calls(pane):
    """{tool_use_id: entry} for a pane, {} when unknown. Read-only; the hook owns the file.
    {} too while the hook's loss marker is fresh: an open call was dropped (the cap, the age
    prune, a corrupt map, a compact or clear while calls were open), so a listed call could look like the owner of a menu whose
    real call is gone. No instance beats a wrong one."""
    if not pane:
        return {}
    try:
        with open(os.path.join(calls_dir(), pane.lstrip("%") + ".json")) as f:
            calls = json.load(f)
        calls = calls if isinstance(calls, dict) else {}
    except (OSError, ValueError):
        return {}
    # The marker AFTER the map: the hook writes the marker before it replaces the map, so a map read
    # first is never an evicted one with no marker yet.
    try:
        with open(os.path.join(calls_dir(), pane.lstrip("%") + ".lossy")) as f:
            if time.time() - float(f.read().strip() or 0) < LOSSY_TTL_S:
                return {}
    except (OSError, ValueError):
        pass
    return calls


def pane_for(session):
    try:
        r = subprocess.run(["tmux", "display-message", "-t", f"={session}:", "-p", "#{pane_id}"],
                           capture_output=True, text=True, timeout=3)
        pane = r.stdout.strip()
        return pane if r.returncode == 0 and pane.startswith("%") else None
    except (OSError, subprocess.SubprocessError):
        return None


def _norm(s):
    s = " ".join(str(s or "").lower().split())
    return s.rstrip("…").rstrip(".").strip()


def _question_matches(screen_q, call_q):
    a, b = _norm(screen_q), _norm(call_q)
    if not a or not b:
        return False
    return a == b or b.startswith(a) or a in b      # the detector truncates on a word boundary


def menu_tool(menu):
    m = _TOOL_SUFFIX.search(str(menu.get("question") or ""))
    return m.group(1) if m else ""


def _newest_tool_call(calls):
    """The most recent open call that could raise a permission prompt (not AUQ/Agent/Task)."""
    live = [(c.get("ts") or 0, tid) for tid, c in calls.items()
            if isinstance(c, dict) and (c.get("tool") or "") not in _NEVER_PERMISSION_OWNER]
    return max(live)[1] if live else None


def candidates(menu, calls):
    """The open calls this menu could belong to (ids). Pure."""
    if not isinstance(menu, dict) or not isinstance(calls, dict):
        return []
    q = menu.get("question") or ""
    out = []
    if menu.get("kind") == "permission":
        # Exactly ONE open call may match, and it must also be the newest tool call. Two same-tool
        # calls open at once (concurrency-safe tools run in parallel: both PreToolUse fire before
        # the first prompt) cannot be told apart, so that is ambiguous. And [Tool] is a screen
        # heuristic (it can name a tool drawn above the prompt), so it may only CONFIRM the newest.
        matches = _permission_matches(menu, calls, q)
        return matches if len(matches) == 1 and matches[0] == _newest_tool_call(calls) else []
    if menu.get("menu_family") == "agy":
        return []
    qs = [q] + [p.get("question") for p in (menu.get("parts") or []) if isinstance(p, dict)]
    for tid, c in calls.items():
        if c.get("tool") != "AskUserQuestion":
            continue
        if any(_question_matches(sq, cq) for sq in qs if sq for cq in (c.get("questions") or [])):
            out.append(tid)
    return out


def _permission_matches(menu, calls, q):
    out = []
    tool = menu_tool(menu)
    for tid, c in calls.items():
        t = c.get("tool") or ""
        if t in _NEVER_PERMISSION_OWNER:
            continue
        if tool:
            if t == tool:
                out.append(tid)
        elif t in _FILE_TOOLS:
            if c.get("file") and _norm(c["file"]) in _norm(q):
                out.append(tid)
        elif not tool:
            out.append(tid)
    return out


def resolve(menu, calls):
    """The instance for a menu given the pane's open calls, or None. Pure, no memo."""
    c = candidates(menu, calls)
    return c[0] if len(c) == 1 else None


def signature(menu):
    opts = [(o.get("n"), o.get("label")) for o in (menu.get("options") or []) if isinstance(o, dict)]
    blob = json.dumps([menu.get("kind"), _norm(menu.get("question")), opts], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class InstanceMemo:
    """One decision per (session, on-screen menu). Thread-safe; lives in the gateway process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_session = {}          # session -> {"sig", "instance", "tool"}

    def decide(self, session, menu, calls):
        """{instance, signature, question, tool, decided_at, sticky} for the menu on screen, or
        None when there is no menu. `signature` is of THIS menu, the one the decision was made
        against; `sticky` says the decision was served from the memo, not resolved now."""
        if not isinstance(menu, dict) or not menu:
            with self._lock:
                self._by_session.pop(session, None)       # no menu: the next one starts fresh
            return None
        sig = signature(menu)
        out = {"signature": sig, "question": _norm(menu.get("question"))}
        with self._lock:
            m = self._by_session.get(session)
            if m is not None:
                inst = m["instance"]
                # Only a newer call that could OWN this menu voids the decision (same tool for a
                # permission menu, same question for AUQ); an unrelated background call must not
                # flip the card.
                owners = (_permission_matches(menu, calls, menu.get("question") or "")
                          if menu.get("kind") == "permission" else candidates(menu, calls))
                newer = [tid for tid in owners
                         if tid != inst and (calls.get(tid) or {}).get("ts", 0) > m["decided_at"]]
                if inst is not None and inst not in calls:
                    m = None                               # its call closed: never serve it again
                elif inst is not None and newer:
                    # A NEWER matching call appeared after the decision while the old one is still
                    # open (an identical prompt from a concurrent subagent): the id is no longer
                    # proven, so this menu gets none, and keeps none (no card flip back).
                    m.update(sig=sig, instance=None, tool=None)
                    return {**out, "instance": None, "tool": None,
                            "decided_at": m["decided_at"], "sticky": True}
                elif m["sig"] == sig:
                    return {**out, "instance": inst, "tool": m["tool"],
                            "decided_at": m["decided_at"], "sticky": True}
                elif (inst is not None and m["tool"] == "AskUserQuestion"
                      and menu.get("kind") != "permission"
                      # an AUQ blocks the agent: a newer call of any tool means the screen moved on
                      and inst == max(calls, key=lambda t: (calls.get(t) or {}).get("ts") or 0)
                      # the next tab matches the same call; the review screen matches nothing but
                      # carries the AUQ's Submit (a plan or trust menu has none)
                      and (candidates(menu, calls) == [inst]
                           or (not candidates(menu, calls) and menu.get("has_submit")))):
                    m["sig"] = sig                         # next tab / review screen of the SAME call
                    return {**out, "instance": inst, "tool": m["tool"],
                            "decided_at": m["decided_at"], "sticky": True}
            inst = resolve(menu, calls)
            m = {"sig": sig, "instance": inst, "decided_at": round(time.time(), 3),
                 "tool": (calls.get(inst) or {}).get("tool") if inst else None}
            self._by_session[session] = m
            return {**out, "instance": inst, "tool": m["tool"], "decided_at": m["decided_at"],
                    "sticky": False}

    def stamp(self, session, menu, calls):
        d = self.decide(session, menu, calls)
        return d["instance"] if d else None


MEMO = InstanceMemo()


def decision_for(session, menu, pane=None):
    """Gateway entry point: the full decision for a live menu (see InstanceMemo.decide). Any failure
    -> None, so a caller binds NO instance rather than a stale one."""
    try:
        return MEMO.decide(session, menu, read_open_calls(pane or pane_for(session)))
    except Exception:  # noqa: BLE001 — identity is additive in phase 0; never break a read
        return None


def instance_for(session, menu, pane=None):
    d = decision_for(session, menu, pane)
    return d["instance"] if d else None
