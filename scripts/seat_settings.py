#!/usr/bin/env python3
"""seat_settings.py — a seat's rotation settings: ``auto_compact`` (the main switch: our compaction runs on its own
only while it is on; default OFF), the share of the context window at which it does (``compact_at``, kept when the
switch is off, applied only while it is on), and whether its chat shows the compaction notice. A person typing
/compact starts one whatever the switch says. Per AGENT (every person sees the same; a per-person override comes later
with the fleet-wide menu), changed from the agent's setup
slideout (API: GET/PUT /api/agents/:id/settings, api/src/lib/seatSettings.ts is this file's twin).

File ``$ORCHESTRA_DIR/state/seat-settings.json`` (0600, written atomically):
  {"seats": {"<id>": {"auto_compact": false, "show_compaction": true, "compact_at": 65}},
   "history": [{"seat", "field", "old", "new", "by", "at"}]}
Optional fleet default ``state/seat-settings-default.json``: {"default": {"auto_compact": ..., "show_compaction": ...,
"compact_at": ...}}.

ONE reader, the same fallback order on both sides: the seat's value, else the fleet default, else built in (65 %,
shown, auto-compact off). ``compact_at`` is valid 40–85; a stored value outside that (or of the wrong type) is ignored, not clamped.
``guard_pct`` (90) is the hard safety guard and is never read from a file. The supervisor reads this at every turn
end, so a change applies on the next turn.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

BUILTIN = {"auto_compact": False, "show_compaction": True, "compact_at": 65}
GUARD_PCT = 90
COMPACT_MIN, COMPACT_MAX = 40, 85
FIELDS = ("auto_compact", "show_compaction", "compact_at")


def data_dir() -> Path:
    env = (os.environ.get("ORCHESTRA_DIR") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".orchestra"


def settings_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "state" / "seat-settings.json"


def default_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "state" / "seat-settings-default.json"


def valid(field: str, value) -> bool:
    """Is this a value the field may hold? (bool is not an int here: True is not 1 %.)"""
    if field in ("auto_compact", "show_compaction"):
        return isinstance(value, bool)
    if field == "compact_at":
        return isinstance(value, int) and not isinstance(value, bool) and COMPACT_MIN <= value <= COMPACT_MAX
    return False


def _read(p: Path) -> dict:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def rotation_settings(seat: str, root: Path | None = None) -> dict:
    """``{"auto_compact": bool, "show_compaction": bool, "compact_at": int, "guard_pct": 90, "source":
    {"auto_compact": "seat|default|builtin", "show_compaction": ..., "compact_at": ...}}`` for one seat."""
    seats = _read(settings_path(root)).get("seats")
    mine = seats.get(seat) if isinstance(seats, dict) and isinstance(seats.get(seat), dict) else {}
    fleet = _read(default_path(root)).get("default")
    fleet = fleet if isinstance(fleet, dict) else {}
    out: dict = {"guard_pct": GUARD_PCT, "source": {}}
    for f in FIELDS:
        if f in mine and valid(f, mine[f]):
            out[f], out["source"][f] = mine[f], "seat"
        elif f in fleet and valid(f, fleet[f]):
            out[f], out["source"][f] = fleet[f], "default"
        else:
            out[f], out["source"][f] = BUILTIN[f], "builtin"
    return out


def update_seat(seat: str, changes: dict, by: str | None, root: Path | None = None) -> dict:
    """Set some of a seat's fields (each validated: ``ValueError`` names the first bad one), append one history row
    per field that changed, write the file atomically (0600). Returns the seat's settings as ``rotation_settings``
    reads them now."""
    bad = [f for f in changes if f not in FIELDS or not valid(f, changes[f])]
    if bad:
        raise ValueError(f"{bad[0]}: " + ("true or false" if bad[0] in ("auto_compact", "show_compaction")
                                          else f"a whole number from {COMPACT_MIN} to {COMPACT_MAX}" if bad[0] == "compact_at"
                                          else "not a setting"))
    p = settings_path(root)
    try:
        from orchestra_cli.settings import guard_test_write
        guard_test_write(p, "seat settings")
    except ImportError:
        pass
    doc = _read(p)
    seats = doc.get("seats") if isinstance(doc.get("seats"), dict) else {}
    history = doc.get("history") if isinstance(doc.get("history"), list) else []
    cur = dict(seats.get(seat) or {})
    before = rotation_settings(seat, root)
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for f, v in changes.items():
        if cur.get(f) == v and f in cur:
            continue
        history.append({"seat": seat, "field": f, "old": before[f], "new": v, "by": by, "at": at})
        cur[f] = v
    seats[seat] = cur
    doc = {**doc, "seats": seats, "history": history}
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".seat-settings-", dir=str(p.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return rotation_settings(seat, root)


def history(seat: str | None = None, root: Path | None = None) -> list[dict]:
    rows = _read(settings_path(root)).get("history")
    rows = rows if isinstance(rows, list) else []
    return [r for r in rows if isinstance(r, dict) and (seat is None or r.get("seat") == seat)]


def migrate_rotation_switch(root: Path | None = None) -> list[str]:
    """One time: a seat with ``"rotation": true`` in the retired R5 v1 switch (``state/headless-seats.json``) gets
    ``auto_compact: true`` here, recorded in the history ``by "migration"``. Idempotent: a seat whose auto_compact is
    already set (on or off) is left alone. Returns the seats changed. The old file is not modified."""
    seats = _read((root or data_dir()) / "state" / "headless-seats.json").get("seats")
    if not isinstance(seats, dict):
        return []
    current = _read(settings_path(root)).get("seats")
    current = current if isinstance(current, dict) else {}
    changed = []
    for seat, row in sorted(seats.items()):
        if isinstance(row, dict) and row.get("rotation") is True and "auto_compact" not in (current.get(seat) or {}):
            update_seat(seat, {"auto_compact": True}, by="migration", root=root)
            changed.append(seat)
    return changed
