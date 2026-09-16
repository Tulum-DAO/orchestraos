# focus_tool.py — the `focus_entity(spoken_ref)` voice tool (spec §3.4) + observe-only gate (§6).
#
# Wires the pure resolver to real state: gather live candidates → resolve() → on `match` assert the
# voice focus + speak the pick; on `ambiguous` speak the one-line either/or (no focus change); on
# `none` ask a SHARP best-guess. Everything here is local reads + local-state writes — NO new
# mutation authority (deliberation still acts only through the existing ledger answer path).
#
# OBSERVE-ONLY FIRST (spec §6): with `observe=True` (the initial rollout) the tool RESOLVES + LOGS
# scores and the would-pick but does NOT switch focus — thresholds tune empirically before the
# auto-focus path is armed. Same discipline that caught the sim-gate / emission-lint over-reach.

import logging
import time as _time

from services.arturo.resolve import resolve, _phrase
from services.arturo.resolve_providers import gather_candidates
from services.arturo.surface import assert_focus

_log = logging.getLogger("arturo.focus")


def _confirm_line(ent):
    """Spoken confirmation for a matched focus switch (hydrates from_agent/feature top-of-mind)."""
    label = (ent.get("label") or ent.get("id") or "it").strip()
    kind = ent.get("kind")
    frm, feat = ent.get("from_agent"), ent.get("feature")
    if kind == "approval":
        s = f"Switched to the {label} approval"
        if frm:
            s += f" from {frm}"
        if feat:
            s += f" — feature {feat}"
        return s + "."
    if kind == "content":
        return f"Focusing on {frm}'s output — {label}." if frm else f"Focusing on {label}."
    if kind == "agent":
        return f"Focusing on the {label} agent."
    if kind == "project":
        return f"Focusing on the {label} project."
    return f"Focusing on {label}."


def _record(log, spoken_ref, res, observe):
    """Emit the observe-only telemetry: the resolve scores + what it WOULD pick, every call."""
    rec = {
        "spoken": spoken_ref, "status": res["status"], "observe": bool(observe),
        "would_pick": (res["entity"] or {}).get("id"),
        "scores": res.get("scores") or [],
    }
    try:
        (log or _default_log)(rec)
    except Exception:
        pass
    return rec


def _default_log(rec):
    _log.info("resolve %s", rec)


def focus_entity(spoken_ref, gw_get, get_output=None, focused_agent=None, named_agents=None,
                 now=None, focus_path=None, observe=True, log=None):
    """Resolve a spoken reference over the live candidate union and act on it (spec §3.4).
    Returns a SPOKEN-ready string. `gw_get`/`get_output` are injected callables (unit-testable);
    `focus_path` is state/arturo/focus.json. When `observe` is True, focus is NOT switched — the
    call only resolves + logs (thresholds-tuning rollout, §6)."""
    now = now if now is not None else _time.time()
    candidates = gather_candidates(gw_get, get_output=get_output, focused_agent=focused_agent,
                                   named_agents=named_agents, now=now)
    res = resolve(spoken_ref, candidates, now)
    _record(log, spoken_ref, res, observe)

    if res["status"] == "match":
        ent = res["entity"]
        confirm = _confirm_line(ent)
        if observe:
            # log-only: name the pick so it's audible + the scores are captured, but DON'T switch.
            return (f"[observe] I'd focus {_phrase(ent)} — resolver is in observe mode, "
                    f"not switching yet.")
        if focus_path:
            assert_focus(ent, "voice", now=now, focus_path=focus_path)
        return confirm

    # ambiguous (either/or) or none (sharp best-guess) → speak the resolver line, never switch.
    return res["said"]
