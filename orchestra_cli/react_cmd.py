"""`orchestra react`: an agent reacts to a message in its own chat with one emoji, instead of a reply that is only
an emoji (spec docs/superpowers/specs/2026-11-02-message-reactions-design.md, "Agent path").

    orchestra react 👍                    the operator's newest message in your chat (--last, the default)
    orchestra react 🎉 --to <ref>          a message by its ref (item.ref in /api/agents/<you>/transcript)
    orchestra react --remove [--to <ref>]  take your reaction back

Who you are: $ORCHESTRA_AGENT_ID (every seat has it), else the tmux session you run in (#S). The API counts you as
that agent only with the secret in <data dir>/state/agent-react-token, sent as X-Orchestra-Agent-Token next to
X-Orchestra-Agent; the API writes it when it starts (a wrong token is a 401 agent_token_invalid, nothing stored). Where the API is: $ORCH_API_URL, else [api] host/port in
orchestra.toml. Reacting again with the emoji you already have changes nothing (the API's PUT would take it off).
Exit 0 done (or nothing to do), 1 the API refused or could not be reached, 2 nothing to react with, as or to.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import settings as S

SURFACE = "agent-chat"
TIMEOUT_S = 10.0
LAST_SCAN = 200          # how many of the chat's newest items --last looks through
#: a user item with one of these is not something the operator said (flags set by api chat-transcript.ts)
NOT_SAID = ("is_system", "machine", "voice_said", "reaction_wake", "call_id")


class ReactError(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def add_parser(sub) -> None:
    r = sub.add_parser("react", help="react to a message in your own chat with one emoji "
                                     "(instead of a reply that is only an emoji)")
    r.add_argument("emoji", nargs="?", help="one emoji, e.g. 👍 (not needed with --remove)")
    to = r.add_mutually_exclusive_group()
    to.add_argument("--to", metavar="REF", help="the message's ref (item.ref in your chat's transcript)")
    to.add_argument("--last", action="store_true", help="the operator's newest message in your chat (the default)")
    r.add_argument("--remove", action="store_true", help="take your reaction on that message back")


def agent_id() -> str:
    env = os.environ.get("ORCHESTRA_AGENT_ID", "").strip()
    if env:
        return env
    if not os.environ.get("TMUX"):
        return ""
    try:
        out = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def api_base() -> str:
    env = os.environ.get("ORCH_API_URL", "").strip()
    if env:
        return env.rstrip("/")
    st = S.load_settings()
    return f"http://{S._connectable(st.api_host)}:{st.api_port}"


def token_file() -> Path:
    env = os.environ.get("ORCHESTRA_AGENT_REACT_TOKEN_FILE", "").strip()
    return Path(env) if env else S.data_dir() / "state" / "agent-react-token"


def read_token() -> str:
    p = token_file()
    try:
        tok = p.read_text().strip()
    except OSError:
        tok = ""
    if not tok:
        raise ReactError(f"no agent react token at {p} (the API writes it when it starts; is it running?)", 2)
    return tok


def _call(method: str, url: str, body=None, headers=None):
    """(status, json body). An HTTP error status is returned, not raised; no answer at all raises ReactError."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            doc = json.loads(e.read() or b"{}")
        except ValueError:
            doc = {}
        return e.code, doc if isinstance(doc, dict) else {}
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ReactError(f"could not reach the API at {url.split('/api/')[0]} ({e})") from e


def _failed(what: str, status: int, doc: dict) -> ReactError:
    return ReactError(f"{what}: HTTP {status} {doc.get('error') or ''}".strip())


def _token_refused() -> ReactError:
    # The API answers 401 agent_token_invalid and stores nothing, so there is nothing to undo.
    return ReactError("agent_token_invalid: the API did not accept your agent token, so nothing was recorded "
                      f"(token file {token_file()}; is the API on the same data dir?)")


def said_by_operator(it) -> bool:
    return (isinstance(it, dict) and it.get("kind") == "text" and it.get("role") == "user" and bool(it.get("ref"))
            and bool(str(it.get("text") or "").strip()) and not any(it.get(k) for k in NOT_SAID))


def last_user_ref(base: str, agent: str) -> str:
    status, doc = _call("GET", f"{base}/api/agents/{urllib.parse.quote(agent, safe='')}/transcript?limit={LAST_SCAN}")
    if status != 200:
        raise _failed("could not read your chat", status, doc)
    for it in reversed(doc.get("items") or []):
        if said_by_operator(it):
            return str(it["ref"])
    raise ReactError("no message from the operator in your chat to react to", 2)


def own_emoji(base: str, agent: str, ref: str):
    q = urllib.parse.urlencode({"surface": SURFACE, "conv": agent})
    status, doc = _call("GET", f"{base}/api/reactions?{q}")
    if status != 200:
        raise _failed("could not read the reactions", status, doc)
    for r in (doc.get("reactions") or {}).get(ref) or []:
        who = r.get("reactor") or {}
        if who.get("kind") == "agent" and who.get("id") == agent:
            return r.get("emoji")
    return None


def react(emoji, *, to=None, remove=False) -> str:
    """React (or take it back); the line to print. Raises ReactError."""
    if not remove and not emoji:
        raise ReactError("which emoji? e.g. orchestra react 👍", 2)
    agent = agent_id()
    if not agent:
        raise ReactError("who are you? ORCHESTRA_AGENT_ID is not set and this is not a tmux pane", 2)
    token = read_token()
    base = api_base()
    ref = to or last_user_ref(base, agent)
    have = own_emoji(base, agent, ref)
    me = {"X-Orchestra-Agent": agent, "X-Orchestra-Agent-Token": token}
    target = {"surface": SURFACE, "conv": agent, "ref": ref}
    if remove:
        if have is None:
            return f"no reaction of yours on {ref}"
        status, doc = _call("DELETE", f"{base}/api/reactions", target, me)
        if status == 401 and doc.get("error") == "agent_token_invalid":
            raise _token_refused()
        if status != 200 or not doc.get("removed"):
            raise _failed("the API did not remove it", status, doc)
        return f"removed your {have} from {ref}"
    if have == emoji:
        return f"already reacted {emoji} to {ref}"
    status, doc = _call("PUT", f"{base}/api/reactions", {**target, "emoji": emoji}, me)
    if status == 401 and doc.get("error") == "agent_token_invalid":
        raise _token_refused()
    if status != 200:
        raise _failed("the API refused", status, doc)
    return f"reacted {emoji} to {ref}"


def cmd_react(ns) -> int:
    try:
        print(react(ns.emoji, to=ns.to, remove=ns.remove))
        return 0
    except ReactError as e:
        print(f"orchestra react: {e}", file=sys.stderr)
        return e.code
