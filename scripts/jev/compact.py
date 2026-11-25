"""Level 7: should I compact / rotate. Four questions after every turn, four tiers back.

Port of ten-levels-of-jev src/levels/level07 + extensions/jev-compact.ts, adapted to
OrchestraOS: in this fleet "compact" means a blue-green rotation, so each tier also maps
to a rotation action the BG engine can read.

  Jev judges whether the WORK moved on (switched_gears, at_boundary, needs_history,
  mid_operation). Context usage and the lines stay in code.

  tier       agent hears                                rotation action
  silent     nothing                                    none
  notice     optional, compact if at a clean point      notice
  recommend  recommended now                            recommend_rotation
  request    please rotate before continuing            prewarm_green_swap

Claude Code adapter:
  Stop hook            -> evaluate the finished turn from the transcript, persist the verdict
                          to <state_dir>/<session_id>.json (the BG engine and the next hook read it)
  UserPromptSubmit     -> inject the pending tier message once, as additionalContext
  should_i_compact     -> the same verdict on demand (jev_mcp tool / jev_hook.py should-compact)

Environment:
  JEV_COMPACT_LINES    "notice,recommend,request" as fractions of the window (<=1) or tokens.
                       Default 0.60,0.70,0.80: recommend/request bracket the fleet's 70-80% BG band.
  JEV_CONTEXT_WINDOW   tokens, default 1_000_000 (fleet seats run [1m])
  JEV_KEEP_RECENT_TOKENS  below this there is nothing worth compacting, default 20000
  JEV_STATE_DIR        verdict directory, default $ORCHESTRA_DIR/state/jev/compact
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from . import audit
from .client import JevError, default_decide
from .helpers import choice, noul, score
from .types import Questions, State

COMPACT_QUESTIONS: Questions = {
    "switched_gears": noul("Is `current_request` a different task from `previous_work`?", {
        "true": "A new feature, a different file area, a different goal, or an unrelated question",
        "false": "The same task continuing, a follow up, a fix to what was just done",
    }),
    "at_boundary": noul("Did `recent_turn` finish a unit of work?", {
        "true": "Tests passed, a commit was made, a summary was given, or a question was asked of the user",
        "false": "Mid task, more steps clearly remain",
    }),
    "needs_history": score("How much of `previous_work` does the next step need?", [
        "None; the new work stands alone",
        "Some references, a file name or a decision",
        "Most of it; the work continues directly from it",
    ]),
    "mid_operation": noul("Is the agent in the middle of a multi step edit whose partial state only exists in the conversation?", {
        "true": "Half applied changes, a plan being executed step by step, an unfinished refactor",
        "false": "A clean point, nothing half done",
    }),
}

TIERS = ("silent", "notice", "recommend", "request")
ROTATION_ACTIONS = {"silent": "none", "notice": "notice", "recommend": "recommend_rotation",
                    "request": "prewarm_green_swap"}

# The reference demo's lines (tokens, deliberately tiny so a few reads cross them).
DEFAULT_LINES = {"notice": 6000, "recommend": 10_000, "request": 14_000}
FLEET_FRACTIONS = (0.60, 0.70, 0.80)
DEFAULT_WINDOW = 1_000_000
DEFAULT_KEEP_RECENT = 20_000


def lines_for_window(window: int, fractions: Tuple[float, float, float] = FLEET_FRACTIONS) -> Dict[str, int]:
    n, r, q = fractions
    if not 0 < n <= r <= q <= 1:
        raise ValueError("line fractions must satisfy 0 < notice <= recommend <= request <= 1")
    return {"notice": int(window * n), "recommend": int(window * r), "request": int(window * q)}


def env_window() -> int:
    raw = (os.environ.get("JEV_CONTEXT_WINDOW") or "").strip()
    return int(raw) if raw else DEFAULT_WINDOW


def env_lines(window: int) -> Dict[str, int]:
    raw = (os.environ.get("JEV_COMPACT_LINES") or "").strip()
    if not raw:
        return lines_for_window(window)
    vals = [float(x) for x in raw.split(",")]
    if len(vals) != 3:
        raise ValueError("JEV_COMPACT_LINES needs three values: notice,recommend,request")
    if all(v <= 1 for v in vals):
        return lines_for_window(window, (vals[0], vals[1], vals[2]))
    n, r, q = (int(v) for v in vals)
    if not 0 < n <= r <= q:
        raise ValueError("JEV_COMPACT_LINES must be increasing")
    return {"notice": n, "recommend": r, "request": q}


def _clip(s: str, n: int = 70) -> str:
    return s[: n - 1] + "…" if len(s) > n else s


def decide_tier(a: Dict[str, Any], usage: Dict[str, float], compactable: bool, lines: Dict[str, int],
                state: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Silent when there is nothing to compact, mid operation, or the work has not moved on.
    Otherwise the tier follows token usage against the lines. Numbers in code."""
    if not compactable:
        return {"tier": "silent", "reason": "nothing to compact yet"}
    if a["mid_operation"]["noul"] > 0.6:
        return {"tier": "silent", "reason": "mid operation"}
    switched = a["switched_gears"]["noul"] > 0.7
    boundary = a["at_boundary"]["noul"] > 0.6 and a["needs_history"]["score"] < 1
    if not switched and not boundary:
        return {"tier": "silent", "reason": "same work continuing"}
    if usage["tokens"] < lines["notice"]:
        return {"tier": "silent", "reason": "below the notice line"}
    if switched:
        detail = f' from "{_clip(state["previous_work"])}" to "{_clip(state["current_request"])}"' if state else ""
        reason = f"The task changed{detail}."
    else:
        reason = "The last turn finished a unit of work and the next step needs little of the earlier context."
    if usage["tokens"] < lines["recommend"]:
        return {"tier": "notice", "reason": reason}
    if usage["tokens"] < lines["request"]:
        return {"tier": "recommend", "reason": reason}
    return {"tier": "request", "reason": reason}


