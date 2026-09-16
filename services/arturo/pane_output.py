# pane_output.py — mechanical ghost/draft-stripping for Arturo's get_agent_output
# (gm msg_91dd9aa5, the operator-caught live on vc_client_573f6b8afffe).
#
# THE DEFECT: get_agent_output captured `tmux capture-pane -p` (plain text, no SGR) and
# returned the WHOLE pane including the composer line. A CLI GHOST SUGGESTION sitting in
# gm's composer ("Give orchestra-builder something to do…") was reported as gm's actual
# last statement. Text alone carries no ghost signal.
#
# THE FIX (mechanism, not model): capture with `-e` (SGR preserved), then classify the
# composer with the FLEET-BLESSED agent-status style-walk (composer-is-a-draft-surface +
# ghost-vs-typed law): reverse-video cursor at char 0 + SGR-2 faint body = GHOST (a CLI
# suggestion, NOT agent output); default-styled composer text = an unsubmitted DRAFT (not
# delivered); real agent output = content ABOVE the input box only. This module is the
# single consumer of that primitive — it does NOT reinvent a second ghost detector.
import importlib.util as _ilu
import pathlib as _pl

_ORCHESTRA = _pl.Path(__file__).resolve().parent.parent.parent


_AGENT_STATUS = None


def _load_agent_status():
    """Import scripts/agent-status.py (hyphenated → importlib) for its style-walk."""
    p = _ORCHESTRA / "scripts" / "agent-status.py"
    spec = _ilu.spec_from_file_location("agent_status", p)
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _agent_status_mod():
    """Memoized agent-status module — exec'd at most once (voice hot path).
    reviewer DEC-1787087455: avoid re-exec'ing ~500 lines on every tool call."""
    global _AGENT_STATUS
    if _AGENT_STATUS is None:
        _AGENT_STATUS = _load_agent_status()
    return _AGENT_STATUS


def split_agent_pane(raw_ansi, find_chrome=None, strip_ansi=None):
    """Split a raw (SGR-preserving) tmux pane into {output, ghost, draft}.

    output = agent's real content ABOVE the live input box (ANSI-stripped).
    ghost  = the composer's CLI ghost suggestion, if any (NOT agent output).
    draft  = the composer's unsubmitted typed text, if any (NOT delivered).

    `find_chrome`/`strip_ansi` are injected for testing; default to the blessed
    agent-status primitives so ghost detection has ONE source of truth."""
    if find_chrome is None or strip_ansi is None:
        _as = _agent_status_mod()
        find_chrome = find_chrome or _as._find_chrome
        strip_ansi = strip_ansi or _as.strip_ansi

    raw_lines = raw_ansi.splitlines()
    stripped = [strip_ansi(l) for l in raw_lines]
    chrome = find_chrome(stripped, raw_lines)

    if not chrome:
        # no input box on screen → the whole capture is scrollback output
        return {"output": "\n".join(stripped).strip(), "ghost": "", "draft": ""}

    # real output = everything strictly ABOVE the box's top framing rule
    box_top = chrome.get("box_top", len(stripped))
    output = "\n".join(stripped[:box_top]).strip()
    return {
        "output": output,
        "ghost": (chrome.get("composer_ghost") or "").strip(),
        "draft": (chrome.get("composer_text") or "").strip(),
    }


def render_for_voice(parts):
    """Render split parts for Arturo, so a ghost is NEVER presented as agent output
    and a draft is explicitly tagged not-delivered."""
    out = parts.get("output", "").strip()
    lines = [out] if out else []
    if parts.get("draft"):
        lines.append(f"[composer draft, NOT delivered: {parts['draft']}]")
    # ghost is a CLI suggestion — intentionally NOT surfaced as agent speech; drop it.
    if not lines:
        return "(no delivered output on screen)"
    return "\n".join(lines)
