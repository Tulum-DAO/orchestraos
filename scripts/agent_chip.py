"""agent_chip.py: the one status chip every surface draws for an agent row.

Computed ONCE in the gateway (/agents rows gain `chip`), so the dashboard, iOS, iPad, Watch and the
Mac grid render the same thing from one source (docs/AGENT_CHIP.md):

    {"name": "gm", "provider": "claude", "model_label": "Opus 5.5",
     "status": "working" | "needs_you" | "idle" | "down", "context_left_pct": 92 | null}

* name     : OUR seat name. The provider is the badge, never the name.
* provider : claude | codex | gemini | grok | null. The live process's runtime, else the registry's
             runtime, so a stopped seat still gets its badge. Draw it with the logo of that id in
             config/providers.json (`logo_svg`): ONE mark set for every surface.
* model    : a short label for hover/tap, from the model id (never invented: an id we cannot
             shorten is shown as it is).
* status   : needs_you = a permission prompt, a pending menu, or a pending card this seat filed
             (waiting on Shaw); down = crashed / stalled / stranded / offline / stopped; working;
             else idle.
* context_left_pct : 100 - context used of the WINDOW, or null when there is no reading. Never 0 or
             100 for "unknown".
"""
from __future__ import annotations

import json
import os
import re

PROVIDERS = ("claude", "codex", "gemini", "grok")
_ALIASES = {"antigravity": "gemini", "agy": "gemini", "openai": "codex", "anthropic": "claude",
            "xai": "grok"}
NEEDS_YOU = {"waiting", "stranded"}
DOWN = {"crashed", "stalled", "offline", "stopped"}

_CLAUDE_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
_STATIC_LABELS: dict[str, str] | None = None


def provider_id(*candidates) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            p = c.strip().lower()
            p = _ALIASES.get(p, p)
            if p in PROVIDERS:
                return p
    return None


def _static_labels() -> dict[str, str]:
    """id -> label from config/providers.json's static model lists (gemini, grok, ...)."""
    global _STATIC_LABELS
    if _STATIC_LABELS is None:
        out: dict[str, str] = {}
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "providers.json")
        try:
            for p in json.load(open(path)).get("providers", []):
                for m in (p.get("model_catalog") or {}).get("static") or []:
                    if isinstance(m, dict) and isinstance(m.get("id"), str) and isinstance(m.get("label"), str):
                        out[m["id"]] = m["label"]
        except (OSError, ValueError, AttributeError):
            pass
        _STATIC_LABELS = out
    return _STATIC_LABELS


def model_label(model) -> str | None:
    """'claude-opus-5-5[1m]' -> 'Opus 5.5'; a catalog id -> its catalog label; otherwise the id."""
    if not isinstance(model, str) or not model.strip():
        return None
    m = model.strip()
    if " " in m:
        return m                                   # already a display name
    base = re.sub(r"\[[^\]]*\]$", "", m)
    hit = _CLAUDE_RE.match(base)
    if hit:
        family, major, minor = hit.groups()
        return f"{family.capitalize()} {major}" + (f".{minor}" if minor else "")
    return _static_labels().get(base, base)


def chip_status(state, has_pending_menu=False, pending_cards=0) -> str:
    if has_pending_menu or (pending_cards or 0) > 0 or state in NEEDS_YOU:
        return "needs_you"
    if state in DOWN:
        return "down"
    if state == "working":
        return "working"
    return "idle"


def context_left(used_of_window) -> int | None:
    if isinstance(used_of_window, bool) or not isinstance(used_of_window, (int, float)):
        return None
    if not 0 <= used_of_window <= 100:
        return None
    return int(round(100 - used_of_window))


def chip_for(row: dict, registry_runtime=None, pending_cards: int = 0) -> dict:
    return {
        "name": row.get("id"),
        "provider": provider_id(row.get("provider"), registry_runtime),
        "model_label": model_label(row.get("model")),
        "status": chip_status(row.get("state"), bool(row.get("has_pending_menu")), pending_cards),
        "context_left_pct": context_left(row.get("context_pct_of_window")),
    }