def tier_message(d: Dict[str, str], usage: Dict[str, float]) -> Optional[str]:
    """The exact text the agent sees next turn. Silent returns None: nothing is injected."""
    pct = f"{round(usage['tokens'] / 1000)}k tokens, {usage['pct']:.1f}% of the window"
    tier, reason = d["tier"], d["reason"]
    if tier == "notice":
        return (f"Context is at {pct}. {reason} Rotating is optional. If you are at a clean point, "
                "write your handoff and let the blue-green rotation take it.")
    if tier == "recommend":
        return (f"Recommended: rotate now. Context is at {pct}. {reason} Finish the current step, "
                "write your handoff, then let the blue-green rotation take it.")
    if tier == "request":
        return (f"Please rotate before continuing. Context is at {pct}. {reason} Write your handoff now; "
                "a green is being prewarmed.")
    return None


def compact_verdict(d: Dict[str, str], a: Dict[str, Any], usage: Dict[str, float], lines: Dict[str, int]) -> Dict[str, Any]:
    """The on-demand tool result. Booleans and numbers first, prose last."""
    should = d["tier"] in ("recommend", "request")
    return {
        "should_compact": should,
        "tier": d["tier"],
        "rotation_action": ROTATION_ACTIONS[d["tier"]],
        "reason": d["reason"],
        "context_tokens": usage["tokens"],
        "context_percent": round(usage["pct"], 1),
        "lines": lines,
        "signals": {
            "switched_gears": a["switched_gears"]["noul"],
            "at_boundary": a["at_boundary"]["noul"],
            "needs_history": a["needs_history"]["score"],
            "mid_operation": a["mid_operation"]["noul"],
        },
        "next_step": ("Write your handoff, then let the blue-green rotation take it." if should
                      else "Optional. Continue, or rotate if you are at a clean point." if d["tier"] == "notice"
                      else "Continue. Nothing to do."),
    }


# ---------------------------------------------------------------- C: pick the cut point

def cut_point_question(turns: List[Dict[str, Any]]) -> Questions:
    """One Choice over the turns, keyed by index, so the pick is always a real turn."""
    criteria: Dict[str, Optional[str]] = {str(t["index"]): t["request"] for t in turns}
    criteria["none"] = "Every turn is still live; keep the most recent context only"
    return {"live_from": choice(
        "Which turn in `turns` starts the work that is still live? Earlier turns can be summarized briefly.", criteria)}


def cut_point_instructions(turns: List[Dict[str, Any]], answer: Dict[str, Any], floor: float = 0.6) -> Dict[str, Any]:
    if answer["choice"] == "none" or answer["confidence"] < floor:
        return {"live_from": None, "confidence": answer["confidence"],
                "instructions": "Summarize the earlier work briefly and keep the most recent turns in detail."}
    idx = int(answer["choice"])
    turn = next((t for t in turns if t["index"] == idx), None)
    frm = f'"{turn["request"]}"' if turn else f"turn {idx}"
    return {"live_from": idx, "confidence": answer["confidence"],
            "instructions": (f"The live work starts at {frm}. Summarize everything before it in a few lines. "
                             f"Keep the decisions, file paths, and open questions from {frm} onward in full detail.")}


# ---------------------------------------------------------------- Claude Code transcript

