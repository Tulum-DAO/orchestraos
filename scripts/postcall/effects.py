"""External writes go through one interface (design §7): live, shadow or test.

An adapter is the outside world as the ledger sees it: send(effect) performs the write and returns
an external id; find(effect) looks the write up by its deterministic effect id (any status), so
recovery can tell "landed but unrecorded" from "never landed"."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Effect:
    effect_id: str
    kind: str
    payload: dict = field(default_factory=dict, compare=False)


class Recorder:
    """Test adapter: an in-memory outside world. Touches nothing outside the process."""

    def __init__(self):
        self.sent = []

    def send(self, effect):
        self.sent.append(effect)
        return f"ext-{len(self.sent)}"

    def find(self, effect):
        for n, e in enumerate(self.sent, 1):
            if e.effect_id == effect.effect_id:
                return f"ext-{n}"
        return None


# --- mode + adapters ---------------------------------------------------------------------------
import json as _json
import os as _os
import sqlite3 as _sqlite3
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile

AUTHOR = "postcall-dev"  # gm ruling msg_92221c61 §9.1; a service identity at rollout
_MODES = ("shadow", "live")


def state_dir():
    return _os.environ.get("POSTCALL_STATE_DIR") or _os.path.expanduser("~/.orchestra/state/postcall")


def mode():
    """'live' only when gm has written exactly `live` to MODE; anything else (missing, unreadable,
    garbage) is shadow. POSTCALL_MODE=test is honoured for the test suite only."""
    if _os.environ.get("POSTCALL_MODE") == "test":
        return "test"
    try:
        with open(_os.path.join(state_dir(), "MODE")) as f:
            value = f.read().strip()
    except OSError:
        return "shadow"
    return value if value in _MODES else "shadow"


def ledger_path(which):
    d = _os.path.join(state_dir(), which)
    _os.makedirs(d, exist_ok=True)
    return _os.path.join(d, "ledger.db")


class Shadow:
    """Writes what WOULD be sent to shadow.jsonl. Never calls approval.py, msg_store or the network."""

    def __init__(self, path):
        self.path = path
        _os.makedirs(_os.path.dirname(path), exist_ok=True)

    def _rows(self):
        try:
            with open(self.path) as f:
                return [_json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def send(self, effect):
        ext = f"shadow-{len(self._rows()) + 1}"
        with open(self.path, "a") as f:
            f.write(_json.dumps({"effect_id": effect.effect_id, "kind": effect.kind, "external_id": ext,
                                 "payload": effect.payload}, sort_keys=True) + "\n")
        return ext

    def find(self, effect):
        for row in self._rows():
            if row["effect_id"] == effect.effect_id:
                return row["external_id"]
        return None


class Live:
    """Files through the approval CLI (never by writing its DB). find() is a read-only lookup by op_key,
    any status, so recovery sees writes that landed but were never recorded (design §1)."""

    def __init__(self):
        self.cli = _os.environ.get("POSTCALL_APPROVAL_CLI") or _os.path.expanduser(
            "~/scripts/agent-orchestra/scripts/approval.py")

    def _db(self):
        return _os.environ.get("APPROVAL_DB_PATH") or _os.path.expanduser("~/.orchestra/state/tasks.db")

    def send(self, effect):
        if effect.kind != "card":
            raise NotImplementedError(f"live adapter: {effect.kind} not wired yet")
        p = effect.payload
        with _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            _json.dump(p["questions"], f)
            qfile = f.name
        try:
            r = _subprocess.run([_sys.executable, self.cli, "questionnaire", "--from", AUTHOR,
                                 "--title", p["title"], "--summary", p["summary"], "--feature", "post_call",
                                 "--op-key", effect.effect_id, "--questions-json", "@" + qfile],
                                capture_output=True, text=True, timeout=120)
        finally:
            _os.unlink(qfile)
        out = (r.stdout or "").strip().splitlines()
        if r.returncode != 0 or not out or not out[-1].startswith("qnr_"):
            raise RuntimeError(f"approval.py questionnaire failed rc={r.returncode}: {(r.stderr or '')[-300:]}")
        return out[-1]

    def find(self, effect):
        table = {"card": "questionnaires", "task": "approval_requests"}.get(effect.kind)
        if table is None or not _os.path.exists(self._db()):
            return None
        with _sqlite3.connect(f"file:{self._db()}?mode=ro", uri=True) as c:
            try:
                row = c.execute(f"SELECT id FROM {table} WHERE from_agent=? AND op_key=? ORDER BY created_at LIMIT 1",
                                (AUTHOR, effect.effect_id)).fetchone()
            except _sqlite3.OperationalError:
                return None
        return row[0] if row else None


def adapter_for(which):
    if which == "live":
        return Live()
    if which == "shadow":
        return Shadow(_os.path.join(state_dir(), "shadow", "shadow.jsonl"))
    if which == "test":
        return Recorder()
    raise ValueError(which)
