"""session_turns.py — the text turns of any agent's saved conversation, whatever CLI wrote it.

A briefing (scripts/briefing.py) needs the same thing from every agent: its last exchanges as
[(role, iso time, text)], oldest first, with a short "[ran <tool> <what>]" line for each action so
it shows what was done and not only what was said. Each CLI saves its conversation differently,
so each format gets a small reader here; an agent's profile (config/agent-profiles/) names its
format. Claude's own reader stays in briefing.py.

The readers mirror the dashboard's (api/src/routes/chat-transcript.ts) on what counts as a turn.
Nothing here raises: an unreadable file is no turns.
"""
import datetime
import json
import os
import re

TAIL_BYTES = 600_000


def _tail_lines(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return lines[1 if size > TAIL_BYTES else 0:]
    except (OSError, TypeError):
        return []


def _records(path):
    out = []
    for line in _tail_lines(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _one_line(text, n):
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _ran(name, args):
    args = args if isinstance(args, dict) else {}
    what = next((args[k] for k in ("file_path", "path", "command", "cmd", "CommandLine", "pattern", "url")
                 if isinstance(args.get(k), str) and args[k]), "")
    return f"[ran {name or 'a tool'}{(' ' + _one_line(what, 100)) if what else ''}]"


def _iso_ms(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _push(turns, role, ts, text):
    text = (text or "").strip()
    if not text:
        return
    # Consecutive parts of one reply (text, then the tools it ran) read as one turn.
    if turns and turns[-1][0] == role == "assistant":
        turns[-1] = (role, turns[-1][1] or ts, f"{turns[-1][2]} {text}")
    else:
        turns.append((role, ts, text))


# ---- Codex: ~/.codex/sessions/**/rollout-*.jsonl -----------------------------------------------

# The same envelopes the dashboard drops (chat-transcript.ts).
_CODEX_ENVELOPE = re.compile(
    r"^\s*<(environment_context|skills_instructions|multi_agent_mode|user_instructions|system_context)\b", re.I)

def codex_turns(path):
    recs = _records(path)
    # The file can have more than one writer; its ordinal, not line order, is the order.
    recs.sort(key=lambda d: d.get("ordinal") if isinstance(d.get("ordinal"), int) else 0)
    turns = []
    for d in recs:
        if d.get("type") != "response_item":
            continue
        p = d.get("payload") or {}
        ts = d.get("timestamp") or ""
        if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            text = " ".join(c.get("text", "") for c in p.get("content") or []
                            if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"))
            # Codex adds its own context to the conversation as user messages; they are not the work.
            if p["role"] == "user" and _CODEX_ENVELOPE.match(text):
                continue
            _push(turns, p["role"], ts, text)
        elif p.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
            raw = p.get("arguments") or p.get("input") or p.get("action") or {}
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = {}
            _push(turns, "assistant", ts, _ran(p.get("name") or p.get("type"), args))
    return turns


# ---- Gemini (agy): brain/<sid>/.system_generated/logs/transcript.jsonl ---------------------------

def _antigravity_user_text(text):
    text = text.split("</CONTEXT_SUMMARY>")[-1]
    text = re.sub(r"<ADDITIONAL_METADATA>[\s\S]*?</ADDITIONAL_METADATA>", "", text)
    m = re.search(r"<USER_REQUEST>([\s\S]*?)</USER_REQUEST>", text)
    return (m.group(1) if m else text).strip()


def antigravity_turns(path):
    turns = []
    for d in _records(path):
        ts = d.get("created_at") or ""
        t = d.get("type")
        if t == "USER_INPUT":
            _push(turns, "user", ts, _antigravity_user_text(d.get("content") or ""))
        elif t == "PLANNER_RESPONSE":
            bits = [d.get("content") or ""]
            for tc in d.get("tool_calls") or []:
                if isinstance(tc, dict):
                    args = {k: (v.strip('"') if isinstance(v, str) else v) for k, v in (tc.get("args") or {}).items()}
                    bits.append(_ran(tc.get("name"), args))
            _push(turns, "assistant", ts, " ".join(b for b in bits if b))
    return turns


# ---- Grok Build: ~/.grok/sessions/<dir>/<sid>/updates.jsonl --------------------------------------

def grok_turns(path):
    turns, last_su = [], None
    for d in _records(path):
        params = d.get("params") or {}
        upd = params.get("update") or {}
        su = upd.get("sessionUpdate")
        if not isinstance(su, str):
            continue
        ms = (params.get("_meta") or {}).get("agentTimestampMs")
        ts = _iso_ms(ms) if isinstance(ms, (int, float)) else (
            _iso_ms(d["timestamp"] * 1000) if isinstance(d.get("timestamp"), (int, float)) else "")
        if su in ("user_message_chunk", "agent_message_chunk"):
            role = "user" if su == "user_message_chunk" else "assistant"
            text = (upd.get("content") or {}).get("text") or ""
            if role == "user" and text.lstrip().startswith(("<system-reminder", "<user_info", "<rules")):
                last_su = su
                continue
            # Text streams in chunks: a chunk following one of the same kind continues it.
            if last_su == su and turns and turns[-1][0] == role:
                turns[-1] = (role, turns[-1][1], turns[-1][2] + text)
            elif text.strip():
                # Raw, not trimmed: the next chunk may continue mid-sentence ("Sure, " + "listing.").
                if role == "assistant" and turns and turns[-1][0] == "assistant":
                    turns[-1] = (role, turns[-1][1] or ts, f"{turns[-1][2].rstrip()} {text}")
                else:
                    turns.append((role, ts, text))
        elif su == "tool_call":
            name = ((upd.get("_meta") or {}).get("x.ai/tool") or {}).get("name") or upd.get("title") or ""
            _push(turns, "assistant", ts, _ran(name, upd.get("rawInput")))
        last_su = su
    return [(r, t, x.strip()) for r, t, x in turns]


# ---- Pi: ~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl ---------------------------------

def _pi_text(content):
    if isinstance(content, str):
        return content
    bits = []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            bits.append(c.get("text") or "")
        elif c.get("type") == "toolCall":
            bits.append(_ran(c.get("name"), c.get("arguments")))
    return " ".join(b for b in bits if b)


def pi_turns(path):
    """Pi saves a tree (id / parentId) so a session can branch; the turns that matter are the
    current branch: from the last entry back to the root."""
    recs = [d for d in _records(path) if d.get("type") != "session"]
    by_id = {d["id"]: d for d in recs if isinstance(d.get("id"), str)}
    branch, cur, guard = [], recs[-1] if recs else None, 0
    while cur is not None and guard <= len(recs):
        branch.append(cur)
        cur = by_id.get(cur.get("parentId")) if cur.get("parentId") else None
        guard += 1
    turns = []
    for d in reversed(branch):
        if d.get("type") != "message":
            continue
        m = d.get("message") or {}
        role = m.get("role")
        ts = d.get("timestamp") or _iso_ms(m.get("timestamp"))
        if role in ("user", "assistant"):
            _push(turns, role, ts, _pi_text(m.get("content")))
        elif role == "bashExecution" and not m.get("excludeFromContext"):
            _push(turns, "user", ts, f"[ran bash {_one_line(m.get('command') or '', 100)}]")
    return turns


READERS = {
    "codex-rollout": codex_turns,
    "antigravity-steps": antigravity_turns,
    "grok-updates": grok_turns,
    "pi-jsonl": pi_turns,
}


def sniff(path):
    """The format a session file is in, from its own lines; None when it is none of these (a
    Claude session is told apart by briefing.py's own reader)."""
    try:
        with open(path) as f:
            head = [f.readline() for _ in range(5)]
    except (OSError, TypeError):
        return None
    for line in head:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "session" and "version" in d and "cwd" in d:
            return "pi-jsonl"
        if d.get("type") in ("session_meta", "turn_context", "response_item"):
            return "codex-rollout"
        if "step_index" in d and isinstance(d.get("source"), str):
            return "antigravity-steps"
        if isinstance((d.get("params") or {}).get("update"), dict):
            return "grok-updates"
    return None


def turns(fmt, path, limit=60):
    reader = READERS.get(fmt)
    if not reader or not path:
        return []
    try:
        return reader(path)[-limit:]
    except Exception:  # noqa: BLE001 - a briefing is a help, never a reason a start fails
        return []
