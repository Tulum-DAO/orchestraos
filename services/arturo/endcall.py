# endcall.py — finalize + sanitized summary + frozen marker + gm injection + drop-watchdog.
import re
import json as _json
import os as _os
import time as _time
from pathlib import Path as _Path

from services.arturo.call_journal import _atomic_write

VC_RE = re.compile(r"^vc_[A-Za-z0-9_-]{4,64}\Z")

# Control/format classes stripped (B2): C0+DEL, C1 (U+0080-9F), bidi overrides/isolates
# (U+202A-202E, U+2066-2069), zero-width + directional marks (U+200B-200F), line/para
# separators (U+2028/2029), BOM (U+FEFF). This text crosses into the LIVE gm composer +
# is rendered in the shared chat grammar, so visual-spoofing of the frozen marker matters.
# NB \t (\x09) IS stripped (AGY): a tab into the tmux composer can trigger tab-completion.
_CTRL_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")


def sanitize_summary(raw: str) -> str:
    # Model text derived from USER speech → treat like an attachment filename (spec §7.2.2):
    # allow a SINGLE \n between the two lines; strip all other control/format chars; cap ~300.
    raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = raw.split("\n")
    lines = [_CTRL_RE.sub("", p).strip() for p in parts if p.strip()]
    s = "\n".join(lines[:2])
    return s[:300]


def build_full_transcript(turns, max_chars=6000):
    """the operator REQUIREMENT (2026-08-10): gm receives the call IN ENTIRETY, never fragments — the
    end-of-call injection must carry the FULL chronological transcript (not a 2-line summary),
    so gm can mine it for the operator's commissions/feedback. Each turn is sanitized like the summary
    (control/bidi-safe, §7.2 class) since it's model+user speech entering gm's composer; capped
    to keep the injection bounded (the frozen [voice-call:] marker + JSON path still carry the
    canonical complete record for anything truncated)."""
    lines = []
    for t in turns:
        role = t.get("role")
        if role == "tool":
            lines.append(f"[tool: {t.get('tool', '?')}]")
        elif role in ("user", "arturo"):
            who = "the operator" if role == "user" else "Arturo"
            txt = sanitize_summary(t.get("text", "")).replace("\n", " ")
            if txt:
                lines.append(f"{who}: {txt}")
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…(truncated — full transcript in the linked JSON)"
    return body


def build_marker(call_id: str, abs_path: str) -> str:
    if not VC_RE.match(call_id):
        raise ValueError("bad call_id")
    # abs_path is server-constructed, but validate defensively (AGY): absolute, no control
    # chars, and the basename must be this exact call_id's file — no smuggling into the marker.
    if not (abs_path.startswith("/") and abs_path.endswith(f"/{call_id}.json")
            and not re.search(r"[\x00-\x1f\x7f]", abs_path)):
        raise ValueError("bad abs_path")
    return f"[voice-call: {call_id} {abs_path}]"


def _default_post(session, text):
    # POST the hardened gateway seam; returns the HTTP status code.
    # ISOLATION GUARD (gm 2026-08-11 flagged a real leak): a finalize test whose gm-inject wasn't
    # stubbed — or whose async inject thread outlived a monkeypatch teardown — reached the LIVE gm
    # inbox with a pytest temp path. Hard-block the real network POST whenever running under pytest,
    # independent of monkeypatch discipline. Returns a non-200/non-409 code → inject_to_gm treats it
    # as a structural stop (no retry, no outbox), so no test can ever hit the live gateway.
    if _os.environ.get("PYTEST_CURRENT_TEST"):
        return 599
    import urllib.request
    import urllib.error
    from services.arturo import gateway_conf as gc
    body = _json.dumps({"session": session, "text": text, "accepts": ["held"]}).encode()
    req = urllib.request.Request(gc.AGENT_MESSAGE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {gc.token()}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def inject_to_gm(session, summary, marker, post=None, max_attempts=6, base_delay=1.0, transcript=None):
    # the operator REQUIREMENT: gm receives the call IN ENTIRETY. When `transcript` (already per-turn
    # sanitized by build_full_transcript) is provided, the injected message carries the FULL
    # chronological transcript + the frozen marker (path to the canonical JSON for anything
    # truncated). Falls back to the 2-line summary when no transcript is passed. 409 = gm busy →
    # park-and-retry; on give-up the durable JSON stays on disk.
    post = post or _default_post
    summary = sanitize_summary(summary)   # defense-in-depth: last gate before gm's composer
    if transcript:
        text = (f"Voice call ended ({summary.splitlines()[0] if summary else 'complete'}). "
                f"FULL TRANSCRIPT below — mine it for the operator's requests/feedback:\n\n"
                f"{transcript}\n\n{marker}")
    else:
        text = f"Voice call ended: {summary}\n{marker}"
    for attempt in range(1, max_attempts + 1):
        code = post(session, text)
        if code == 200:
            return True, attempt
        if code == 409:
            if base_delay:
                _time.sleep(min(base_delay * (2 ** (attempt - 1)), 30))
            continue
        return False, attempt          # 401/404/502 → structural, don't spin
    return False, max_attempts


def sweep_dropped(dir, idle_secs=120):
    # A "live" JSON with no mtime movement in idle_secs is a dropped call (proxy crash /
    # network) → flip status→ended so no permanent ☎ badge (spec §7.3 stale-call guard).
    flipped = []
    for p in _Path(dir).glob("vc_*.json"):
        try:
            if _time.time() - _os.path.getmtime(p) < idle_secs:
                continue
            d = _json.loads(p.read_text())
            if d.get("status") == "live":
                d["status"] = "ended"
                if not d.get("summary"):
                    d["summary"] = "Call dropped (no activity)."
                _atomic_write(p, d)
                flipped.append(d.get("call_id"))
        except Exception:
            continue
    return flipped
