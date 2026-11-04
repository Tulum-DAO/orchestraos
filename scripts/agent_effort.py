"""An agent's effort: how hard its model thinks. The levels each runtime has, where an agent's chosen
level is kept, how a spawn reads it, and a running Claude seat's live switch.

Levels (dashboard/src/lib/modelEffort.ts draws the same table):
- Claude Code: low, medium, high, xhigh, max (`claude --help` 2.1.295: `--effort <level>`).
- Codex: low, medium, high, xhigh (its "Select Reasoning Level" picker; `model_reasoning_effort`).
- Gemini: the level is part of the model id, so there is nothing separate. Grok: none driven.

Where it is kept: state/agent-efforts.json {"efforts": {<lineage root>: <level>}}. By lineage root, so
a rotation's successor (<root>-g<N>) starts on the level its seat was given, the way it inherits the
seat's model; the identity store's schema is left alone. A seat with no entry starts on the CLI's own
default (nothing is passed).

How it applies:
- At spawn: spawn-agent.sh asks `agent_effort.py resolve <agent> --runtime <rt>` and passes Claude
  `--effort <level>`, Codex `-c model_reasoning_effort=<level>` (a per-run override). An AGENT_EFFORT
  in the spawn's environment (a new agent's default, from Settings > Agents) wins and is kept.
- Live, Claude: `/effort <level>` typed into the IDLE pane (switch_effort_live below).
- Live, Codex: its model picker's effort step (agent_model_codex.switch_live(effort=...)).
- Headless Claude seat (scripts/seat_supervisor.py): its supervisor passes `--effort <level>` (this store,
  read at each start of its CLI) and takes {"cmd":"set_effort"}: the CLI's `apply_flag_settings`
  control request ({"effortLevel"}, session-only, so the host settings file is never written), read
  back with `get_settings`; a restart on --effort when the CLI refuses it. Codex has no headless seat.

What the installed Claude CLI does with `/effort <level>` (2.1.295, read from its own code, not run
against a live pane): it sets the session's level and prints "Set effort level to <level> (saved as
your default for new sessions): ..." for low..xhigh, "(this session only)" for max; "Effort '<x>'
exceeds the cap ...; set to '<y>' instead" when capped; "Not applied: CLAUDE_CODE_EFFORT_LEVEL=..."
when the environment overrides it. A seat with history is asked first ("Change effort level?",
"1. Yes, switch to <level>" / "2. No, go back"), the dialog `/model` shows. The save writes
`modelSettings.<model>.effortLevel` into the user settings file every new Claude session on the
host reads, so, like `/model`'s "model" key, the switch runs under the one host model-switch lock,
snapshots `modelSettings` (and the older top-level `effortLevel`) before, and puts them back after.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

try:
    from scripts import agent_model as AM
except ImportError:  # run as a script: scripts/ is on sys.path, the checkout is not
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import agent_model as AM

LEVELS = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high", "xhigh"),
}
STORE_NAME = "agent-efforts.json"
JOURNAL_NAME = ".orchestra-effort-switch.pending.json"
#: The user-settings keys a Claude `/effort` save can write.
SETTINGS_KEYS = ("modelSettings", "effortLevel")
_ALL = frozenset(lv for lvs in LEVELS.values() for lv in lvs)


def valid(runtime, level) -> bool:
    return isinstance(level, str) and level in LEVELS.get((runtime or "").lower(), ())


def levels(runtime) -> list:
    return list(LEVELS.get((runtime or "").lower(), ()))


# --- the per-agent store ------------------------------------------------------------------------

def store_path(orch_dir=None) -> Path:
    env = os.environ.get("AGENT_EFFORT_FILE")
    if env:
        return Path(env)
    base = orch_dir or os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR")
    if not base:
        try:
            from orchestra_cli import settings as _s
            base = str(_s.data_dir())
        except Exception:  # noqa: BLE001
            base = os.path.expanduser("~/.orchestra")
    return Path(base) / "state" / STORE_NAME


def root_of(agent_id: str, registry: dict | None = None) -> str:
    """The lineage root an agent id belongs to: the registry's lineage_root, else the id without a
    -g<N> generation suffix."""
    rec = ((registry or {}).get("agents") or {}).get(agent_id) or {}
    root = rec.get("lineage_root") if isinstance(rec, dict) else None
    if isinstance(root, str) and root:
        return root
    return re.sub(r"-g\d+$", "", agent_id)


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        eff = data.get("efforts") if isinstance(data, dict) else None
        return {k: v for k, v in eff.items() if isinstance(k, str) and v in _ALL} if isinstance(eff, dict) else {}
    except (OSError, ValueError):
        return {}


def get(path, root: str) -> str | None:
    """The level kept for this lineage root; None when none (or the file is unreadable)."""
    return _read(Path(path)).get(root)


def put(path, root: str, level: str | None) -> None:
    """Keep `level` for this lineage root (None removes it). Under a lock, temp file + rename."""
    if level is not None and level not in _ALL:
        raise ValueError(f"not an effort level: {level!r}")
    path = Path(path)
    if level is None and not path.exists():
        return                         # nothing kept: nothing to forget, and no file made for it
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            data = _read(path)
            if level is None:
                data.pop(root, None)
            else:
                data[root] = level
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"efforts": data}, indent=2) + "\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


# --- the host settings keys a Claude /effort save writes -----------------------------------------

_ABSENT = object()


def read_keys(path: Path) -> dict:
    """{key: value} for SETTINGS_KEYS present in the settings file. Raises on a file that exists but
    is not a JSON object: never guess what to put back."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return {k: data[k] for k in SETTINGS_KEYS if k in data}


