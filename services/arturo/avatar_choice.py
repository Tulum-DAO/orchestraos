"""Each agent's saved avatar look (shape, colour, eye colour), keyed by agent id.

Same pattern as voice_choice.py: one JSON file, read on every request (never cached at boot), so a pick
from any surface shows everywhere at once with no restart; atomic tmp-replace writes under a lock; an
append-only audit log that names the writing device. An agent with no record has the default look: its
shape from the FNV-1a hash of its id (the same hash the dashboard's avatarCore.ts uses), theme-aware ink,
and auto eyes. Keyed by agent id, not global, so every agent (and later every person's assistant) has its own.

The allowed values mirror the dashboard's avatarCore.ts (SHAPES order, the Bold palette); the test reads
that file so the two cannot drift apart.
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# The ONE data-dir default is orchestra_cli.settings.data_dir; orchestra_cli lives in this file's checkout,
# appended (never prepended) so nothing already on the path is shadowed.
if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", _data_dir()))
DEFAULT_PATH = ORCHESTRA_DIR / "state/avatar-choice.json"
DEFAULT_LOG = ORCHESTRA_DIR / "state/avatar-choice.log"

# In avatarCore.ts SHAPES order: the hash indexes into it, so the order is part of every default look.
SHAPES = ("circle", "square", "pill", "hexagon", "heart", "droplet", "star", "pebble",
          "gumdrop", "bean", "cloud", "bunny", "kitty", "egg", "flower", "sprout")
BOLD = ("#E5243B", "#FF7A1A", "#FFC61A", "#1FA855", "#00A6A0", "#1E6BE6",
        "#4B3FD8", "#8B2FE0", "#D61F8C", "#FF4F8B", "#2C2E3B")
COLORS = ("ink",) + BOLD               # the swatches: theme-aware ink first, then Bold
EYE_COLORS = ("auto", "black", "white")
DEFAULT_COLOR, DEFAULT_EYES = "ink", "auto"
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FIELDS = ("shape", "color", "eyeColor")


def fnv1a(s):
    """32-bit FNV-1a over UTF-16 code units, bit for bit what avatarCore.ts computes (charCodeAt)."""
    h = 0x811C9DC5
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def default_look(agent_id):
    return {"shape": SHAPES[fnv1a(str(agent_id)) % len(SHAPES)], "color": DEFAULT_COLOR, "eyeColor": DEFAULT_EYES}


def valid_agent_id(agent_id):
    return isinstance(agent_id, str) and bool(AGENT_ID_RE.match(agent_id))


def _norm_color(c):
    c = str(c or "").strip()
    return "ink" if c.lower() == "ink" else c.upper()


def validate(shape=None, color=None, eye_color=None):
    """The look fields given, checked and normalised; raises ValueError with a plain reason."""
    out = {}
    if shape is not None:
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of: {', '.join(SHAPES)}")
        out["shape"] = shape
    if color is not None:
        c = _norm_color(color)
        if c not in COLORS:
            raise ValueError("color must be ink or one of the Bold palette colours")
        out["color"] = c
    if eye_color is not None:
        if eye_color not in EYE_COLORS:
            raise ValueError(f"eyeColor must be one of: {', '.join(EYE_COLORS)}")
        out["eyeColor"] = eye_color
    return out


class AvatarChoiceStore:
    """{"<agentId>": {shape, color, eyeColor, changed_at, changed_by, source}}. Only saved looks are stored;
    a reset removes the agent's record, so it falls back to the default look."""

    def __init__(self, path=DEFAULT_PATH, log_path=DEFAULT_LOG):
        self.path = Path(path)
        self.log_path = Path(log_path)
        self._lock = threading.Lock()

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            return {}
        if not isinstance(d, dict):
            return {}
        out = {}
        for agent, rec in d.items():
            if not (valid_agent_id(agent) and isinstance(rec, dict)):
                continue
            try:
                look = validate(rec.get("shape"), rec.get("color"), rec.get("eyeColor"))
            except ValueError:
                continue                               # a hand-edited bad record: the default look instead
            out[agent] = {**default_look(agent), **look,
                          **{k: rec.get(k) for k in ("changed_at", "changed_by", "source")}}
        return out

    def _write(self, d, log_entry):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(self.path)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def all(self):
        """Every saved look (agents with no record are not listed: they have the default)."""
        return self._read()

    def state(self, agent_id):
        """The agent's look as every surface should draw it, and whether it is a saved choice."""
        rec = self._read().get(agent_id)
        if rec:
            return {"agent": agent_id, "chosen": True, **rec}
        return {"agent": agent_id, "chosen": False, **default_look(agent_id),
                "changed_at": None, "changed_by": None, "source": None}

    def set(self, agent_id, shape=None, color=None, eye_color=None, by="settings", source="settings", device=None):
        """Save a look. Fields left out keep the agent's current look (saved, else default)."""
        if not valid_agent_id(agent_id):
            raise ValueError("agent must be 1-80 letters, digits, '.', '_', ':' or '-'")
        look = validate(shape, color, eye_color)
        if not look:
            raise ValueError("nothing to change: give shape, color or eyeColor")
        stamp = {"changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "changed_by": str(by)[:40], "source": str(source)[:40]}
        with self._lock:
            d = self._read()
            base = {k: (d.get(agent_id) or default_look(agent_id))[k] for k in _FIELDS}
            rec = {**base, **look, **stamp}
            d[agent_id] = rec
            # audit: who (by, the client's label), how (source), and which device (the gateway's resolved
            # caller, never the body)
            self._write(d, {"action": "set", "agent": agent_id, **rec, "device": device})
        return {"agent": agent_id, "chosen": True, **rec}

    def reset(self, agent_id, by="settings", source="settings", device=None):
        """Forget the saved look: the agent is back on its default."""
        if not valid_agent_id(agent_id):
            raise ValueError("agent must be 1-80 letters, digits, '.', '_', ':' or '-'")
        with self._lock:
            d = self._read()
            had = d.pop(agent_id, None) is not None
            self._write(d, {"action": "reset", "agent": agent_id, "had_record": had,
                            "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "changed_by": str(by)[:40], "source": str(source)[:40], "device": device})
        return self.state(agent_id)


_default = AvatarChoiceStore()


def all_choices():
    return _default.all()


def state(agent_id):
    return _default.state(agent_id)


def set_avatar(agent_id, **kw):
    return _default.set(agent_id, **kw)


def reset_avatar(agent_id, **kw):
    return _default.reset(agent_id, **kw)
