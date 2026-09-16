"""Live-fleet -> decide() input adapter (pure).

Shapes the output of `scripts/agent-status.py --all` (plus registry
metadata and per-session ENRICHMENT -- pane status line + jsonl token
count, added by the entry module) into the observation dicts that decide()
consumes. Every function here is pure: the actual (read-only) live reads
happen in the entry / enrich modules and are injected in as extra keys on
each status dict.

ctx% source priority (per WS1 observe-stage finding 2026-08-12 -- the shared
agent-status parser returns context_pct='' for the '████ 86%' and skull
'0% until auto-compact' bar formats, so decide() noop'd the exact agents that
should trigger):
  1. agent-status.py context_pct   (when the shared detector DID parse it)
  2. the tmux pane status-bar %     (authoritative; daemon-local fallback)
  3. jsonl_tokens / effective_ceiling(model)   (deepest fallback)

Death: the skull 💀 / '0% until auto-compact' pane marker sets death.exhausted
-> death_signal 'exhausted' -> hard_rotate (a full-context zombie the token
math alone reads as merely SOFT).
"""

import os
import re
import sys
from typing import Optional

from scripts.lineage_daemon.ctxstate import effective_ceiling

# CORE seat predicate (DEC-1787657323) — single-sourced in runtime_signatures,
# consumed here for the ROTATION domain gate (rotation-eligible = CORE AND ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runtime_signatures import is_claude_stop_hook_seat  # noqa: E402

# A run of the bar block is drawn with these glyphs; the status line we want is
# the one carrying the context meter.
_BAR_GLYPHS = "█░"
_PCT_RE = re.compile(r"(\d+)\s*%")


def parse_pct(s) -> Optional[int]:
    """Extract the integer immediately preceding a '%'.

    "29%"->29, "8%"->8, ""->None, None->None, "ctx:36%"->36.
    Returns None when there is no '%' with a preceding integer.
    """
    if not s:
        return None
    idx = s.find("%")
    if idx <= 0:
        return None
    j = idx
    while j > 0 and s[j - 1].isdigit():
        j -= 1
    digits = s[j:idx]
    if not digits:
        return None
    return int(digits)


def pane_pct(status_line) -> Optional[int]:
    """Parse the context % from a captured tmux status-bar line.

    Handles the '████████░░ 86%' bar the shared agent-status parser misses.
    Returns the LAST integer-percent on the line (the meter sits at the right),
    or None. A skull/auto-compact line ('0% until auto-compact') yields 0 here,
    but exhaustion is caught separately by pane_exhausted() and dominates.
    """
    if not status_line:
        return None
    matches = _PCT_RE.findall(status_line)
    if not matches:
        return None
    return int(matches[-1])


def pane_exhausted(status_line) -> bool:
    """True iff the pane status bar shows a context-exhausted / skull state.

    Markers: the skull glyph 💀, or '0% until auto-compact' (0% headroom left).
    Either means the agent is wedged at the context wall -> a death signal.
    """
    if not status_line:
        return False
    if "\U0001f480" in status_line:      # 💀
        return True
    return bool(re.search(r"0\s*%\s*until\s+auto-?compact", status_line, re.I))


def jsonl_fallback_pct(jsonl_tokens, model) -> Optional[int]:
    """jsonl token count / effective ceiling, as an integer percent.

    Returns None when jsonl_tokens is absent, and None for an UNKNOWN model
    (incident inspiration bg 2026-09-15: model='unknown' silently took the
    160k "standard" ceiling while the live session ran a 1M window ->
    229k/160k = 143 "%", stored as last_valid_ctx_pct=1.43). Never guess a
    ceiling for an unknown model. Uses the auto-compact-aware effective
    ceiling so a known model's result agrees with the TUI status bar.
    """
    if jsonl_tokens is None:
        return None
    if not model or str(model).strip().lower() == "unknown":
        print("ctx:jsonl-fallback-skipped model=unknown", file=sys.stderr)
        return None
    return round(jsonl_tokens / effective_ceiling(model) * 100)


def _range_guarded(p, source, seat) -> Optional[int]:
    """0..100 or None; an out-of-range value is dropped LOUDLY so the caller
    falls through to the next source instead of propagating a lie."""
    if p is None:
        return None
    if not 0 <= p <= 100:
        print(f"ctx:out-of-range source={source} value={p} seat={seat}",
              file=sys.stderr)
        return None
    return p