def write_keys(path: Path, before: dict) -> bool:
    """Put SETTINGS_KEYS back exactly as `before` had them (absent -> removed), keeping every other
    key the file holds now. Temp file + rename, mode kept; True when a read-back matches."""
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        if not before:
            return True
        text = "{}"
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    if {k: data[k] for k in SETTINGS_KEYS if k in data} != before:
        for k in SETTINGS_KEYS:
            if k in before:
                data[k] = before[k]
            else:
                data.pop(k, None)
        mode = None
        with contextlib.suppress(FileNotFoundError):
            mode = path.stat().st_mode & 0o7777
        fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            if mode is not None:
                os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
    return read_keys(path) == before


def _journal(settings_path: Path) -> Path:
    return Path(settings_path).parent / JOURNAL_NAME


def _write_journal(settings_path: Path, before: dict) -> None:
    j = _journal(settings_path)
    fd, tmp = tempfile.mkstemp(prefix=".journal.", suffix=".tmp", dir=str(j.parent))
    with os.fdopen(fd, "w") as f:
        json.dump({"keys": before}, f)
    os.replace(tmp, j)


def recover_pending(settings_path: Path) -> bool:
    """An effort switch that died between the CLI's save and our restore left its snapshot here:
    put it back (under the lock, before every switch). True when nothing is pending afterwards."""
    j = _journal(settings_path)
    try:
        rec = json.loads(j.read_text())
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001 — an unreadable journal is never acted on, never deleted
        return False
    keys = rec.get("keys") if isinstance(rec, dict) else None
    if isinstance(keys, dict) and write_keys(settings_path, {k: v for k, v in keys.items() if k in SETTINGS_KEYS}):
        j.unlink()
        return True
    return False


# --- confirmation --------------------------------------------------------------------------------

_SET = re.compile(r"^\s*Set effort level to\s+`?([a-z]+)`?")
_CAPPED = re.compile(r"^\s*Effort '[^']*' exceeds the cap\b.*?set to '([a-z]+)' instead", re.S)
_STDOUT = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.S)


def scan_effort_result(jsonl_path, start_offset: int):
    """What the CLI said to `/effort` in transcript lines written after `start_offset`: ("set",
    level) for its "Set effort level to" (or capped "set to '<y>' instead") line, ("refused", text)
    for any other effort answer, None when nothing yet. A `/model` line is not an answer to this."""
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if not 0 <= start_offset <= f.tell():
                return None
            f.seek(start_offset)
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001 — torn line
            continue
        if not isinstance(d, dict) or d.get("type") != "user":
            continue
        for out in _STDOUT.findall(AM._entry_text(d)):
            m = _SET.match(out) or _CAPPED.match(out)
            if m:
                return ("set", m.group(1))
            if re.search(r"\beffort\b", out, re.I):
                return ("refused", out.strip()[:300])
    return None


