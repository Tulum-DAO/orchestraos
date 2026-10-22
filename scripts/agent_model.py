"""Switch a running agent's model (gateway GET/PUT /agent-model).

How a switch happens depends on the runtime:

- Claude switches LIVE: the gateway types `/model <id>` into the idle pane (the verified send),
  and the CLI answers with a `<local-command-stdout>Set model to ...` line in the seat's
  transcript. That line is the confirmation; nothing else is.
- Every other runtime records the new model on the seat and takes it at its next rotation
  (spawn passes the stored model). The response says so; nothing is typed into the pane.

The catch with Claude: an interactive `/model` ALSO saves the pick as the host's default
model, in the user settings file every new Claude session on the machine reads (Claude Code
2.1.284: the save runs whenever the session is interactive, and every agent pane is). An
agent switch must not change the host. So the whole switch runs under ONE host-wide lock
that snapshots the settings file's "model" key before and puts back ONLY that key after
(temp file + atomic rename, read back to verify). Two switches can then never interleave
their snapshot and restore, and a person's own `/model` choice made earlier is what comes
back, not a fixed value.

The offered models come from one server catalog: the static lists in config/providers.json
plus whatever this login's CLIs reported to the API's live probe (state/model-catalog-live.json,
the file the Arturo brain validates against too). A client never carries its own list.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from pathlib import Path

LOCK_NAME = ".orchestra-model-switch.lock"
LIVE_CATALOG = "model-catalog-live.json"
RESPONSE_CACHE = "runtimes-available-cache.json"

# Runtimes whose pane can be switched in place. Everything else waits for its next rotation.
LIVE_RUNTIMES = {"claude"}
# grok seats do not rotate yet (rotate_agent refuses them), so a stored model would never apply.
NO_SWITCH = {"grok": "this runtime cannot switch models yet"}

_SET_MODEL = re.compile(r"<local-command-stdout>\s*Set model to\s+`?([^`<\n]+?)`?(?:\s+and saved[^<]*|\s+for this session[^<]*)?\s*</local-command-stdout>")
_STDOUT = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.S)
# A model id is typed into a pane as `/model <id>`: only this alphabet, so no catalog source
# (the live probe file included) can make it type anything else.
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,99}")


def valid_model_id(mid) -> bool:
    return isinstance(mid, str) and MODEL_ID.fullmatch(mid) is not None


# --- catalog -------------------------------------------------------------------------------

def off_runtimes(env=None) -> dict:
    """{runtime: reason} the install has switched off, from AGENT_MODEL_OFF="gemini=out of
    usage,grok=out of usage". An install setting, not a fact about the runtime: another
    server may have usage left."""
    raw = (env if env is not None else os.environ).get("AGENT_MODEL_OFF", "") or ""
    out = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip().lower()] = v.strip() or "switched off on this server"
        elif part.strip():
            out[part.strip().lower()] = "switched off on this server"
    return out


def _context_subtitle(model_id: str, caps: dict | None) -> str | None:
    if model_id.endswith("[1m]"):
        return "1M context"
    win = (caps or {}).get("context_window")
    if isinstance(win, int) and win > 0:
        return f"{win // 1000}K context" if win < 1_000_000 else f"{win // 1_000_000}M context"
    return None


def load_catalog(runtime: str, providers_path: Path, state_dir: Path) -> list[dict]:
    """[{id, title, subtitle?}] in draw order for one runtime: the API's last live answer for
    this login when it has one (labels included), else providers.json's static list, plus any
    live-only ids the static list has not heard of. Unreadable files mean "fewer rows", never
    an error: this is a cache."""
    rows: list[dict] = []
    seen: set[str] = set()

    def add(mid, label=None, caps=None):
        if valid_model_id(mid) and mid not in seen:
            seen.add(mid)
            row = {"id": mid, "title": label if isinstance(label, str) and label else mid}
            sub = _context_subtitle(mid, caps)
            if sub:
                row["subtitle"] = sub
            rows.append(row)

    try:
        cached = json.loads((state_dir / RESPONSE_CACHE).read_text())
        for p in (cached.get("providers") or []):
            if p.get("id") == runtime:
                for m in (p.get("models") or []):
                    add(m.get("id"), m.get("label"), m.get("capabilities"))
    except Exception:  # noqa: BLE001 — a cache
        pass
    try:
        data = json.loads(Path(providers_path).read_text())
        for p in data.get("providers", []):
            if p.get("id") == runtime:
                for m in ((p.get("model_catalog") or {}).get("static") or []):
                    add(m.get("id"), m.get("label"), m.get("capabilities"))
    except Exception:  # noqa: BLE001
        pass
    try:
        live = json.loads((state_dir / LIVE_CATALOG).read_text())
        for mid in ((live.get("providers") or {}).get(runtime) or []):
            add(mid)
    except Exception:  # noqa: BLE001
        pass
    return rows


def describe(runtime: str | None, current: str | None, catalog: list[dict], off: dict) -> dict:
    """The GET body (same shape as the Arturo engine picker): every row present, marked
    available or not with a plain reason, so a client draws the server's answer and never
    an empty list."""
    rt = (runtime or "").lower()
    reason = None
    if not rt:
        reason = "this agent's runtime is not known"
    elif rt in off:
        reason = off[rt]
    elif rt in NO_SWITCH:
        reason = NO_SWITCH[rt]
    elif not catalog:
        reason = "no models are known for this runtime"
    models = [dict(r, available=reason is None, **({"reason": reason} if reason else {}))
              for r in catalog]
    return {
        "runtime": rt or None,
        "model": current or None,
        "how": None if reason else ("live" if rt in LIVE_RUNTIMES else "next_rotation"),
        "allowed": [] if reason else [r["id"] for r in catalog],
        "unavailable": {r["id"]: reason for r in catalog} if reason else {},
        "models": models,
    }


# --- the host settings key -------------------------------------------------------------------

def claude_settings_path(env=None) -> Path:
    """The user settings file a `/model` save writes: <CLAUDE_CONFIG_DIR>/settings.json, else
    ~/.claude/settings.json."""
    e = env if env is not None else os.environ
    cfg = e.get("CLAUDE_CONFIG_DIR")
    return (Path(cfg) if cfg else Path(os.path.expanduser("~/.claude"))) / "settings.json"


_ABSENT = object()


def read_model_key(path: Path):
    """The settings file's "model" value, _ABSENT when the key (or the file) is not there.
    Raises on a file that exists but is not a JSON object: never guess what to put back."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return _ABSENT
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data.get("model", _ABSENT)


