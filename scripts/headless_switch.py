"""The per-seat headless switch and the client side of a seat's supervisor (scripts/seat_supervisor.py).

A Claude seat that is switched on runs one warm headless `claude -p` process under its supervisor
instead of the TUI in its tmux pane. Everything that types a message into a pane (the gateway's
verified_inject, the message router's inject) asks `headless_seat()` first and, for a switched-on
seat, hands the words to the supervisor over its unix socket instead of send-keys.

The switch lives in its own small file, `<data dir>/state/headless-seats.json`, not on the registry
row: the flat registry.json is a projection of the identity store that is rewritten (and has
flapped) underneath its readers, so a field added there could vanish. The file is read on every
call (an mtime-keyed cache keeps that cheap), so the router (a fresh process each pass) and the
long-running gateway both see a change at once, with no restart.

    {"seats": {"<seat>": {"headless": true, "runtime": "claude", "since": "...", "by": "..."}}}

DEFAULT OFF: no file, an unreadable file, a seat that is not listed, or a listed seat whose
runtime is not claude (recorded at switch time, and the registry row when it says otherwise) all
read as off. Codex, Gemini and Grok seats stay on tmux whatever the file says.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

#: The only runtime a seat may run headless on; every other runtime stays on tmux.
HEADLESS_RUNTIMES = frozenset({"claude"})

SWITCH_FILE = "headless-seats.json"
#: Socket name inside the seat's supervisor dir. Short on purpose: a unix socket path is
#: limited to ~107 bytes, and the data dir can be deep.
SOCK_NAME = "sock"


def data_dir(orch_dir=None) -> Path:
    if orch_dir:
        return Path(orch_dir)
    env = (os.environ.get("ORCHESTRA_DIR", "").strip() or os.environ.get("ORCH_DIR", "").strip())
    if env:
        return Path(os.path.expanduser(env))
    from orchestra_cli.settings import data_dir as _dd
    return _dd()


def switch_path(orch_dir=None) -> Path:
    return data_dir(orch_dir) / "state" / SWITCH_FILE


def seat_dir(seat: str, orch_dir=None) -> Path:
    return data_dir(orch_dir) / "state" / "headless" / seat


def socket_path(seat: str, orch_dir=None) -> Path:
    return seat_dir(seat, orch_dir) / SOCK_NAME


_cache_lock = threading.Lock()
_cache: dict = {}


def _read_json(path: Path, cache_key: str):
    """Parsed JSON, cached on (mtime_ns, size). None when missing or unreadable."""
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] == sig:
            return hit[1]
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        doc = None
    with _cache_lock:
        _cache[cache_key] = (sig, doc)
    return doc


def load_switch(orch_dir=None) -> dict:
    """{seat: entry} for every listed seat (on or off). {} on any read problem: default off."""
    p = switch_path(orch_dir)
    doc = _read_json(p, f"switch:{p}")
    seats = doc.get("seats") if isinstance(doc, dict) else None
    return seats if isinstance(seats, dict) else {}


def _registry_rows(orch_dir=None) -> dict:
    p = data_dir(orch_dir) / "registry.json"
    doc = _read_json(p, f"registry:{p}")
    if not isinstance(doc, dict):
        return {}
    rows = doc.get("agents", doc)
    return rows if isinstance(rows, dict) else {}


def registry_runtime(seat: str, orch_dir=None):
    row = _registry_rows(orch_dir).get(seat)
    rt = row.get("runtime") if isinstance(row, dict) else None
    return (rt or "").strip().lower() or None


def is_headless(seat: str, runtime: str | None = None, orch_dir=None) -> bool:
    """True only for a seat switched on whose runtime is claude. `runtime`, when the caller
    already resolved one (the router's routing_runtime), must be claude too."""
    if not seat:
        return False
    entry = load_switch(orch_dir).get(seat)
    if not isinstance(entry, dict) or entry.get("headless") is not True:
        return False
    if (entry.get("runtime") or "").strip().lower() not in HEADLESS_RUNTIMES:
        return False
    reg = registry_runtime(seat, orch_dir)
    if reg is not None and reg not in HEADLESS_RUNTIMES:
        return False
    if runtime is not None and (runtime or "").strip().lower() not in HEADLESS_RUNTIMES:
        return False
    return True


def in_dev(seat: str, orch_dir=None) -> bool:
    """The seat's supervisor has handed the pane to the TUI (Dev): its `dev` marker exists."""
    try:
        return (seat_dir(seat, orch_dir) / "dev").exists()
    except OSError:
        return False


def headless_seat(session: str, runtime: str | None = None, orch_dir=None, include_dev: bool = False):
    """The seat id when the tmux session `session` belongs to a switched-on seat, else None.
    A session is its seat's name, or the registry row's tmux_session. A seat in Dev (its TUI runs
    in the pane, scripts/seat_supervisor.py) reads as None: every pane path applies to it as to
    any tmux seat until the TUI exits."""
    session = str(session or "").strip()
    if not session:
        return None
    seats = load_switch(orch_dir)
    if not seats:
        return None             # the common case: nothing switched on, no registry read
    if session in seats:
        hit = session if is_headless(session, runtime, orch_dir) else None
    else:
        hit = next((seat for seat, row in _registry_rows(orch_dir).items()
                    if seat in seats and isinstance(row, dict) and row.get("tmux_session") == session
                    and is_headless(seat, runtime, orch_dir)), None)
    return None if hit is None or (in_dev(hit, orch_dir) and not include_dev) else hit


def set_switch(seat: str, on: bool, *, runtime: str, by: str = "", orch_dir=None) -> dict:
    """Write one seat's entry (atomic replace). Refuses to switch on a non-Claude seat."""
    rt = (runtime or "").strip().lower()
    if on and rt not in HEADLESS_RUNTIMES:
        raise ValueError(f"{seat}: runtime {rt or 'unknown'} stays on tmux; only claude seats run headless")
    p = switch_path(orch_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("seats"), dict):
            doc = {"seats": {}}
    except (OSError, ValueError):
        doc = {"seats": {}}
    old = doc["seats"].get(seat)
    entry = {k: v for k, v in (old.items() if isinstance(old, dict) else ())
             if k not in ("headless", "runtime", "since", "by")}      # per-seat options are kept
    entry.update({"headless": bool(on), "runtime": rt,
                  "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": by or ""})
    doc["seats"][seat] = entry
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)
    return entry


