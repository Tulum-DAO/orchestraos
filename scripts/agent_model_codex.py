"""Switch a running Codex agent's model live (the codex half of GET/PUT /agent-model).

Measured by effect on codex 0.153.4 (a fenced scratch seat, 2026-10-22):
- `/model <id>` typed WITH an argument is not a command. It reaches the model as a prompt,
  and the model answers "Model selection updated to <id>." while the next turn still runs the
  old model. So that form is never typed, and no screen or reply text is ever a confirmation.
- The TUI runs the agent in-process; the app-server control socket cannot reach it.
- What works is the picker: `/model` opens "Select Model and Effort" (a numbered list), a digit
  picks the model, "Select Reasoning Level for <id>" (numbered) follows, a digit picks the
  effort. The rollout then records `event_msg {type: thread_settings_applied, thread_settings:
  {model: <id>}}`: that line, written after the send, is the only confirmation.
- Each pick ALSO rewrites `model` and `model_reasoning_effort` in ~/.codex/config.toml, the
  host default every new codex session reads. Like the Claude path, the switch runs under the
  one host model-switch lock and puts back exactly those two top-level keys afterwards.

The digits are read from the captured picker, never assumed: a row's number is whatever the
screen shows for the requested id, and a picker that does not list it is closed and refused.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from scripts import agent_model as AM

KEYS = ("model", "model_reasoning_effort")
JOURNAL_NAME = ".orchestra-model-switch.pending.json"

_ROW = re.compile(r"^\s*(?:[›>❯]\s*)?(\d{1,2})\.\s+(\S+)(.*)$")
_EFFORT_LABEL = {"low": "low", "medium": "medium", "high": "high", "xhigh": "extra high",
                 "extra high": "extra high", "minimal": "minimal"}


def codex_home(env=None) -> Path:
    e = env if env is not None else os.environ
    return Path(e.get("CODEX_HOME") or os.path.expanduser("~/.codex"))


def rollout_for_sid(sid: str, home: Path | None = None) -> Path | None:
    """~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sid>.jsonl, newest if several."""
    if not isinstance(sid, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", sid):
        return None
    hits = sorted((home or codex_home()).joinpath("sessions").glob(f"*/*/*/rollout-*-{sid}.jsonl"),
                  key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


# --- the picker on screen ---------------------------------------------------------------------

PICKER_FOOTER = "Press enter to confirm or esc to go back"


def picker_live(screen: str) -> bool:
    """A picker is OPEN only while its footer is the screen's last non-empty line: an open
    picker replaces the composer at the bottom, and once it closes the composer is back there.
    A picker still visible higher up (or in an old capture) is not open."""
    lines = [ln for ln in (screen or "").splitlines() if ln.strip()]
    return bool(lines) and PICKER_FOOTER in lines[-1]


def _section(screen: str, title_re: str) -> list[str] | None:
    """The lines after the LAST line matching `title_re`, only while that picker is open."""
    if not picker_live(screen):
        return None
    lines = screen.splitlines()
    idx = [i for i, ln in enumerate(lines) if re.search(title_re, ln)]
    return lines[idx[-1] + 1:] if idx else None


def model_rows(screen: str) -> dict[str, str] | None:
    """{model id: digit} from a captured "Select Model and Effort" picker; None when it is not
    on screen."""
    body = _section(screen, r"Select Model and Effort")
    if body is None:
        return None
    rows = {}
    for ln in body:
        m = _ROW.match(ln)
        if m:
            rows.setdefault(m.group(2), m.group(1))
    return rows


def effort_rows(screen: str, model: str) -> dict[str, str] | None:
    """{effort label (lowercase, "(default)" dropped): digit} from "Select Reasoning Level for
    <model>"; None when that picker (for that model) is not on screen."""
    body = _section(screen, r"Select Reasoning Level for\s+" + re.escape(model) + r"\b")
    if body is None:
        return None
    rows = {}
    for ln in body:
        m = re.match(r"^\s*(?:[›>❯]\s*)?(\d{1,2})\.\s+(.+?)\s{2,}", ln + "  ")
        if m:
            label = re.sub(r"\s*\(default\)\s*$", "", m.group(2)).strip().lower()
            rows.setdefault(label, m.group(1))
            if "(default)" in m.group(2):
                rows.setdefault("(default)", m.group(1))
    return rows


def pick_effort(rows: dict[str, str], current: str | None) -> tuple[str, str]:
    """(digit, label) for the agent's current effort, else the picker's default row."""
    want = _EFFORT_LABEL.get((current or "").lower())
    if want and want in rows:
        return rows[want], want
    if "(default)" in rows:
        label = next((k for k, v in rows.items() if v == rows["(default)"] and k != "(default)"), "default")
        return rows["(default)"], label
    raise LookupError("the effort picker shows no usable row")


# --- the rollout ------------------------------------------------------------------------------