def write_model_key(path: Path, value) -> bool:
    """Put ONLY the "model" key back (value _ABSENT removes it), keeping every other key the
    file holds now. Temp file in the same folder + os.replace, so a reader never sees a half
    file, and the file's mode is kept. True when a read-back shows the intended value."""
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        if value is _ABSENT:
            return True
        text = "{}"
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    if data.get("model", _ABSENT) == value:
        return True
    if value is _ABSENT:
        data.pop("model", None)
    else:
        data["model"] = value
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
    return read_model_key(path) == value


JOURNAL_NAME = ".orchestra-model-switch.pending.json"


def _journal(settings_path: Path) -> Path:
    return Path(settings_path).parent / JOURNAL_NAME


def recover_pending(settings_path: Path) -> bool:
    """A switch that died between Claude's save and our restore left its snapshot here: put it
    back. Called under the lock before every switch. True when nothing is pending afterwards."""
    j = _journal(settings_path)
    try:
        rec = json.loads(j.read_text())
    except FileNotFoundError:
        return True
    except Exception:  # noqa: BLE001 — an unreadable journal is never acted on, never deleted
        return False
    value = rec.get("model", _ABSENT) if rec.get("present") else _ABSENT
    if write_model_key(settings_path, value):
        j.unlink()
        return True
    return False


