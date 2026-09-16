"""green_quota — vendor-quota truth for a booted GREEN (DEC follow-through, gm msg_7b3ffa11).

By effect 2026-09-16 01:32Z: leg (ii) promoted a codex green whose team workspace credits
were depleted on a premium model; the readiness/swap gates never looked. Provider-agnostic
DATA lookup (same discipline as CID_RESOLVER_REGISTRY): GREEN_QUOTA_READERS[runtime] ->
fn(green_alias, sid) -> {exhausted, kind, limit_id, reset_at, detail} or None (unknown).
Only a POSITIVE detection holds a fire; None / unreadable => the caller proceeds as today.
  * codex : the green's OWN rollout, latest event_msg token_count.rate_limits record.
            exhausted iff rate_limit_reached_type is set, OR limit_id == premium AND
            credits.has_credits is false AND not credits.unlimited. kind = credits (does not
            self-recover) | window (reset_at from primary/secondary resets_at).
  * claude: pane text (profile_switcher.detect_exhaustion_text) — a weak secondary signal,
            reported only when POSITIVE (a clean pane proves nothing -> None).
  * gemini: no source seen by effect yet -> not registered (never guess).
"""
import json
import os
import subprocess
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _latest_rate_limits(path):
    """The rate_limits dict of the LAST token_count event in a codex rollout, else None."""
    last = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "rate_limits" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                payload = obj.get("payload") or {}
                rl = payload.get("rate_limits")
                if isinstance(rl, dict):
                    last = rl
    except OSError:
        return None
    return last


def _window_reset(rl):
    for k in ("primary", "secondary"):
        w = rl.get(k) or {}
        if isinstance(w, dict) and w.get("resets_at"):
            return w.get("resets_at")
    return None


def codex_quota(sid, *, rollout_path_fn=None):
    """Codex reader over the green's own rollout (see module doc). None when no rollout or
    no rate_limits record has been written yet (unknown, never assumed exhausted)."""
    if not sid:
        return None
    if rollout_path_fn is None:
        from .ctx_adapters import _source_path_codex
        rollout_path_fn = _source_path_codex
    path = rollout_path_fn(sid)
    if not path:
        return None
    rl = _latest_rate_limits(path)
    if rl is None:
        return None
    credits = rl.get("credits") or {}
    limit_id = rl.get("limit_id")
    reached = rl.get("rate_limit_reached_type")
    credits_out = (limit_id == "premium" and credits.get("has_credits") is False
                   and not credits.get("unlimited"))
    if reached and "credit" in str(reached):
        credits_out = True
    if credits_out:
        return {"exhausted": True, "kind": "credits", "limit_id": limit_id,
                "reset_at": None, "detail": reached or "premium:has_credits=false"}
    if reached:
        return {"exhausted": True, "kind": "window", "limit_id": limit_id,
                "reset_at": _window_reset(rl), "detail": reached}
    return {"exhausted": False, "kind": None, "limit_id": limit_id,
            "reset_at": _window_reset(rl), "detail": None}


def pane_text_quota(lines):
    """Secondary signal from pane text via the shared exhaustion detector."""
    sys.path.insert(0, _SCRIPTS) if _SCRIPTS not in sys.path else None
    from profile_switcher import detect_exhaustion_text
    exhausted, reset_at = detect_exhaustion_text("\n".join(lines or []))
    return {"exhausted": bool(exhausted), "kind": "window" if exhausted else None,
            "limit_id": None, "reset_at": reset_at, "detail": "pane-text" if exhausted else None}


def _capture(target):
    out = subprocess.run(["tmux", "capture-pane", "-t", target, "-p"],
                         capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "capture-pane failed")
    return out.stdout.splitlines()


def _codex_reader(green_alias, sid, capture_fn=None):
    r = codex_quota(sid)
    if r is not None:
        return r
    # no rollout record yet: fall back to the pane text, positive only
    try:
        p = pane_text_quota((capture_fn or _capture)(green_alias))
    except Exception:  # noqa: BLE001 — unreadable pane => unknown
        return None
    return p if p["exhausted"] else None


def _claude_reader(green_alias, sid, capture_fn=None):
    try:
        p = pane_text_quota((capture_fn or _capture)(green_alias))
    except Exception:  # noqa: BLE001
        return None
    return p if p["exhausted"] else None      # a clean pane proves nothing


GREEN_QUOTA_READERS = {
    "codex": _codex_reader,
    "claude": _claude_reader,
}


def green_quota_any(runtime, green_alias, sid, **kw):
    """Registry entry point: the green's quota state by its runtime, or None (unknown
    runtime / no reader / any error). Never raises."""
    fn = GREEN_QUOTA_READERS.get((runtime or "").strip().lower())
    if fn is None:
        return None
    try:
        return fn(green_alias, sid, **kw)
    except Exception:  # noqa: BLE001 — fail-soft: unknown
        return None
