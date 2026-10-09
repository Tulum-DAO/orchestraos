"""Where the operator is in onboarding, as server state (DEC-1791511578959986).

The operator, 2026-10-09: "Arturo should always know how far along in the onboarding a user is in
case they leave part way through and come back." The operator facts (operator_store) already hold
their name and devices, and the team is seen live (starter_team_state). This file holds the rest,
written only by server code, never by the brain:

  onboarding_conversation   the onboarding thread. Every browser and device opens THIS one while
                            onboarding is not finished, so leaving and coming back resumes it.
  team_declined_at          the operator said "not now" to the starter team on a dashboard turn.

Which devices are paired is read from the device store (Arturo's own codes), never stored here.
Reset: delete this file together with onboarding.json (docs/ARTURO.md).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

# Every write is read-modify-write: one at a time, or two openers (or an opener and a decline) lose an
# update or both "win" the pin.
_LOCK = threading.Lock()

FILENAME = "onboarding-progress.json"
PAIR_MINTER = "arturo-onboarding"          # arturo-proxy.py _PAIR_MINTER


def _path(state_dir) -> Path:
    return Path(state_dir) / FILENAME


def read(state_dir) -> dict:
    try:
        rec = json.loads(_path(state_dir).read_text())
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(state_dir, rec):
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=FILENAME + ".", suffix=".tmp", dir=p.parent)   # never a shared tmp
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def pin_conversation(state_dir, conversation_id) -> str:
    """The onboarding thread is the first one an opener ran in; later calls keep it. Returns the
    pinned id (which may be another browser's, when two opened at once)."""
    with _LOCK:
        rec = read(state_dir)
        if not rec.get("onboarding_conversation") and conversation_id:
            rec["onboarding_conversation"] = str(conversation_id)
            _write(state_dir, rec)
        return rec.get("onboarding_conversation") or ""


def conversation(state_dir) -> str:
    return str(read(state_dir).get("onboarding_conversation") or "")


def set_team_declined(state_dir, when=None):
    with _LOCK:
        rec = read(state_dir)
        rec["team_declined_at"] = float(when if when is not None else time.time())
        _write(state_dir, rec)


def clear_team_declined(state_dir):
    with _LOCK:
        rec = read(state_dir)
        if rec.pop("team_declined_at", None) is not None:
            _write(state_dir, rec)


def team_declined(state_dir) -> bool:
    return bool(read(state_dir).get("team_declined_at"))


def paired(device_records, devices=("iPhone", "iPad", "Mac")) -> dict:
    """{device: 'connected' | 'code not used yet'} for the codes Arturo made (label
    "<device> (arturo)", lowercase), ignoring revoked ones. Connected wins over an unused code.
    Names and states only: never a code, token or device id."""
    by_label = {f"{d.lower()} (arturo)": d for d in devices}
    out = {}
    for rec in device_records or []:
        if rec.get("minted_by") != PAIR_MINTER or rec.get("revoked_at"):
            continue
        device = by_label.get(str(rec.get("label") or "").strip().lower())
        if device is None:
            continue
        if rec.get("last_seen_at"):
            out[device] = "connected"
        else:
            out.setdefault(device, "code not used yet")
    return {d: out[d] for d in devices if d in out}       # a fixed order, not the store's
