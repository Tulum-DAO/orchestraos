"""Per-session live ENRICHMENT for the lineage daemon (read-only IO).

The daemon's decide() is blind when `agent-status.py --all` can't parse a
session's context bar (the '████ 86%' and skull '0% until auto-compact'
formats). This module adds two daemon-local, READ-ONLY signals to each status
dict so collect.build_agent can fall back:

  * pane_status_line : the tmux status-bar line (`capture-pane -p`, read-only)
  * jsonl_tokens     : the live .jsonl context token count (last usage record)
  * resolved_model   : model string (from the resume_command sid, then registry)

It NEVER writes, kills, spawns, or touches a live service -- only `tmux
capture-pane -p` (read-only) and reading session .jsonl files. All IO seams are
injectable (capture_fn / project_root / opener) so unit tests stay hermetic.
The sid used for the .jsonl is taken from resume_command FIRST (agent-sessions'
session_id is periodically clobbered to a stale value -- the sid-clobber gotcha).
"""

import json
import os
import re
import subprocess

_RESUME_SID_RE = re.compile(r"(?:--resume|--conversation)\s+([0-9a-f-]{36})")
_MODEL_RE = re.compile(r"--model\s+'([^']+)'")


def _project_dir(cwd: str) -> str:
    """Claude Code encodes a cwd into its projects/ dir by replacing '/' and
    '.' with '-'. e.g. /home/testuser/agent-orchestra ->
    -home-testuser-agent-orchestra."""
    return re.sub(r"[/.]", "-", cwd or "")


def live_sid(entry: dict) -> str:
    """The session's LIVE id. Prefer the resume_command's --resume or
    --conversation sid (authoritative) over agent-sessions' session_id."""
    entry = entry or {}
    m = _RESUME_SID_RE.search(entry.get("resume_command") or "")
    if m:
        return m.group(1)
    return entry.get("session_id") or ""


def resolve_model(entry: dict, reg_entry: dict) -> str:
    """Model string: from the resume_command --model first, then registry, then
    the session metadata. The resume_command carries the true [1m] variant."""
    entry = entry or {}
    reg_entry = reg_entry or {}
    m = _MODEL_RE.search(entry.get("resume_command") or "")
    if m:
        return m.group(1)
    mod = reg_entry.get("model") or entry.get("model") or ""
    if not mod and (reg_entry.get("runtime") == "gemini" or entry.get("runtime") == "gemini"):
        return "gemini-3.7-flash"
    return mod