def switch_effort_live(*, effort: str, settings_path: Path, send, find_jsonl, capture=None, keys=None,
                       timeout_s: float = 10.0, settle_s: float = 3.0, recheck_s: float = 5.0,
                       sleep=time.sleep, clock=time.monotonic):
    """Type `/effort <effort>` into an idle Claude pane under the host model-switch lock, answer its
    confirm dialog ("1" only for the level asked for), wait for its own line, then put the host's
    settings keys back. Returns (status, body) like agent_model.switch_live: 200 switched, 400 bad
    level, 409 busy, 502 unconfirmable / not sent / refused / another level / unconfirmed, 500 when
    the host settings could not be put back."""
    if effort not in LEVELS["claude"]:
        return 400, {"ok": False, "error": "bad effort level"}
    saved = effort != "max"        # max is session-only: the CLI never saves it, so no late save to wait for
    with AM.host_model_lock(settings_path):
        if not AM.recover_pending(settings_path) or not recover_pending(settings_path):
            return 500, {"ok": False, "error": "a previous switch left the host settings unrestored"}
        try:
            before = read_keys(settings_path)
        except Exception as e:  # noqa: BLE001
            return 500, {"ok": False, "error": "host settings unreadable", "detail": repr(e)}
        path0 = find_jsonl()
        if AM.file_size(path0) is None:
            return 502, {"ok": False, "error": "unconfirmable",
                         "detail": "this agent's transcript was not found, so an effort change could "
                                   "not be confirmed; nothing was typed"}
        _write_journal(settings_path, before)
        res, typed, result, note = None, False, None, None
        try:
            offset = AM.file_size(path0)
            if offset is None:
                raise FileNotFoundError(f"transcript vanished: {path0}")
            asking = AM.switch_dialog(capture() or "") if capture is not None else None
            if asking is not None:
                ok, info = False, {"reason": "busy", "asking": asking}
            else:
                ok, info = send(f"/effort {effort}")
            info = info or {}
            typed = bool(ok) or (info.get("reason") != "busy" and not info.get("cleared"))
            if not typed:
                result = (409, {"ok": False, "error": "busy", **{k: v for k, v in info.items() if k != "reason"}}) \
                    if info.get("reason") == "busy" else (502, {"ok": False, "error": "not sent", "detail": info})
            else:
                deadline, answered = clock() + timeout_s, False
                while True:
                    res = scan_effort_result(path0, offset)
                    if res is not None:
                        break
                    late = clock() >= deadline
                    if capture is not None and keys is not None and not answered:
                        offered = AM.switch_dialog(capture() or "")
                        if offered is not None and AM.switch_dialog(capture() or "") == offered:
                            answered = True
                            if offered.strip().lower() == effort and not late:
                                keys("1")          # then wait for its line, as for one never asked
                            else:
                                keys("2")          # another level, or too late to wait inside the lock
                                note = offered
                                break
                    if late:
                        break
                    sleep(0.25)
                if saved:
                    sleep(settle_s)        # the CLI's save may land just after its line
                if note is not None:
                    result = (502, {"ok": False, "error": "the agent offered a different effort", "detail": note})
                elif res is None and not ok:
                    result = (502, {"ok": False, "error": "not sent", "detail": info})
        except Exception as e:  # noqa: BLE001 — answered, never raised past the restore
            result = (502, {"ok": False, "error": "effort switch failed", "detail": repr(e)[:300]})
        finally:
            if typed and capture is not None and keys is not None:
                with contextlib.suppress(Exception):
                    if AM.switch_dialog(capture() or "") is not None:
                        keys("2")
                        sleep(0.5)
            restored, err = _restore(settings_path, before)
            if restored and typed and saved:
                with contextlib.suppress(Exception):
                    sleep(recheck_s)
                restored, err = _restore(settings_path, before)
            if restored:
                with contextlib.suppress(FileNotFoundError):
                    _journal(settings_path).unlink()
        if not restored:
            return 500, {"ok": False, "error": "host settings not restored",
                         "switched": bool(res and res[0] == "set"), **({"detail": err} if err else {})}
        if result is not None:
            return result
        if res is None:
            return 502, {"ok": False, "error": "unconfirmed",
                         "detail": f"no 'Set effort level' line in the transcript within {int(timeout_s)} s"}
        if res[0] == "refused":
            return 502, {"ok": False, "error": "refused", "detail": res[1]}
        if res[1] != effort:
            return 502, {"ok": False, "error": "a different effort was set", "detail": res[1]}
        return 200, {"ok": True, "effort": effort, "switching": "switched"}


def _restore(settings_path: Path, before: dict):
    try:
        return write_keys(settings_path, before), None
    except Exception as e:  # noqa: BLE001
        return False, repr(e)


# --- CLI: spawn-agent.sh / agent-recovery.sh ------------------------------------------------------

def _registry(orch_dir) -> dict:
    p = os.environ.get("REGISTRY") or os.path.join(orch_dir or os.environ.get("ORCHESTRA_DIR") or "", "registry.json")
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="print the level a spawn passes for this agent (nothing for none)")
    r.add_argument("agent")
    r.add_argument("--runtime", required=True)
    r.add_argument("--orchestra-dir", default=None)
    fg = sub.add_parser("forget", help="take out the level kept for this agent's lineage (a new agent, a retire)")
    fg.add_argument("agent")
    fg.add_argument("--orchestra-dir", default=None)
    a = ap.parse_args(argv)
    path = store_path(a.orchestra_dir)
    if a.cmd == "forget":
        put(path, root_of(a.agent, _registry(a.orchestra_dir)), None)     # under the writers' lock
        return 0
    root = root_of(a.agent, _registry(a.orchestra_dir))
    override = (os.environ.get("AGENT_EFFORT") or "").strip()
    if override and valid(a.runtime, override):
        if get(path, root) != override:
            with contextlib.suppress(OSError):
                put(path, root, override)      # kept, so the agent's next rotation starts on it too
        print(override)
        return 0
    level = get(path, root)
    if valid(a.runtime, level):
        print(level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
