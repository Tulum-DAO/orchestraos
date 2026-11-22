"""The rendered context views Arturo reads each turn (memory slice 1, step 5).

gm keeps five small, ranked files under ORCHESTRA_DIR/state/context/, each replaced atomically by the
collector: operator.md, now.md, projects.md, decisions.md, recent.md. Their format is fixed:

    as_of: 2026-11-22T23:08:18Z
    stale_after: 2026-11-29T23:08:18Z
    sources: c0110ed411d9b, ...
    ---
    <one plain sentence> [c:<id>] [c:<id>]
    ...
    …cut, N more            (only when the writer's cap cut lines)

This module only READS those files (never any context.db, never a write). Every reader here is
total: a missing, truncated or malformed file is a status, not an exception, so a bad file can
never fail a turn. The block it renders goes into build_context behind ARTURO_CONTEXT_FILES=1 and
only for fleet principals; the citations are stripped before the text reaches the model.

Contract (orchestra-builder, 2026-11-22): as_of is when the view last matched its candidates and
stale_after = as_of + the block's window, so a view past stale_after is a real stale signal and is
announced "as of <time>". Times in the files are UTC with a Z; the block speaks them in the
operator's zone (the operator_store `timezone` fact, else [operator] timezone in orchestra.toml,
else UTC). An empty view (header, `---`, no lines) is treated exactly like a missing one.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

FLAG = "ARTURO_CONTEXT_FILES"
FILES = ("operator", "now", "projects", "decisions", "recent")
#: The writer's hard caps (chars of body text). Enforced again on read, so an over-cap file can
#: never grow the prompt past what the contract allows.
CAPS = {"operator": 4000, "now": 2500, "projects": 3000, "decisions": 1500, "recent": 1500}
VOICE_FILES = ("operator", "now", "projects", "decisions")
VOICE_PROJECT_LINES = 5
VOICE_CAP = 8000
#: Spoken turns get the short voice block; everything else (telegram, dashboard, text) the full one.
VOICE_CHANNELS = frozenset({"voice", "ptt"})
TITLE = "WHAT I KNOW NOW"

_CITATION = re.compile(r"\s*\[c:[^\]\n]*\]")
_CUT_LINE = re.compile(r"^…cut, (\d+) more$")
_HEADER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")

FRESH, STALE, MISSING, MALFORMED, EMPTY = "fresh", "stale", "missing", "malformed", "empty"


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get(FLAG) == "1"


def context_dir(orchestra_dir) -> Path:
    return Path(orchestra_dir) / "state" / "context"


@dataclass
class View:
    name: str
    status: str                       # FRESH | STALE | MISSING | MALFORMED | EMPTY
    path: Path
    as_of: datetime | None = None
    stale_after: datetime | None = None
    sources: list = field(default_factory=list)
    lines: list = field(default_factory=list)   # body lines, citations still attached
    detail: str = ""                  # why, for MALFORMED (doctor prints it)

    @property
    def present(self) -> bool:
        return self.status in (FRESH, STALE)


def strip_citations(line: str) -> str:
    return _CITATION.sub("", line).strip()


def _parse_time(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cap_lines(lines: list, cap: int) -> list:
    """Cut at the line boundary so the body text fits `cap` chars, with the visible last line the
    writer uses. A cut line already at the end counts toward "N more"."""
    total = 0
    kept = []
    for i, line in enumerate(lines):
        total += len(line) + (1 if kept else 0)
        if total > cap:
            dropped = lines[i:]
            n = len(dropped)
            m = _CUT_LINE.match(dropped[-1])
            if m:
                n = n - 1 + int(m.group(1))
            kept.append(f"…cut, {n} more")
            return kept
        kept.append(line)
    return kept


def read_view(path, name: str, now: datetime | None = None) -> View:
    """One file -> a View. Never raises: a missing file is MISSING, anything that does not parse as
    the contract (no `---`, no as_of/stale_after, an unparsable time, undecodable bytes) is
    MALFORMED with a one-line reason; a file cut short after its `---` is just shorter."""
    path = Path(path)
    now = now or datetime.now(timezone.utc)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return View(name, MISSING, path, detail="no file")
    except OSError as e:
        return View(name, MALFORMED, path, detail=f"unreadable ({type(e).__name__})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return View(name, MALFORMED, path, detail="not utf-8")
    header, body, seen_sep = {}, [], False
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not seen_sep:
            if line.strip() == "---":
                seen_sep = True
                continue
            m = _HEADER_KEY.match(line.strip())
            if m:
                header[m.group(1)] = m.group(2).strip()
            elif line.strip():
                return View(name, MALFORMED, path, detail=f"header line is not key: value ({line.strip()[:40]!r})")
        elif line.strip():
            body.append(line.strip())
    if not seen_sep:
        return View(name, MALFORMED, path, detail="no `---` after the header")
    as_of = _parse_time(header.get("as_of", ""))
    stale_after = _parse_time(header.get("stale_after", ""))
    if as_of is None:
        return View(name, MALFORMED, path, detail="as_of missing or not a time")
    if stale_after is None:
        return View(name, MALFORMED, path, detail="stale_after missing or not a time")
    sources = [s.strip() for s in header.get("sources", "").split(",") if s.strip()]
    if not body:
        return View(name, EMPTY, path, as_of=as_of, stale_after=stale_after, sources=sources, detail="no lines")
    status = STALE if now > stale_after else FRESH
    lines = _cap_lines(body, CAPS.get(name, VOICE_CAP))
    return View(name, status, path, as_of=as_of, stale_after=stale_after, sources=sources, lines=lines)


def read_all(orchestra_dir, now: datetime | None = None) -> list:
    d = context_dir(orchestra_dir)
    now = now or datetime.now(timezone.utc)
    return [read_view(d / f"{name}.md", name, now) for name in FILES]


def operator_zone(state_dir=None):
    """The operator's zone as a tzinfo: the operator_store `timezone` fact (what the brain learned),
    else [operator] timezone in orchestra.toml (scripts/notify_policy), else UTC. An unknown name is
    UTC too. Never raises."""
    names = []
    if state_dir is not None:
        try:
            from services.arturo import operator_store as _ops
            names.append(_ops.get(state_dir, "timezone"))
        except Exception:  # noqa: BLE001
            pass
    try:
        import sys as _sys
        root = str(Path(__file__).resolve().parent.parent.parent / "scripts")
        if root not in _sys.path:
            _sys.path.insert(0, root)
        import notify_policy as _np
        names.append(_np.install_user_and_zone()[1])
    except Exception:  # noqa: BLE001 — no config, no scripts dir: UTC
        pass
    for name in names:
        if name and isinstance(name, str):
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo(name.strip())
            except Exception:  # noqa: BLE001 — an unknown zone reads as UTC, as the app shows times
                continue
    return timezone.utc


def _hhmm(dt: datetime, zone) -> str:
    return dt.astimezone(zone).strftime("%H:%M %Z")


def _as_given(dt: datetime, zone=timezone.utc) -> str:
    return dt.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def _section(view: View, zone, limit: int | None = None) -> list:
    head = view.name.upper()
    if view.status == STALE:
        head += f" (as of {_as_given(view.as_of, zone)}, past its refresh time)"
    lines = [strip_citations(x) for x in view.lines]
    if limit is not None and len(lines) > limit:
        lines = lines[:limit]
    return [head + ":"] + [x for x in lines if x]


def block(orchestra_dir, voice: bool, now: datetime | None = None, zone=None) -> str | None:
    """The prompt block, or None when no view is readable (nothing to announce). Voice: operator,
    now, the top VOICE_PROJECT_LINES project lines and decisions, within VOICE_CAP chars. Text: all
    five files. Stale files are announced with their as_of; missing, empty and malformed files are
    skipped (the doctor says which). Times are spoken in `zone` (UTC when None)."""
    zone = zone or timezone.utc
    views = {v.name: v for v in read_all(orchestra_dir, now)}
    names = VOICE_FILES if voice else FILES
    shown = [views[n] for n in names if views[n].present and views[n].lines]
    if not shown:
        return None
    newest = max(v.as_of for v in shown)
    lines = [f"{TITLE} (as of {_hhmm(newest, zone)}; the context views kept for you, read every turn, "
             "facts not opinions):"]
    for v in shown:
        lines.extend(_section(v, zone, VOICE_PROJECT_LINES if voice and v.name == "projects" else None))
    if voice:
        lines = _cap_lines(lines, VOICE_CAP)
    return "\n".join(lines)


def doctor_rows(orchestra_dir, now: datetime | None = None) -> list:
    """One (name, status, detail) per file for `orchestra doctor`: present/fresh, stale, missing,
    empty, malformed. Never raises."""
    rows = []
    for v in read_all(orchestra_dir, now):
        if v.status == FRESH:
            detail = f"present, fresh (as_of {_as_given(v.as_of)}, {len(v.lines)} line(s))"
        elif v.status == STALE:
            detail = f"stale since {_as_given(v.stale_after)} (as_of {_as_given(v.as_of)})"
        elif v.status == MISSING:
            detail = f"missing: {v.path}"
        elif v.status == EMPTY:
            detail = f"empty (header only, as_of {_as_given(v.as_of)}): {v.path}"
        else:
            detail = f"malformed: {v.detail} ({v.path})"
        rows.append((v.name, v.status, detail))
    return rows