def resolve_ctx_pct(status: dict) -> Optional[int]:
    """The best available context % for a status dict, by source priority.
    Final return is None or an int in 0..100 — each source is range-guarded
    (the 143% incident class: a wrong-ceiling percent must never reach
    decide()/bg state)."""
    seat = status.get("session")
    p = _range_guarded(parse_pct(status.get("context_pct")),
                       "context_pct", seat)
    if p is not None:
        return p
    p = _range_guarded(pane_pct(status.get("pane_status_line")), "pane", seat)
    if p is not None:
        return p
    return _range_guarded(
        jsonl_fallback_pct(status.get("jsonl_tokens"),
                           status.get("resolved_model")), "jsonl", seat)


def build_agent(status: dict, reg_entry: Optional[dict]) -> dict:
    """Produce a decide() input dict from one (enriched) status + registry entry.

    Consumes optional enrichment keys added upstream by the entry/enrich module:
      pane_status_line : raw captured tmux status-bar line (str)
      jsonl_tokens     : int context tokens from the live .jsonl
      resolved_model   : model string (from resume_command sid / registry)
    All are optional -- absent means "fall through to the next source" (and, for
    a hermetic unit test, build_agent stays pure over whatever is passed).
    """
    reg = reg_entry or {}
    model = (status.get("resolved_model") or reg.get("model")
             or status.get("model") or "")
    return {
        "agent_id": status["session"],
        "tier_class": reg.get("tier") or "T2",
        # Piece 2 (2b/2c): the "which seats does the beat evaluate at all" fields.
        # runtime feeds the CORE seat predicate (skip non-claude); lineage_status /
        # succeeded_by let the beat skip retired/quiescent/superseded predecessors.
        "runtime": _resolve_runtime(reg, model),
        "lineage_status": reg.get("status"),
        "succeeded_by": reg.get("succeeded_by"),
        "ctx": {
            "status_bar_pct": resolve_ctx_pct(status),
            "jsonl_tokens": status.get("jsonl_tokens"),
            "model": model,
        },
        "death": {
            "court": False,
            "exhausted": pane_exhausted(status.get("pane_status_line")),
            "session_alive": True,
            "jsonl_growing": True,
            "state": status.get("state"),
            "state_age_s": status.get("state_age_s") or 0,
        },
    }


def _resolve_runtime(reg: dict, model: str) -> str:
    """The seat's runtime for the lineage beat predicate, from the registry entry (pure).
    Mirrors runtime_signatures.agent_runtime's dict logic without a file read:
    explicit `runtime` wins; else a claude/gemini/codex model row is matched;
    else fail-safe to 'claude' (never widen the rotation surface for an unrecognized
    row — the domain gate below still requires the other rotation conditions)."""
    rt = (reg.get("runtime") or "").strip().lower()
    if rt:
        return "gemini" if rt == "agy" else rt
    m = (model or "").strip().lower()
    if m.startswith("claude"):
        return "claude"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("codex"):
        return "codex"
    return "claude"


# Statuses whose rows are NOT live occupants of a seat — a predecessor that has
# rotated out (retired) or is parked (quiescent) must never be nudged/rotated.
_INERT_STATUSES = frozenset({"retired", "quiescent"})
_SUPPORTED_RUNTIMES = frozenset({"claude", "gemini", "codex"})


def beat_skip_reason(agent: dict) -> Optional[str]:
    """The ONE 'which seats does the beat evaluate at all' predicate (pure).

    Returns None when the beat SHOULD evaluate the seat, else a short skip reason
    (effect, not prose). This is the ROTATION domain gate: rotation-eligible =
    SUPPORTED_RUNTIME(claude, gemini, codex) AND live-occupant (not retired/quiescent) AND
    not-already-superseded (succeeded_by unset).

    Order is deterministic (runtime -> status -> superseded) so a row that would
    skip for multiple reasons logs a stable reason.
    """
    rt = (agent.get("runtime") or "claude").strip().lower()
    if rt == "agy":
        rt = "gemini"
    if rt not in _SUPPORTED_RUNTIMES:
        return "unsupported-runtime"
    if (agent.get("lineage_status") or "") in _INERT_STATUSES:
        return "retired-or-quiescent"
    if agent.get("succeeded_by"):
        return "succeeded-by-set"
    return None


def collect_fleet(status_list: list, registry: dict) -> list:
    """Map every status dict to a decide() input, attaching its tier (pure)."""
    agents = registry.get("agents", {})
    return [build_agent(s, agents.get(s["session"])) for s in status_list]
