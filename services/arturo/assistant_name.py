"""The assistant's display name on this install: what it is called on every screen, in what it says, and in
its own instructions. "Arturo" until someone renames it.

Only the NAME changes. The internal id stays "arturo" everywhere (routes, this service's folder, gateway
scopes, state files, the agent id), so the phone and Watch apps and other boxes keep working whatever the
assistant is called.

The name is a seat name like any other agent's, under the id "arturo" (scripts/identity_store/seat_names.py), so
one record names every agent and the assistant's old names stay its aliases. The JSON file it used to live in is
still written alongside and still read when there is no seat name (an install with no identity store, or one not
migrated yet: `python3 -m services.arturo.assistant_name migrate`).

Read on every call (never cached at boot), so a rename shows on the next request with no restart; atomic
tmp-replace writes under a lock; an append-only audit log naming the writing device. name() never raises: a
missing or bad record is the default.

The name goes into the model's instructions, so only plain name characters are allowed (letters, digits,
spaces, and ' - . inside a word): nothing that could close a quote or start a new instruction line.
"""
import json
import os
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

if os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from orchestra_cli.settings import data_dir as _data_dir  # noqa: E402

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", _data_dir()))
DEFAULT_PATH = ORCHESTRA_DIR / "state/assistant-name.json"
DEFAULT_LOG = ORCHESTRA_DIR / "state/assistant-name.log"

DEFAULT_NAME = "Arturo"
MAX_LEN = 32
_INNER = set("'-.’")


def validate(raw):
    """The name, tidied (trimmed, inner spaces collapsed, NFC); raises ValueError with a plain reason."""
    if not isinstance(raw, str):
        raise ValueError("name must be text")
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise ValueError("name can't be empty")
    if len(name) > MAX_LEN:
        raise ValueError(f"name must be at most {MAX_LEN} characters")
    for i, ch in enumerate(name):
        if ch.isalnum() or ch == " ":
            continue
        # an apostrophe, hyphen or dot only between letters/digits (O'Neil, Jean-Luc, J.R.), never at an end
        if ch in _INNER and 0 < i < len(name) - 1 and name[i - 1] != " ":
            continue
        raise ValueError("use letters, numbers and spaces (an apostrophe, hyphen or dot inside a word is fine)")
    if not any(ch.isalpha() for ch in name):
        raise ValueError("name needs at least one letter")
    return name


class AssistantNameStore:
    """{"name", "changed_at", "changed_by", "source"}. No file (or a reset) means the default name."""

    def __init__(self, path=DEFAULT_PATH, log_path=DEFAULT_LOG, orchestra_dir=None):
        self.path = Path(path)
        self.log_path = Path(log_path)
        # The data dir whose identity store holds the seat name: the one this file sits in (<dir>/state/...).
        self.orchestra_dir = Path(orchestra_dir) if orchestra_dir else self.path.parent.parent
        self._lock = threading.Lock()

    def _seat_name(self):
        try:
            from scripts.identity_store import seat_names as SN
            n = SN.current_name_ro(self.orchestra_dir, SN.ASSISTANT_ID)
            return validate(n) if n else None
        except Exception:
            return None                                # no identity store, or unreadable: the file decides

    def _seat_write(self, name, by):
        """Make `name` the assistant's seat name (None: no name). Refused names raise ValueError. No-op on an
        install with no identity store: the file alone names it there."""
        from scripts.identity_store import seat_names as SN
        if not os.path.exists(SN.db_path_for(self.orchestra_dir)):
            return
        conn = SN.open_db(self.orchestra_dir)
        try:
            if name is None:
                SN.clear_name(conn, SN.ASSISTANT_ID)
            else:
                SN.set_name(conn, SN.ASSISTANT_ID, name, by=by, orchestra_dir=self.orchestra_dir, assistant=True)
        except SN.NameError_ as e:
            raise ValueError(str(e)) from None
        finally:
            conn.close()

    def _read(self):
        try:
            d = json.loads(self.path.read_text())
            return {**d, "name": validate(d.get("name"))} if isinstance(d, dict) else None
        except Exception:
            return None                                # missing, unreadable or hand-edited badly: the default

    def _write_log(self, entry):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def name(self):
        seat = self._seat_name()
        if seat:
            return seat
        rec = self._read()
        return rec["name"] if rec else DEFAULT_NAME

    def state(self):
        rec = self._read()
        seat = self._seat_name()
        if seat or rec:
            return {"name": seat or rec["name"], "default": DEFAULT_NAME, "custom": True,
                    **{k: (rec or {}).get(k) for k in ("changed_at", "changed_by", "source")}}
        return {"name": DEFAULT_NAME, "default": DEFAULT_NAME, "custom": False,
                "changed_at": None, "changed_by": None, "source": None}

    def set(self, raw, by="settings", source="settings", device=None):
        name = validate(raw)
        stamp = {"changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "changed_by": str(by)[:40], "source": str(source)[:40]}
        with self._lock:
            before = self.name()
            self._seat_write(name, stamp["changed_by"])    # first: a name another agent holds is refused here
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"name": name, **stamp}))
            tmp.replace(self.path)
            self._write_log({"action": "set", "from": before, "name": name, **stamp, "device": device})
        return self.state()

    def migrate(self):
        """Copy a name saved only in the file into the seat names (once; a no-op when the seat already has one or
        there is no identity store). Returns the name migrated, or None."""
        rec = self._read()
        if not rec or self._seat_name():
            return None
        from scripts.identity_store import seat_names as SN
        if not os.path.exists(SN.db_path_for(self.orchestra_dir)):
            return None
        with self._lock:
            self._seat_write(rec["name"], str(rec.get("changed_by") or "migrate")[:40])
        return rec["name"]

    def reset(self, by="settings", source="settings", device=None):
        """Back to the default name."""
        with self._lock:
            before = self.name()
            self._seat_write(None, str(by)[:40])
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._write_log({"action": "reset", "from": before, "name": DEFAULT_NAME,
                             "changed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "changed_by": str(by)[:40], "source": str(source)[:40], "device": device})
        return self.state()


_default = AssistantNameStore()


def name():
    """The assistant's name right now (the default when none is saved). Never raises."""
    return _default.name()


# Left as written: the "[Arturo Voice]:" tag other seats and boxes match on messages typed into their panes,
# and "the Arturo tab", the phone app's own tab label, which the app decides.
_KEEP = r"(?! Voice\]| tab\b)"


def named(text):
    """Text written with the default name, in the current one ("Arturo" and "ARTURO" as whole words), for
    instructions and lines kept as constants."""
    n = name()
    if n == DEFAULT_NAME or not text:
        return text
    text = re.sub(r"\bArturo\b" + _KEEP, lambda _m: n, text)
    return re.sub(r"\bARTURO\b", lambda _m: n.upper(), text)


def state():
    return _default.state()


def set_name(raw, **kw):
    return _default.set(raw, **kw)


def reset_name(**kw):
    return _default.reset(**kw)


def migrate():
    return _default.migrate()


if __name__ == "__main__":
    if sys.argv[1:] == ["migrate"]:
        print(json.dumps({"ok": True, "migrated": migrate(), "name": name()}))
    else:
        print("usage: python3 -m services.arturo.assistant_name migrate")
        sys.exit(2)