def current_effort(rollout: Path) -> str | None:
    """The effort of the rollout's latest turn_context."""
    eff = None
    try:
        with open(rollout, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512 * 1024))
            for line in f.read().decode("utf-8", "replace").splitlines():
                with contextlib.suppress(Exception):
                    d = json.loads(line)
                    if d.get("type") == "turn_context":
                        eff = (d.get("payload") or {}).get("effort") or eff
    except OSError:
        return None
    return eff if isinstance(eff, str) else None


def rollout_thread_id(rollout: Path) -> str | None:
    """The thread this rollout belongs to: the uuid in its file name."""
    m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$",
                  str(rollout))
    return m.group(1).lower() if m else None


def applied_after(rollout: Path, offset: int, model: str):
    """"set" when a thread_settings_applied for `model`, for THIS rollout's thread, was written
    after `offset`; "other:<id>" for one naming another model; None for nothing yet (or a file
    that shrank). An event for another thread never counts."""
    thread = rollout_thread_id(rollout)
    try:
        with open(rollout, "rb") as f:
            f.seek(0, os.SEEK_END)
            if not 0 <= offset <= f.tell():
                return None
            f.seek(offset)
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    seen = None
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001 — torn line
            continue
        p = d.get("payload") if isinstance(d, dict) else None
        if d.get("type") == "event_msg" and isinstance(p, dict) and p.get("type") == "thread_settings_applied":
            if not thread or str(p.get("thread_id") or "").lower() != thread:
                continue
            got = (p.get("thread_settings") or {}).get("model")
            if got == model:
                return "set"
            seen = f"other:{got}"
    return seen


# --- config.toml: two top-level keys, line by line ----------------------------------------------

_STRINGS = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\])*"|\'[^\']*\'')
_HEADER = re.compile(r"^\s*\[\[?[^\[\]=]+\]\]?\s*(#.*)?$")      # [table] / [[array]]; values are excluded by depth


def _read(path: Path) -> str:
    """The file's text with its line endings exactly as stored (no newline translation)."""
    with open(path, newline="") as f:
        return f.read()


def _top_level_lines(lines: list[str]) -> list[bool]:
    """For each line: is it a TOP-LEVEL line (above the first table header, and not inside a
    multi-line value)? A `[` that opens a line inside an open array is a value, not a header."""
    out, depth, top = [], 0, True
    for ln in lines:
        bare = _STRINGS.sub('""', ln.rstrip("\r\n"))
        bare = bare.split("#", 1)[0]
        if top and depth == 0 and _HEADER.match(ln.rstrip("\r\n")):
            top = False
        out.append(top and depth == 0)
        depth = max(0, depth + bare.count("[") - bare.count("]") + bare.count("{") - bare.count("}"))
    return out


def _key_of(ln: str):
    return next((k for k in KEYS if re.match(rf"^\s*{re.escape(k)}\s*=", ln)), None)


def read_keys(path: Path) -> dict:
    """{key: [its top-level lines, byte for byte, in order]} for KEYS (absent keys omitted)."""
    try:
        lines = _read(path).splitlines(keepends=True)
    except FileNotFoundError:
        return {}
    out: dict = {}
    for ln, top in zip(lines, _top_level_lines(lines)):
        k = _key_of(ln) if top else None
        if k:
            out.setdefault(k, []).append(ln)
    return out


def write_keys(path: Path, before: dict) -> bool:
    """Put KEYS' top-level lines back exactly as `before` had them (every occurrence, its own
    bytes and line ending; absent -> removed), leaving every other line as it is now. Temp
    file + rename, mode kept, read back."""
    path = Path(path)
    try:
        text = _read(path)
    except FileNotFoundError:
        if not before:
            return True
        text = ""
    lines = text.splitlines(keepends=True)
    pending = {k: list(v) for k, v in before.items() if k in KEYS}
    kept = []
    for ln, top in zip(lines, _top_level_lines(lines)):
        k = _key_of(ln) if top else None
        if k is None:
            kept.append(ln)
        elif pending.get(k):
            kept.append(pending[k].pop(0))
    nl = "\r\n" if "\r\n" in text else "\n"
    missing = [ln if ln.endswith(("\n", "\r")) else ln + nl for k in KEYS for ln in pending.get(k, [])]
    new = "".join(missing + kept)
    if new != text:
        mode = None
        with contextlib.suppress(FileNotFoundError):
            mode = path.stat().st_mode & 0o7777
        fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", newline="") as f:
                f.write(new)
            if mode is not None:
                os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
    return read_keys(path) == {k: v for k, v in before.items() if k in KEYS and v}


def _journal(config: Path) -> Path:
    return Path(config).parent / JOURNAL_NAME


def recover_pending(config: Path) -> bool:
    j = _journal(config)
    try:
        rec = json.loads(j.read_text())
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001 — never act on an unreadable journal
        return False
    if isinstance(rec, dict) and write_keys(config, {k: v for k, v in rec.items() if k in KEYS}):
        j.unlink()
        return True
    return False