def _text_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_prompt(entry: Dict[str, Any]) -> bool:
    """A real user prompt: not a tool_result carrier, not meta, not a sidechain, not a summary."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    return bool(_text_blocks(content).strip())


def read_transcript(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a torn last line while the writer is mid-append
            if isinstance(entry, dict):
                out.append(entry)
    return out


def context_tokens(usage: Dict[str, Any]) -> int:
    """Tokens in context on the last request: fresh + cache writes + cache reads + output."""
    return sum(int(usage.get(k) or 0) for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))


def transcript_snapshot(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything the hook needs, built in code from the session. No LLM involved."""
    prompts: List[str] = []
    last_summary = ""
    last_prompt_at = -1
    for i, e in enumerate(entries):
        if e.get("isCompactSummary"):
            last_summary = _text_blocks((e.get("message") or {}).get("content"))
        elif _is_prompt(e):
            prompts.append(_text_blocks(e["message"]["content"]).strip())
            last_prompt_at = i
    recent_text, tools, tokens = "", [], 0
    for e in entries[last_prompt_at + 1:]:
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        msg = e.get("message") or {}
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                recent_text = b["text"]
            elif isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append(b.get("name", "?"))
    for e in reversed(entries):
        if e.get("type") == "assistant" and not e.get("isSidechain") and (e.get("message") or {}).get("usage"):
            tokens = context_tokens(e["message"]["usage"])
            break
    previous = [_clip(p, 200) for p in prompts[:-1]]
    if last_summary:
        previous.append(f"Summary so far: {_clip(last_summary, 400)}")
    state = {
        "current_request": _clip(prompts[-1] if prompts else "", 600),
        "previous_work": "\n".join(previous) or "(nothing before this request)",
        "recent_turn": _clip(recent_text.strip() or f"(tool calls only: {', '.join(tools) or 'none'})", 600),
        "tools_this_turn": tools[-50:],
    }
    return {"state": state, "prompts": len(prompts), "tokens": tokens,
            "turns": [{"index": i, "request": _clip(p, 120)} for i, p in enumerate(prompts)]}


def evaluate(snapshot: Dict[str, Any], window: int, lines: Dict[str, int], keep_recent: int,
             decide=default_decide) -> Dict[str, Any]:
    """One Jev call (or none), the tier, the message, the verdict. Raises JevError on a failed call."""
    usage = {"tokens": snapshot["tokens"], "pct": snapshot["tokens"] / window * 100 if window else 0.0}
    compactable = usage["tokens"] > keep_recent
    if snapshot["prompts"] < 2:
        d = {"tier": "silent", "reason": "first request, nothing to move on from"}
        answers = {"switched_gears": {"type": "noul", "noul": 0}, "at_boundary": {"type": "noul", "noul": 0},
                   "needs_history": {"type": "score", "score": 2}, "mid_operation": {"type": "noul", "noul": 0}}
        return {"decision": d, "message": None, "usage": usage, "answers": answers, "result": None,
                "verdict": compact_verdict(d, answers, usage, lines), "skipped": True}
    result = decide(snapshot["state"], COMPACT_QUESTIONS)
    answers = result["answers"]
    d = decide_tier(answers, usage, compactable, lines, snapshot["state"])
    return {"decision": d, "message": tier_message(d, usage), "usage": usage, "answers": answers,
            "result": result, "verdict": compact_verdict(d, answers, usage, lines), "skipped": False}


# ---------------------------------------------------------------- verdict store

def state_dir() -> str:
    explicit = os.environ.get("JEV_STATE_DIR")
    if explicit:
        return explicit
    base = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/.orchestra")
    return os.path.join(base, "state", "jev", "compact")


def _safe_id(session_id: str) -> str:
    sid = "".join(c for c in session_id if c.isalnum() or c in "-_")
    if not sid:
        raise ValueError("session_id is empty after sanitizing")
    return sid


def verdict_path(session_id: str, directory: Optional[str] = None) -> str:
    return os.path.join(directory or state_dir(), f"{_safe_id(session_id)}.json")


