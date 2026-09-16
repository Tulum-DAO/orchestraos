"""Runtime catalog probes — the Python twin of api/src/routes/runtimes-available.ts.

ONE probe definition source: config/providers.json. The API route interprets it in
TypeScript for GET /api/runtimes/available; `orchestra doctor` interprets the SAME
definitions here (same kinds, same verdicts, same auth_reason strings) so an
operator never sees two dialects disagree. Keep the two interpreters in lockstep;
tests/test_runtime_probe.py pins the verdict table.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

KNOWN_PROBE_KINDS = ("cli-json", "file-json-key", "file-json-expiry")


@dataclass
class ProbeDeps:
    which: Callable[[str], Optional[str]]
    run_cmd: Callable[[list], str]            # returns stdout; raises on failure
    read_file: Callable[[str], str]           # raises FileNotFoundError / OSError
    now_ms: Callable[[], int]
    expand_home: Callable[[str], str]


def default_deps() -> ProbeDeps:
    def _run(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=20,
                              check=True, stdin=subprocess.DEVNULL).stdout

    def _read(path):
        return Path(path).read_text()

    return ProbeDeps(which=shutil.which, run_cmd=_run, read_file=_read,
                     now_ms=lambda: int(time.time() * 1000),
                     expand_home=os.path.expanduser)


def load_providers(path: Path) -> list:
    return json.loads(Path(path).read_text())["providers"]


def _get_by_path(obj, dotted: str):
    cur = obj
    for key in dotted.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _parse_iso_ms(s: str) -> Optional[int]:
    """Date.parse() equivalent for the ISO shapes the auth files carry."""
    from datetime import datetime, timezone
    txt = s.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def run_auth_probe(probe: dict, deps: ProbeDeps) -> dict:
    try:
        kind = probe.get("kind")
        if kind == "cli-json":
            cmd = probe.get("cmd")
            if not cmd:
                return {"authed": "unverified", "auth_reason": "no-probe-cmd-configured"}
            try:
                out = deps.run_cmd(shlex.split(cmd))
            except Exception:  # noqa: BLE001 — CLI probe failed to run
                if probe.get("fallback"):
                    return run_auth_probe(probe["fallback"], deps)
                return {"authed": "unverified", "auth_reason": "auth-probe-cmd-failed"}
            parsed = json.loads(out)
            key = probe.get("success_key") or "loggedIn"
            val = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(val, bool):
                return {"authed": True} if val else {"authed": False, "auth_reason": f"{key}=false"}
            if probe.get("fallback"):
                return run_auth_probe(probe["fallback"], deps)
            return {"authed": "unverified", "auth_reason": f"auth-probe-missing-key:{key}"}

        if kind == "file-json-key":
            path = deps.expand_home(probe.get("path") or "")
            try:
                parsed = json.loads(deps.read_file(path))
            except (FileNotFoundError, OSError):
                return {"authed": False, "auth_reason": "auth-file-missing"}
            key = probe.get("key")
            present = _get_by_path(parsed, key) if key else None
            if present is None:
                return {"authed": False, "auth_reason": f"auth-file-missing-key:{key}"}
            return {"authed": True}

        if kind == "file-json-expiry":
            path = deps.expand_home(probe.get("path") or "")
            try:
                parsed = json.loads(deps.read_file(path))
            except (FileNotFoundError, OSError):
                return {"authed": False, "auth_reason": "auth-file-missing"}
            ekey = probe.get("expiry_key")
            raw = _get_by_path(parsed, ekey) if ekey else None
            if not isinstance(raw, str):
                return {"authed": "unverified", "auth_reason": f"auth-file-missing-expiry:{ekey}"}
            ms = _parse_iso_ms(raw)
            if ms is None:
                return {"authed": "unverified", "auth_reason": "auth-file-unparseable-expiry"}
            return {"authed": True} if ms > deps.now_ms() else {"authed": False, "auth_reason": "token-expired"}

        return {"authed": "unverified", "auth_reason": f"unknown-probe-kind:{kind}"}
    except Exception:  # noqa: BLE001
        return {"authed": "unverified", "auth_reason": "auth-probe-threw"}


def probe_provider(provider: dict, deps: ProbeDeps) -> dict:
    installed = bool(deps.which(provider["cli"]))
    auth = run_auth_probe(provider["auth_probe"], deps) if installed \
        else {"authed": False, "auth_reason": "not-installed"}
    return {"id": provider["id"], "label": provider.get("label", provider["id"]),
            "cli": provider["cli"], "installed": installed,
            "authed": auth["authed"], "auth_reason": auth.get("auth_reason")}


def probe_all(providers: list, enabled: list, deps: ProbeDeps | None = None) -> list:
    deps = deps or default_deps()
    want = set(enabled)
    return [probe_provider(p, deps) for p in providers if p["id"] in want]