def jsonl_context_tokens(sid, cwd="", project_root=None, brain_root=None,
                         codex_root=None, opener=open):
    """Sum the context tokens from a session's .jsonl (Claude), brain transcript (Gemini),
    or rollout JSONL (Codex).

    Claude: context tokens = input + cache_read + cache_creation (the window actually
    being re-read each turn).
    Gemini: conservative heuristic ~250 tokens per KB JSONL from brain transcript log.
    Codex: last token_count event's total_tokens from rollout JSONL.
    Returns None if the file is missing/unreadable or has no usage record.
    `project_root`, `brain_root`, `codex_root`, + `opener` are injectable for tests.
    """
    if not sid:
        return None

    # 1. Try Claude projects path
    root = project_root or os.path.expanduser("~/.claude/projects")
    claude_path = os.path.join(root, _project_dir(cwd), f"{sid}.jsonl")
    if not os.path.exists(claude_path) and not project_root and os.path.isdir(root):
        import glob
        c_hits = sorted(glob.glob(os.path.join(root, "**", f"{sid}.jsonl"), recursive=True))
        if c_hits:
            claude_path = c_hits[0]
    if os.path.exists(claude_path) or project_root:
        last = None
        try:
            with opener(claude_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    msg = obj.get("message")
                    usage = msg.get("usage") if isinstance(msg, dict) else None
                    if usage:
                        last = usage
            if last:
                return (last.get("input_tokens", 0)
                        + last.get("cache_read_input_tokens", 0)
                        + last.get("cache_creation_input_tokens", 0))
        except OSError:
            pass

    # 2. Try Gemini brain path
    b_root = brain_root or os.path.expanduser("~/.gemini/antigravity-cli/brain")
    gemini_trans_full = os.path.join(b_root, sid, ".system_generated", "logs", "transcript_full.jsonl")
    gemini_trans = os.path.join(b_root, sid, ".system_generated", "logs", "transcript.jsonl")
    gemini_alt = os.path.join(b_root, f"{sid}.jsonl")

    target_path = None
    for p in (gemini_trans_full, gemini_trans, gemini_alt):
        if os.path.exists(p):
            target_path = p
            break

    if target_path:
        try:
            sz = os.path.getsize(target_path)
            size_kb = sz / 1024.0
            return int(size_kb * 250)
        except OSError:
            pass

    # 3. Try Codex sessions path
    c_root = codex_root or os.path.expanduser("~/.codex/sessions")
    if os.path.isdir(c_root) or codex_root:
        import glob
        c_hits = sorted(glob.glob(os.path.join(c_root, "**", f"*{sid}*.jsonl"), recursive=True))
        if c_hits:
            last_tokens = None
            try:
                with opener(c_hits[0]) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or "token_count" not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if obj.get("type") == "event_msg":
                            payload = obj.get("payload") or {}
                            if payload.get("type") == "token_count":
                                info = payload.get("info") or {}
                                usage = info.get("last_token_usage") or {}
                                tot = usage.get("total_tokens")
                                if tot is not None:
                                    last_tokens = tot
                if last_tokens is not None:
                    return last_tokens
            except OSError:
                pass

    return None


def _capture_status_line(session, capture_fn):
    """Return the tmux status-bar line for a session (the last line carrying the
    context bar glyphs), or "" -- READ-ONLY (`tmux capture-pane -p`)."""
    text = capture_fn(session) or ""
    bar_lines = [ln for ln in text.splitlines() if ("█" in ln or "░" in ln)]
    return bar_lines[-1] if bar_lines else ""


def _tmux_capture(session):
    try:
        r = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def enrich_statuses(status_list, meta, registry, *, capture_fn=None,
                    project_root=None, brain_root=None, codex_root=None,
                    opener=open, only_when_empty=True, codex_ctx_fn=None):
    """Return status dicts augmented with pane_status_line / jsonl_tokens /
    resolved_model. READ-ONLY. By default a session is enriched only when its
    agent-status context_pct is empty (the ones decide() would otherwise miss),
    keeping the extra live reads bounded."""
    capture_fn = capture_fn or _tmux_capture
    agents = (registry or {}).get("agents", {})
    out = []
    for s in status_list:
        s = dict(s)
        session = s.get("session")
        if only_when_empty and s.get("context_pct"):
            out.append(s)   # shared detector already has it; no extra reads
            continue
        entry = (meta or {}).get(session, {})
        reg_entry = agents.get(session, {})

        # Runtime resolution
        rt = (reg_entry.get("runtime") or entry.get("runtime")
              or s.get("process", {}).get("runtime") or s.get("runtime") or "").strip().lower()
        if rt == "agy":
            rt = "gemini"
        if not rt:
            m = (s.get("model") or reg_entry.get("model") or entry.get("model") or "").strip().lower()
            if m.startswith("claude"):
                rt = "claude"
            elif m.startswith("gemini"):
                rt = "gemini"
            elif m.startswith("codex") or "gpt" in m:
                rt = "codex"
            else:
                rt = "claude"

        # Claude: status bar carries █░ context meter
        if rt == "claude":
            s["pane_status_line"] = _capture_status_line(session, capture_fn)
        else:
            # Codex and Gemini do not use Claude █░ status bar; scanning terminal
            # buffers for █░ produces false-positive context readings from commands/diffs.
            s["pane_status_line"] = ""

        s["resolved_model"] = resolve_model(entry, reg_entry)

        # Codex process-bound token rollout reading when context_pct is missing
        if rt == "codex" and not s.get("context_pct"):
            try:
                ctx_getter = codex_ctx_fn
                if ctx_getter is None:
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    import codex_context
                    ctx_getter = codex_context.get_codex_session_context
                res = ctx_getter(session)
                if res:
                    s["context_pct"] = f"{res[0]:.0f}%"
                    s["jsonl_tokens"] = res[1]
            except Exception:
                pass

        if "jsonl_tokens" not in s or s["jsonl_tokens"] is None:
            s["jsonl_tokens"] = jsonl_context_tokens(
                live_sid(entry), entry.get("cwd") or reg_entry.get("cwd") or "",
                project_root=project_root, brain_root=brain_root,
                codex_root=codex_root, opener=opener)
        out.append(s)
    return out
