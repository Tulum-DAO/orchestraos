"""deliver_now.py: the "Deliver now" waiver, and what is waiting for an agent.

Mail to an agent waits while the router's gates say not yet (scripts/message-router.py). Some of
those gates only protect the human's attention: the per-agent cooldown, an attached and active
terminal, the breathe window after recent activity, and (for Claude only) a turn in progress, since
Claude Code queues typed input mid-turn. A person who presses "Deliver now" waives exactly those SOFT
gates, for one session, for WAIVER_S. The HARD gates are never waived: text the human typed and has
not sent, a permission prompt, a stranded paste chip, Codex or Grok mid-turn. Injecting there
clobbers the human or corrupts the turn.

Only the gateway's POST /deliver-now writes a waiver (never an agent); the router reads it and logs
DELIVER-NOW with who pressed it.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

WAIVER_S = 5 * 60
_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# The router's hold reasons (message-router.py note_hold), in the words a person reads.
REASONS = {
    "attached-active": "a terminal is open on it and in use",
    "breathe-window": "it was active a moment ago",
    "not-idle": "it is busy (mid-turn, a prompt on screen, or text typed in its box)",
    "cooldown": "it just got a message",
}


def waiver_dir(data_dir) -> Path:
    return Path(data_dir) / "state" / "deliver-now"


def _path(data_dir, session: str) -> Path:
    if not isinstance(session, str) or not _SESSION_RE.match(session):
        raise ValueError(f"not a session name: {session!r}")
    return waiver_dir(data_dir) / f"{session}.json"


def write_waiver(data_dir, session: str, by: str, now: float | None = None) -> dict:
    """Record that `by` pressed Deliver now for `session`. Only the gateway route calls this."""
    import tempfile
    now = time.time() if now is None else now
    path = _path(data_dir, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"session": session, "by": str(by or "unknown")[:64], "at": int(now),
           "expires_at": int(now + WAIVER_S)}
    fd, tmp = tempfile.mkstemp(prefix=f".{session}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return rec


def active_waiver(data_dir, session: str, now: float | None = None) -> dict | None:
    """The waiver for this session if one is in force, else None (missing, expired, malformed)."""
    now = time.time() if now is None else now
    try:
        rec = json.loads(_path(data_dir, session).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("session") != session:
        return None
    exp = rec.get("expires_at")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or now >= exp:
        return None
    return rec


def hold_reason(holds: dict, session: str) -> str | None:
    """The router's most recent hold reason for this session, in plain words, from its hold file
    (message-router-holds.json, keyed "session|reason")."""
    best, best_at = None, -1.0
    for key, entry in (holds or {}).items():
        if not isinstance(key, str) or "|" not in key:
            continue
        sess, reason = key.split("|", 1)
        if sess != session or not isinstance(entry, dict):
            continue
        # note_hold stamps msgs[<id>].seen_at every cycle a row is held, logged_at when it logs.
        seen = [m.get("seen_at") for m in (entry.get("msgs") or {}).values() if isinstance(m, dict)]
        seen.append(entry.get("logged_at"))
        nums = [x for x in seen if isinstance(x, (int, float)) and not isinstance(x, bool)]
        at = max(nums) if nums else 0
        if at > best_at:
            best, best_at = reason, float(at)
    return REASONS.get(best, best) if best else None


def waiting_summary(rows: list, holds: dict, session: str, now: float | None = None) -> dict:
    """{count, messages: [{id, from, subject, age_s}], reason} for an agent's pending mail."""
    now = time.time() if now is None else now
    msgs = []
    for r in rows:
        created = r.get("created_at")
        try:
            from datetime import datetime
            age = max(0, int(now - datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()))
        except (TypeError, ValueError):
            age = None
        msgs.append({"id": r.get("id"), "from": r.get("from_agent"),
                     "subject": (r.get("subject") or "")[:120], "age_s": age})
    reason = hold_reason(holds, session) if msgs else None
    if msgs and reason is None:
        reason = "waiting for the next delivery pass (about a minute)"
    return {"count": len(msgs), "messages": msgs, "reason": reason}
