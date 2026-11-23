"""Source health for the files Arturo reads into a turn (build_context + the knowledge tool).

A read-only audit found build_context injecting from sources that no longer have a writer on a
real install: state/users/operator/profile.json (never written), state/telegram-outbound.jsonl
(last written months ago, injected as "RECENT"), state/learning.db (no live writer) and the
knowledge tool's facts/facts_db.json (absent). gm ruled: minimal fix, skip + doctor row, no new
sources. So every read site asks this module first:

  fresh       -> read and inject exactly as before (byte-identical prompt)
  stale       -> inject NOTHING, except learning.db: still injected, heading carries "(as of <date>)"
                 (gm's design may replace learning.db; a dated heading is honest until then)
  missing     -> nothing: no heading, no empty block
  unreadable  -> nothing

`orchestra doctor` prints one advisory row per source from `doctor_rows`. Nothing here writes.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FRESH, STALE, MISSING, UNREADABLE = "fresh", "stale", "missing", "unreadable"

# Per-source freshness windows (seconds). A source older than its window is STALE: for a prompt
# block labelled "RECENT" that means it is not recent; for a profile or rule store it means the
# writer has gone quiet and the model should not treat the rows as current.
TELEGRAM_MAX_AGE = 6 * 3600          # "RECENT TELEGRAM MESSAGES": texts older than a shift are not recent
LEARNING_MAX_AGE = 7 * 24 * 3600     # "LEARNED RULES": a week without a new rule = stale, still shown dated
PROFILE_MAX_AGE = 30 * 24 * 3600     # "LEARNED FROM CONVERSATIONS": implicit facts a month old are not memory
FACTS_MAX_AGE = None                 # facts/facts_db.json is a lookup store: present or not, never "stale"


@dataclass(frozen=True)
class SourceStatus:
    state: str                  # FRESH | STALE | MISSING | UNREADABLE
    age: float | None = None    # seconds since the last write (None when missing/unreadable)
    max_age: float | None = None
    mtime: float | None = None

    @property
    def readable(self) -> bool:
        return self.state in (FRESH, STALE)

    @property
    def fresh(self) -> bool:
        return self.state == FRESH

    def as_of(self) -> str:
        """'2026-11-12' for a readable source, '' otherwise."""
        if self.mtime is None:
            return ""
        return datetime.fromtimestamp(self.mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def source_status(path, max_age=None, now=None) -> SourceStatus:
    """Classify one file. Never raises: a stat or permission error is UNREADABLE, absence is MISSING.
    `max_age=None` means the source cannot go stale (present = fresh)."""
    p = Path(path)
    try:
        if not p.exists():
            return SourceStatus(MISSING)
        if not p.is_file() or not os.access(p, os.R_OK):
            return SourceStatus(UNREADABLE)
        mtime = p.stat().st_mtime
    except OSError:
        return SourceStatus(UNREADABLE)
    age = max(0.0, (now if now is not None else time.time()) - mtime)
    if max_age is not None and age > max_age:
        return SourceStatus(STALE, age, max_age, mtime)
    return SourceStatus(FRESH, age, max_age, mtime)


def usable(path, max_age=None, now=None) -> bool:
    """True only for a FRESH source: the gate every skip-on-stale read site uses."""
    return source_status(path, max_age, now).fresh


def as_of_suffix(status: SourceStatus) -> str:
    """' (as of 2026-11-12)' for a STALE source, '' for a fresh one: appended to a heading that is
    still injected when stale (learning.db). A fresh heading stays byte-identical."""
    if status.state == STALE and status.as_of():
        return f" (as of {status.as_of()})"
    return ""


# --- the registry `orchestra doctor` walks -------------------------------------------------------

#: (row name, path under the data dir, window, what Arturo does with it when stale)
SOURCES = (
    ("profile", ("state", "users", "operator", "profile.json"), PROFILE_MAX_AGE, "skipped"),
    ("telegram", ("state", "telegram-outbound.jsonl"), TELEGRAM_MAX_AGE, "skipped"),
    ("learning", ("state", "learning.db"), LEARNING_MAX_AGE, "injected with an as-of date"),
    ("facts", ("facts", "facts_db.json"), FACTS_MAX_AGE, "n/a"),
)


def source_path(orchestra_dir, name: str) -> Path:
    for n, rel, _w, _p in SOURCES:
        if n == name:
            return Path(orchestra_dir).joinpath(*rel)
    raise KeyError(name)


def _human(seconds: float) -> str:
    s = int(seconds)
    if s < 3600:
        return f"{max(s // 60, 1)}m" if s >= 60 else f"{s}s"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def doctor_rows(orchestra_dir, now=None):
    """[(name, state, detail)] — one per source; advisory, read-only, never raises."""
    rows = []
    for name, rel, window, when_stale in SOURCES:
        path = Path(orchestra_dir).joinpath(*rel)
        rel_s = "/".join(rel)
        try:
            st = source_status(path, window, now)
        except Exception as e:  # noqa: BLE001 — a doctor row never raises
            rows.append((name, UNREADABLE, f"{rel_s}: {e}"))
            continue
        if st.state == FRESH:
            detail = f"{rel_s} fresh ({_human(st.age)} old" + (f", window {_human(window)})" if window else ")")
        elif st.state == STALE:
            detail = f"{rel_s} stale ({_human(st.age)} old, window {_human(window)}; {when_stale})"
        elif st.state == MISSING:
            detail = f"{rel_s} missing (not injected)"
        else:
            detail = f"{rel_s} unreadable (not injected)"
        rows.append((name, st.state, detail))
    return rows


def safe_import(name: str):
    """Import a dotted module and return it, or None when the import fails for ANY reason: a prompt
    source's reader is a nicety, and a broken one must never fail a turn. #604's build_context hook
    imports `services.arturo.context_files` at module level outside its try block, so an import
    error there would fail every turn, flag on or off; after that PR lands, its hook should read
    `_context_files = _source_health.safe_import("services.arturo.context_files")` and skip when None."""
    import importlib
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 — SyntaxError, ImportError, anything the module raises at import
        return None


# --- a flag as the RUNNING proxy sees it ----------------------------------------------------------

PROXY_CHILD = "arturo"   # the supervisor's child name for services/arturo/run.sh (process_table.py)


def running_flag(name: str, data_dir, env=None, proc_root="/proc", child=PROXY_CHILD):
    """(value, origin) for env var `name` as the running proxy has it: origin "proxy" when read from
    /proc/<pid>/environ of the supervisor's arturo child (pid from <data>/state/supervisor.json, the
    file doctor already reads), else "shell" from this process's env, else "unset". A doctor row that
    reports a flag must say which: doctor's own shell rarely carries what `orchestra up` gave the
    proxy. Read-only, never raises. The child pid is run.sh's; the proxy inherits that environ."""
    env = os.environ if env is None else env
    try:
        state = json.loads((Path(data_dir) / "state" / "supervisor.json").read_text())
        pid = int(((state.get("children") or {}).get(child) or {}).get("pid") or 0)
        if pid > 0:
            raw = (Path(proc_root) / str(pid) / "environ").read_bytes()
            kv = dict(item.split("=", 1) for item in raw.decode("utf-8", "replace").split("\0") if "=" in item)
            return kv.get(name), "proxy"
    except Exception:  # noqa: BLE001 — no supervisor file, dead pid, no /proc: fall back to the shell
        pass
    if name in env:
        return env[name], "shell"
    return None, "unset"