def _write_journal(config: Path, before: dict) -> None:
    j = _journal(config)
    fd, tmp = tempfile.mkstemp(prefix=".journal.", suffix=".tmp", dir=str(j.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(before, f)
    os.replace(tmp, j)


# --- the switch -------------------------------------------------------------------------------

def _wait_screen(capture, parse, timeout_s, sleep, clock):
    deadline = clock() + timeout_s
    while True:
        got = parse(capture() or "")
        if got is not None:
            return got
        if clock() >= deadline:
            return None
        sleep(0.25)


def switch_live(*, model: str, config_path: Path, lock_path: Path, send, keys, capture,
                rollout: Path | None, timeout_s: float = 10.0, screen_timeout_s: float = 5.0,
                recheck_s: float = 3.0, sleep=time.sleep, clock=time.monotonic):
    """Drive the codex picker to `model` and confirm it from the rollout.

    send(text) -> (ok, info): the verified send (refuses a busy pane). keys(k): one literal key
    (a digit, or "Escape"). capture() -> the pane's text. Returns (status, body) like
    agent_model.switch_live: 200 switched, 409 busy, 422 not in this codex's picker, 502
    unconfirmable / unsent / picker did not open / failed / unconfirmed / a different model,
    500 when config.toml could not be put back."""
    if not AM.valid_model_id(model):
        return 400, {"ok": False, "error": "bad model id"}
    offset = AM.file_size(rollout)
    if offset is None:
        return 502, {"ok": False, "error": "unconfirmable",
                     "detail": "this agent's rollout was not found, so a switch could not be "
                               "confirmed; nothing was typed"}
    with AM.host_model_lock(lock_path):
        if not recover_pending(config_path):
            return 500, {"ok": False, "error": "a previous switch left the host default model unrestored"}
        before = read_keys(config_path)
        _write_journal(config_path, before)
        effort_now = current_effort(rollout)
        res, typed = None, False
        try:
            # Only events written after THIS point can confirm: read the offset right before
            # the send, under the lock (the size read above only proves the rollout exists).
            offset = AM.file_size(rollout)
            if offset is None:
                raise FileNotFoundError(f"rollout vanished: {rollout}")
            ok, info = send("/model")
            typed = bool(ok)
            if not ok:
                if (info or {}).get("reason") == "busy":
                    result = (409, {"ok": False, "error": "busy",
                                    **{k: v for k, v in (info or {}).items() if k != "reason"}})
                else:
                    result = (502, {"ok": False, "error": "not sent", "detail": info})
            else:
                rows = _wait_screen(capture, model_rows, screen_timeout_s, sleep, clock)
                if rows is None:
                    result = (502, {"ok": False, "error": "the model picker did not open"})
                elif model not in rows:
                    result = (422, {"ok": False, "error": "not in this agent's model picker",
                                    "offered": sorted(rows)})
                elif len(rows[model]) != 1:
                    # A two-digit row would go in as two keys: the first picks another row.
                    result = (422, {"ok": False, "error": "this model's picker row cannot be "
                                    "picked with one key", "row": rows[model]})
                else:
                    keys(rows[model])
                    eff = _wait_screen(capture, lambda s: effort_rows(s, model), screen_timeout_s,
                                       sleep, clock)
                    if eff is None:
                        result = (502, {"ok": False, "error": "the effort picker did not open"})
                    else:
                        digit, label = pick_effort(eff, effort_now)
                        if len(digit) != 1:
                            raise LookupError(f"effort row {digit} cannot be picked with one key")
                        keys(digit)
                        res = _wait_screen(lambda: applied_after(rollout, offset, model),
                                           lambda r: r or None, timeout_s, sleep, clock)
                        result = None
                        effort_label = label
        except Exception as e:  # noqa: BLE001 — answered, never raised past the restore
            result = (502, {"ok": False, "error": "switch failed", "detail": repr(e)[:300]})
        finally:
            if typed:                             # never leave the agent sitting in a menu
                with contextlib.suppress(Exception):
                    # Escape only while a picker is OPEN on screen, re-read each time: an Escape
                    # on the empty composer primes codex's "edit previous message" (seen by effect).
                    for _ in range(3):
                        if not picker_live(capture() or ""):
                            break
                        keys("Escape")
                        sleep(0.3)
            restored, err = _restore(config_path, before)
            if restored and typed:
                with contextlib.suppress(Exception):
                    sleep(recheck_s)
                restored, err = _restore(config_path, before)
            if restored:
                with contextlib.suppress(FileNotFoundError):
                    _journal(config_path).unlink()
        if not restored:
            return 500, {"ok": False, "error": "host default model not restored",
                         "switched": res == "set", **({"detail": err} if err else {})}
        if result is not None:
            return result
        if res is None:
            return 502, {"ok": False, "error": "unconfirmed",
                         "detail": f"no thread_settings_applied for {model} in the rollout within "
                                   f"{int(timeout_s)} s"}
        if res != "set":
            return 502, {"ok": False, "error": "a different model was confirmed", "detail": res[6:]}
        return 200, {"ok": True, "model": model, "switching": "switched", "effort": effort_label}


def _restore(config: Path, before: dict):
    try:
        return write_keys(config, before), None
    except Exception as e:  # noqa: BLE001
        return False, repr(e)
