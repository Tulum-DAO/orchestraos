"""First-class death-signal classifier (pure).

Court-glitch, OOM (session death), and wedged-stall are treated as
death signals in their own right, not merely as context-% pressure.
This module does NOT detect these conditions itself -- it purely
classifies observations passed in by the caller.
"""

from typing import Optional


def death_signal(obs: dict) -> Optional[str]:
    """Classify observations into the first matching death signal.

    Priority order (first match wins):
      1. "court"     if obs["court"] is True
      2. "exhausted" if obs["exhausted"] is True (ctx-ceiling / auto-compact
                     skull -- the TUI shows 💀 + "0% until auto-compact"; the
                     agent is wedged at the context wall and cannot progress)
      3. "oom"       if obs["session_alive"] is False (session gone == hard death)
      4. "wedged"    if obs["state"] == "stalled" and obs["state_age_s"] >= 600
    Otherwise None.
    """
    if obs.get("court") is True:
        return "court"
    if obs.get("exhausted") is True:
        return "exhausted"
    if obs.get("session_alive") is False:
        return "oom"
    if obs.get("state") == "stalled" and obs.get("state_age_s", 0) >= 600:
        return "wedged"
    return None
