"""agent_profiles.py — read the agent profiles in config/agent-profiles/ (one JSON file per agent CLI).

A profile says, as data, what OrchestraOS needs to know about an agent: the program names that
mean it is running, how it resumes, where its saved conversation is and in which format, and which
features work for it today. config/agent-profiles/README.md describes the fields.

The four built-in agents' profiles restate the tables the fleet already runs on
(providers._PROCESS_BASENAME, runtime_signatures.PROMPT_SIGNATURES, agent_model's switch tables);
scripts/test_agent_profiles.py fails if they ever disagree, so a profile can replace a table
without changing behaviour.
"""
import glob
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "config" / "agent-profiles"

STATE_SOURCES = {"hooks", "screen", "events-file", "report"}
FORMATS = {"claude-jsonl", "codex-rollout", "antigravity-steps", "grok-updates", "pi-jsonl"}
FINDERS = {"claude-projects", "session-files", "grok-sessions", "reported"}
# Any feature may also be marked as working only in a private tree for now.
UPSTREAM = "private-needs-upstreaming"
FEATURES = {
    "context": {"statusline", "session-log", "events-file", "report", "none"},
    "tokens": {"session-log", "none"},
    "model_switch": {"live", "next-rotation", "none"},
    "menus": {"chat", "single-choice", "terminal-only", "none"},
    "rotation": {"supported", "opt-in", "none"},
}


def problems(p: dict) -> list:
    """What is wrong with a profile, in plain words; empty when it is valid."""
    out = []
    if p.get("format") != 1:
        out.append("format must be 1")
    if not isinstance(p.get("id"), str) or not p["id"]:
        out.append("id is required")
    proc = p.get("process") or {}
    if not (proc.get("basenames") or proc.get("node_scripts")):
        out.append("process needs basenames or node_scripts")
    if (p.get("state") or {}).get("source") not in STATE_SOURCES:
        out.append(f"state.source must be one of {sorted(STATE_SOURCES)}")
    if p.get("prompt_char") is None and (p.get("state") or {}).get("source") != "report":
        out.append("an agent with no prompt_char must report its own state")
    log = p.get("session_log") or {}
    if log.get("format") not in FORMATS:
        out.append(f"session_log.format must be one of {sorted(FORMATS)}")
    for f in log.get("find") or []:
        if f not in FINDERS and not (isinstance(f, str) and f.startswith("glob:")):
            out.append(f"unknown session_log.find entry {f!r}")
    resume = (p.get("launch") or {}).get("resume")
    if resume is not None and not (isinstance(resume, list) and "{session_id}" in resume):
        out.append("launch.resume must be an argv list containing {session_id}, or null")
    for k, allowed in FEATURES.items():
        if p.get(k) not in allowed | {UPSTREAM}:
            out.append(f"{k} must be one of {sorted(allowed | {UPSTREAM})}")
    return out


def load_all(directory=None) -> dict:
    """Every valid profile by id. An invalid file is skipped, never half-used."""
    out = {}
    for path in sorted(Path(directory or PROFILES_DIR).glob("*.json")):
        try:
            p = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(p, dict) and not problems(p) and path.stem == p["id"]:
            out[p["id"]] = p
    return out


def get(runtime, directory=None):
    """The profile for a registry row's runtime; a row with none is Claude (providers.provider_for)."""
    return load_all(directory).get(runtime or "claude")


def works(p: dict, feature: str) -> bool:
    """Whether a feature works in this repository for this agent (not merely somewhere)."""
    return p.get(feature) not in (None, "none", UPSTREAM)


def runtime_for_argv(argv, directory=None):
    """Which profiled agent an argv is: by the program's basename, or for an agent started through
    node, by the script it runs."""
    if not argv:
        return None
    base = os.path.basename(argv[0])
    profiles = load_all(directory)
    for p in profiles.values():
        if base in (p["process"].get("basenames") or []):
            return p["id"]
    if base in ("node", "nodejs", "bun") and len(argv) > 1:
        script = argv[1].replace("\\", "/")
        for p in profiles.values():
            if any(script.endswith(s) for s in p["process"].get("node_scripts") or []):
                return p["id"]
    return None


# ---- where an agent's saved conversation is -------------------------------------------------------

def reported_transcript(tmux_session, orchestra_dir, run=subprocess.run):
    """The transcript_path the agent last reported in its pane state file (the report-in file)."""
    if not tmux_session or not orchestra_dir:
        return None
    try:
        r = run(["tmux", "display-message", "-p", "-t", f"{tmux_session}:", "#{pane_id}"],
                capture_output=True, text=True, timeout=3)
        pane = (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if not pane.startswith("%"):
        return None
    try:
        ev = json.loads((Path(orchestra_dir) / "state" / "agent-events" / "panes" / f"{pane[1:]}.json").read_text())
    except (OSError, ValueError):
        return None
    tp = ev.get("transcript_path") if isinstance(ev, dict) else None
    return tp if isinstance(tp, str) and os.path.isfile(tp) else None


def _grok_updates(sid, home):
    root = Path(os.environ.get("GROK_HOME") or Path(home) / ".grok") / "sessions"
    hits = [p for p in glob.glob(str(root / "*" / sid)) if os.path.isdir(p)]
    path = Path(hits[0]) / "updates.jsonl" if len(hits) == 1 else None
    return str(path) if path and path.is_file() else None


def find_session_log(p: dict, record: dict, orchestra_dir=None, home=None, run=subprocess.run):
    """The agent's saved conversation file, trying the profile's finders in order; None if not found.
    `record` is the seat's registry/session record (session_id, cwd, tmux_session)."""
    home = str(home or os.path.expanduser("~"))
    sid = record.get("session_id")
    for how in (p.get("session_log") or {}).get("find") or []:
        hit = None
        if how == "reported":
            hit = reported_transcript(record.get("tmux_session"), orchestra_dir, run=run)
        elif not sid:
            continue
        elif how == "claude-projects":
            hits = glob.glob(str(Path(home) / ".claude" / "projects" / "*" / f"{glob.escape(sid)}.jsonl"))
            hit = hits[0] if hits else None
        elif how == "session-files":
            from scripts.sid_invariants import find_transcript
            hit = find_transcript(sid, projects_root=Path(home) / ".claude" / "projects",
                                  gemini_brain_root=Path(home) / ".gemini" / "antigravity-cli" / "brain",
                                  codex_sessions_root=Path(home) / ".codex" / "sessions")
        elif how == "grok-sessions":
            hit = _grok_updates(sid, home)
        elif how.startswith("glob:"):
            pattern = os.path.expanduser(how[5:].replace("~", home, 1) if how[5:].startswith("~") else how[5:])
            hits = sorted(glob.glob(pattern.replace("{session_id}", glob.escape(sid))))
            hit = hits[-1] if hits else None
        if hit:
            return Path(hit)
    return None