def write_verdict(session_id: str, record: Dict[str, Any], directory: Optional[str] = None) -> str:
    """Atomic replace, so a reader (the BG engine) never sees a torn file."""
    path = verdict_path(session_id, directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".verdict-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    os.replace(tmp, path)
    return path


def latest_verdict(session_id: str, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """For the rotation engine: the last verdict for a session, or None if never evaluated."""
    try:
        with open(verdict_path(session_id, directory), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def stop_hook(event: Dict[str, Any], decide=default_decide, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Claude Code Stop hook: evaluate the turn, persist. Prints nothing; never blocks the stop."""
    if event.get("stop_hook_active"):
        return None
    sid, transcript = event.get("session_id"), event.get("transcript_path")
    if not sid or not transcript or not os.path.isfile(transcript):
        return None
    window = env_window()
    lines = env_lines(window)
    keep = int(os.environ.get("JEV_KEEP_RECENT_TOKENS") or DEFAULT_KEEP_RECENT)
    snap = transcript_snapshot(read_transcript(transcript))
    base = {"hook": "Stop", "session_id": sid}
    try:
        ev = evaluate(snap, window, lines, keep, decide)
    except JevError as err:
        audit.record("error", {**base, "gate": "L7", "error": f"{type(err).__name__}: {err}"})
        return None
    record = {**ev["verdict"], "message": ev["message"], "delivered": ev["message"] is None,
              "session_id": sid, "model": (ev["result"] or {}).get("model"), "skipped": ev["skipped"],
              "transcript_path": transcript}
    write_verdict(sid, record, directory)
    audit.record("compact", {**base, "tier": ev["decision"]["tier"], "reason": ev["decision"]["reason"],
                             "tokens": ev["usage"]["tokens"], "skipped": ev["skipped"],
                             **(audit.usage_of(ev["result"]) if ev["result"] else {})})
    return None


def user_prompt_submit_hook(event: Dict[str, Any], directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Inject the pending tier message once. Silent verdicts inject nothing."""
    sid = event.get("session_id")
    if not sid:
        return None
    record = latest_verdict(sid, directory)
    if not record or record.get("delivered") or not record.get("message"):
        return None
    record["delivered"] = True
    write_verdict(sid, record, directory)
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                   "additionalContext": f"[jev-compact] {record['message']}"}}


def should_i_compact(transcript_path: str, decide=default_decide) -> Dict[str, Any]:
    """On-demand verdict for a transcript (the should_i_compact tool)."""
    window = env_window()
    lines = env_lines(window)
    keep = int(os.environ.get("JEV_KEEP_RECENT_TOKENS") or DEFAULT_KEEP_RECENT)
    return evaluate(transcript_snapshot(read_transcript(transcript_path)), window, lines, keep, decide)["verdict"]


# ---------------------------------------------------------------- stage 4: the seam signal
# DEC-1791324686371842 (v2, CONSENSUS_REACHED): the live decide_bg may swap an idle blue after
# 60 s instead of 300 s when the verdict for the blue's LIVE sid says the work moved on. This
# module answers only "moved on, fresh, this transcript"; sid PROVENANCE (live vs canonical) is
# the caller's gate, because only build_obs knows where the sid came from.

SEAM_FRESH_TOLERANCE_S = 15.0  # measured 2026-10-06: idle transcripts trail their verdict by 0-5 s


def signals_moved_on(signals: Dict[str, Any]) -> bool:
    """The decide_tier predicate on flat verdict signals: not mid-operation, and either the task
    switched or a unit of work finished needing little history. Kept in step with decide_tier by
    test_seam_predicate_matches_decide_tier."""
    return (signals["mid_operation"] <= 0.6
            and (signals["switched_gears"] > 0.7
                 or (signals["at_boundary"] > 0.6 and signals["needs_history"] < 1)))


def _find_transcript(session_id: str) -> Optional[str]:
    import glob as _glob
    hits = _glob.glob(os.path.join(os.path.expanduser("~/.claude/projects"), "*", f"{_safe_id(session_id)}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def seam_signal(session_id: str, directory: Optional[str] = None,
                tolerance_s: float = SEAM_FRESH_TOLERANCE_S) -> Dict[str, Any]:
    """{"moved_on": bool, "why": str} for one session. Every doubt is False (plain lull applies).

    Fresh means the verdict judged the transcript as it stands now: verdict mtime + tolerance >=
    transcript mtime. (The spec's "newer than idle_since" cannot hold: the Stop hook writes the
    verdict at turn end, and the beat observes idle up to a beat later.)"""
    path = verdict_path(session_id, directory)
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        verdict_mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return {"moved_on": False, "why": "no-verdict"}
    except (OSError, ValueError) as err:
        audit.record("error", {"hook": "seam_signal", "session_id": session_id,
                               "error": f"{type(err).__name__}: {err}"})
        return {"moved_on": False, "why": "unreadable-verdict"}
    signals = record.get("signals") if isinstance(record, dict) else None
    if not isinstance(signals, dict) or not all(
            isinstance(signals.get(k), (int, float)) for k in ("switched_gears", "at_boundary", "needs_history", "mid_operation")):
        return {"moved_on": False, "why": "malformed-verdict"}
    if record.get("skipped"):
        return {"moved_on": False, "why": "verdict-skipped"}
    transcript = record.get("transcript_path") or _find_transcript(session_id)
    try:
        transcript_mtime = os.path.getmtime(transcript) if transcript else None
    except OSError:
        transcript_mtime = None
    if transcript_mtime is None:
        return {"moved_on": False, "why": "no-transcript"}
    if verdict_mtime + tolerance_s < transcript_mtime:
        return {"moved_on": False, "why": "stale-verdict"}
    if not signals_moved_on(signals):
        return {"moved_on": False, "why": "same-work" if signals["mid_operation"] <= 0.6 else "mid-operation"}
    return {"moved_on": True, "why": "moved-on"}