def _write_journal(settings_path: Path, before) -> None:
    j = _journal(settings_path)
    rec = {"present": before is not _ABSENT, **({"model": before} if before is not _ABSENT else {})}
    fd, tmp = tempfile.mkstemp(prefix=".journal.", suffix=".tmp", dir=str(j.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, j)


@contextlib.contextmanager
def host_model_lock(settings_path: Path):
    """ONE host-wide lock around snapshot -> switch -> restore."""
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path.parent / LOCK_NAME, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# --- confirmation -----------------------------------------------------------------------------

def _entry_text(d: dict) -> str:
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def file_size(path) -> int | None:
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def scan_switch_result(jsonl_path: Path, since_iso: str, start_offset: int | None = None,
                       tail_bytes: int = 256 * 1024):
    """What the CLI said to `/model` in lines written after the send: ("set", display) for its
    "Set model to" line, ("refused", text) for any other command output, None when nothing yet.
    With `start_offset` (the file's size just before the send) only bytes appended since are
    read, so no earlier line can count whatever its timestamp; without it, entries stamped
    before `since_iso` are skipped. A torn last line is skipped."""
    try:
        with open(jsonl_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if start_offset is not None:
                if not 0 <= start_offset <= size:
                    return None             # the file shrank: no line can be proven new
                f.seek(start_offset)
            else:
                f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except Exception:  # noqa: BLE001 — torn or partial line
            continue
        if not isinstance(d, dict) or d.get("type") != "user":
            continue
        if start_offset is None and str(d.get("timestamp") or "") < since_iso:
            continue
        text = _entry_text(d)
        m = _SET_MODEL.search(text)
        if m:
            return ("set", m.group(1).strip())
        out = _STDOUT.search(text)
        if out:
            return ("refused", out.group(1).strip()[:300])
    return None


def display_matches(display: str, model_id: str, title: str | None = None) -> bool:
    """Does the CLI's "Set model to `<display>`" name the model we asked for? Its display name
    ("Opus 5.5", "Opus 5.5 (1M context)") against the catalog title, or the family and version
    read from the id (claude-opus-5-5[1m] -> opus, 5.5)."""
    d = re.sub(r"\s+", " ", (display or "").strip().lower())
    if title and re.sub(r"\s+", " ", title.strip().lower()) in d:
        return True
    m = re.fullmatch(r"(?:claude-)?([a-z]+)-(\d+)-(\d+)(?:-\d{8})?(\[1m\])?", model_id.lower())
    if not m:
        return model_id.lower() in d
    # Family and version only: how the CLI words the 1M-context variant is not pinned down, and
    # a wrong guess would call a good switch failed. The id itself (with [1m]) is what is stored.
    fam, ver = m.group(1), f"{m.group(2)}.{m.group(3)}"
    return fam in d and ver in d


def wait_for_switch(find_jsonl, since_iso: str, start_offset=None, timeout_s: float = 10.0,
                    poll_s: float = 0.25, sleep=time.sleep, clock=time.monotonic):
    """Poll the transcript (located by `find_jsonl()`, which may return None until the file is
    known) for the switch result, up to `timeout_s`."""
    deadline = clock() + timeout_s
    while True:
        path = find_jsonl()
        res = scan_switch_result(path, since_iso, start_offset) if path else None
        if res is not None:
            return res
        if clock() >= deadline:
            return None
        sleep(poll_s)


def utc_iso_now() -> str:
    """The transcript's timestamp format (UTC, millisecond, Z), so string order is time order."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def _restore(settings_path: Path, before) -> tuple[bool, str | None]:
    try:
        return write_model_key(settings_path, before), None
    except Exception as e:  # noqa: BLE001 — reported, never raised past the switch result
        return False, repr(e)


# Claude Code 2.1.284, a seat WITH history (measured 2026-10-22 on a scratch seat): `/model <id>`
# does not switch at once. It asks first, then saves and prints "Set model to ..." on "1":
#   This conversation is cached for the current model. Switching to Sonnet 5.5 means the full
#   history gets re-read on your next message.
#   ❯ 1. Yes, switch to Sonnet 5.5
#     2. No, go back
_DIALOG_YES = re.compile(r"^\s*(?:[❯›>]\s*)?1\.\s+Yes, switch to\s+(.+?)\s*$", re.M)
_DIALOG_NO = re.compile(r"^\s*(?:[❯›>]\s*)?2\.\s+No, go back\s*$", re.M)


def switch_dialog(screen: str) -> str | None:
    """The model a "Switching to X ... 1. Yes, switch to X / 2. No, go back" dialog on screen
    offers, else None."""
    yes = _DIALOG_YES.search(screen or "")
    return yes.group(1) if yes and _DIALOG_NO.search(screen or "") else None


def _wait_answer(path0, since, offset, model, title, capture, keys, timeout_s, sleep,
                 clock=time.monotonic):
    """Wait for the CLI's answer to `/model`, answering its confirm dialog on the way: "1" when it
    offers the requested model, "2" (No, go back) when it offers anything else.
    Returns (result, note): result as scan_switch_result; note "other_dialog:<name>" when a
    dialog for another model was declined."""
    deadline, answered = clock() + timeout_s, False
    while True:
        res = scan_switch_result(path0, since, offset)
        if res is not None:
            return res, None
        if capture is not None and keys is not None and not answered:
            offered = switch_dialog(capture() or "")
            if offered is not None:
                answered = True
                if display_matches(offered, model, title):
                    keys("1")
                else:
                    keys("2")
                    return None, f"other_dialog:{offered}"
        if clock() >= deadline:
            return None, None
        sleep(0.25)


def switch_live(*, model: str, settings_path: Path, send, find_jsonl, title: str | None = None,
                capture=None, keys=None,
                timeout_s: float = 10.0, settle_s: float = 3.0, recheck_s: float = 5.0,
                sleep=time.sleep, now=utc_iso_now):
    """Type `/model <model>` with `send(text) -> (ok, info)` under the host lock, wait for the
    CLI's answer, then put the host's "model" setting back as it was.

    Crash safety: the snapshot is journaled beside the settings file before anything is typed,
    and the next switch (under the same lock) restores a journal it finds. A save that lands
    late is caught by a second look `recheck_s` after the restore.

    With `capture()` (the pane's text) and `keys(k)` (one literal key), the confirm dialog a seat
    with history shows is answered INSIDE the lock ("1" for the requested model, else "2"), so the
    CLI's save lands before the restore; and a dialog still on screen when the switch ends is
    answered "2" (No, go back): a pane is never left sitting in it.

    Returns (status, body): 200 switched, 409 busy, 502 unconfirmable / unsent / failed /
    unconfirmed / refused / a different model confirmed, 500 when the host setting could not be put back (the body says
    whether the switch itself happened)."""
    if not valid_model_id(model):
        return 400, {"ok": False, "error": "bad model id"}
    with host_model_lock(settings_path):
        if not recover_pending(settings_path):
            return 500, {"ok": False, "error": "a previous switch left the host default model unrestored"}
        try:
            before = read_model_key(settings_path)
        except Exception as e:  # noqa: BLE001 — never guess what to put back
            return 500, {"ok": False, "error": "host settings unreadable", "detail": repr(e)}
        # Only lines appended after the send may confirm it, so the transcript must be known (and
        # its size read) BEFORE anything is typed. Unknown -> nothing is typed at all.
        path0 = find_jsonl()
        offset = file_size(path0)
        if offset is None:
            return 502, {"ok": False, "error": "unconfirmable",
                         "detail": "this agent's transcript was not found, so a switch could not "
                                   "be confirmed; nothing was typed"}
        _write_journal(settings_path, before)
        res, sent, note = None, False, None
        try:
            since = now()
            ok, info = send(f"/model {model}")
            sent = bool(ok)
            if not ok:
                if (info or {}).get("reason") == "busy":
                    result = (409, {"ok": False, "error": "busy",
                                    **{k: v for k, v in (info or {}).items() if k != "reason"}})
                else:
                    result = (502, {"ok": False, "error": "not sent", "detail": info})
            else:
                res, note = _wait_answer(path0, since, offset, model, title, capture, keys,
                                         timeout_s, sleep)
                if note:
                    result = (502, {"ok": False, "error": "the agent offered a different model",
                                    "detail": note.split(":", 1)[1]})
                # The CLI writes the setting before it prints, unless its own save timed out
                # (then the write may still land): give a late write time to land first.
                sleep(settle_s)
                if not note:
                    result = None
        except Exception as e:  # noqa: BLE001 — answered, never raised past the restore
            result = (502, {"ok": False, "error": "switch failed", "detail": repr(e)[:300],
                            "sent": sent})
        finally:
            if sent and capture is not None and keys is not None:
                with contextlib.suppress(Exception):    # never leave the pane in the dialog
                    if switch_dialog(capture() or "") is not None:
                        keys("2")
                        sleep(0.5)
            restored, err = _restore(settings_path, before)
            if restored and sent:
                with contextlib.suppress(Exception):    # a save later than the settle
                    sleep(recheck_s)
                restored, err = _restore(settings_path, before)
            if restored:
                with contextlib.suppress(FileNotFoundError):
                    _journal(settings_path).unlink()
        if not restored:
            return 500, {"ok": False, "error": "host default model not restored",
                         "switched": bool(res and res[0] == "set"), **({"detail": err} if err else {})}
        if result is not None:
            return result
        if res is None:
            return 502, {"ok": False, "error": "unconfirmed",
                         "detail": "no 'Set model to' line in the transcript within "
                                   f"{int(timeout_s)} s"}
        if res[0] == "refused":
            return 502, {"ok": False, "error": "refused", "detail": res[1]}
        if not display_matches(res[1], model, title):
            return 502, {"ok": False, "error": "a different model was confirmed", "detail": res[1]}
        return 200, {"ok": True, "model": model, "switching": "switched", "display": res[1]}