def set_option(seat: str, key: str, value, orch_dir=None) -> dict:
    """Set one per-seat option (for example message_uuid) on the seat's entry, keeping the rest.
    An option never switches a seat on: a seat with no entry gets one that is off."""
    if key in ("headless", "runtime", "since", "by"):
        raise ValueError(f"{key} is set by set_switch")
    p = switch_path(orch_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("seats"), dict):
            doc = {"seats": {}}
    except (OSError, ValueError):
        doc = {"seats": {}}
    entry = doc["seats"].get(seat)
    entry = dict(entry) if isinstance(entry, dict) else {"headless": False, "runtime": ""}
    entry[key] = value
    doc["seats"][seat] = entry
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)
    return entry


# --- client ---------------------------------------------------------------------------------------

class SupervisorDown(Exception):
    """The seat's supervisor socket is missing or does not answer."""


def request(seat: str, payload: dict, orch_dir=None, timeout: float = 5.0) -> dict:
    """One request/response over the seat's supervisor socket (one JSON line each way)."""
    path = socket_path(seat, orch_dir)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except (OSError, socket.timeout) as e:
        raise SupervisorDown(f"{seat}: supervisor not answering at {path} ({e})") from e
    finally:
        s.close()
    try:
        out = json.loads(buf.decode() or "null")
    except ValueError as e:
        raise SupervisorDown(f"{seat}: supervisor sent a bad reply") from e
    if not isinstance(out, dict):
        raise SupervisorDown(f"{seat}: supervisor sent no reply")
    return out


def send(seat: str, text: str, msg_id: str, *, allow_waiting: bool = False, orch_dir=None) -> dict:
    return request(seat, {"cmd": "send", "id": msg_id, "text": text, "allow_waiting": allow_waiting},
                   orch_dir=orch_dir)


def status(seat: str, orch_dir=None) -> dict:
    return request(seat, {"cmd": "status"}, orch_dir=orch_dir)


def message_state(seat: str, msg_id: str, orch_dir=None) -> dict:
    return request(seat, {"cmd": "message", "id": msg_id}, orch_dir=orch_dir)


#: Message states that prove the CLI took the words (its replay echo was seen).
DELIVERED_STATES = frozenset({"delivered", "answered", "interrupted"})


def wait_delivered(seat: str, msg_id: str, timeout: float = 20.0, poll: float = 1.0,
                   orch_dir=None, sleep=time.sleep) -> str | None:
    """Poll one message until the replay echo shows it delivered (or past it). Returns the last
    state seen, None when the supervisor stopped answering."""
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            last = message_state(seat, msg_id, orch_dir=orch_dir).get("state")
        except SupervisorDown:
            return None
        if last in DELIVERED_STATES or time.monotonic() >= deadline:
            return last
        sleep(poll)
