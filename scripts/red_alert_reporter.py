#!/usr/bin/env python3
"""red_alert_reporter.py — deterministic structured auto-file for RED ALERT reports.

This is the ONLY outbound GitHub path that may run without a human at the keyboard.
It is ordinary code, never an LLM or agent: no hook, MCP, tool, or prompt can trigger,
compose, or edit an issue through this module.

Lethal-trifecta rule: auto-filed issues contain ONLY fixed, allowlisted structured
fields (enums, ints, version/commit, OS/arch, counts, timestamps, fingerprint hash).
Unknown fields are dropped, not scrubbed. Free text (logs, pane dumps, prompts, paths,
user data, stack traces) stays in the local report and leaves the box only via the
human preview + confirm path (or a prefilled URL the human submits).

The GitHub token lives in a 0600 token file (default: <data>/state/github-reporter-token).
It is never placed in seat/agent environment (child_env strips GITHUB_TOKEN / GH_TOKEN).
The outbound repo is pinned in settings; issue content cannot change the target.

    python3 scripts/red_alert_reporter.py payload <report-id>
    python3 scripts/red_alert_reporter.py auto-file <report-id> [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Catalogue classes plus a user-flagged bucket. Anything else is dropped (alert_type omitted
# and auto-file is skipped — we never copy an unknown string through).
ALERT_TYPES = frozenset({
    "process_suspended",
    "pane_dead",
    "out_of_usage",
    "api_error",
    "login_screen",
    "bypass_permissions_dialog",
    "composer_stuck",
    "tmux_server_dead",
    "green_died",
    "api_health_fail",
    "gateway_unreachable",
    "user_report",
})

COMPONENTS = frozenset({
    "gateway", "api", "dashboard", "arturo", "telemetryd", "bus", "router",
    "seat", "tmux", "hooks", "db", "unknown",
})

# class -> component. Missing class maps to "unknown".
_COMPONENT_FOR_CLASS = {
    "process_suspended": "seat",
    "pane_dead": "seat",
    "out_of_usage": "seat",
    "api_error": "api",
    "login_screen": "seat",
    "bypass_permissions_dialog": "seat",
    "composer_stuck": "seat",
    "tmux_server_dead": "tmux",
    "green_died": "seat",
    "api_health_fail": "api",
    "gateway_unreachable": "gateway",
    "user_report": "unknown",
}

# Keys that may appear in a structured auto-file payload / issue body. Nothing else.
ALLOWLIST_FIELDS = (
    "schema",
    "alert_type",
    "component",
    "error_code",
    "exit_code",
    "http_status",
    "orchestraos_version",
    "orchestraos_commit",
    "os",
    "arch",
    "count",
    "seat_count",
    "created_at",
    "fingerprint",
)

SCHEMA = "orchestraos-red-alert-v1"
MAX_STR = 64
MAX_BODY = 2000
DEFAULT_REPO = "Tulum-DAO/orchestraos"
ISSUE_LABEL = "red-alert"
DEFAULT_TOKEN_NAME = "github-reporter-token"
RATE_GLOBAL_PER_DAY = 10
RATE_COMMENT_PER_FP_HOURS = 6
GITHUB_TOKEN_ENV_KEYS = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_PAT",
    "GH_PAT",
    "ORCHESTRA_GITHUB_TOKEN",
    "ORCHESTRA_GITHUB_REPORTER_TOKEN",
    "GITHUB_REPORTER_TOKEN",
)


def _clamp_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Only a tight token: alphanumerics, dot, underscore, hyphen, plus. No paths, URLs, spaces.
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", s):
        return None
    return s[:MAX_STR]


def _clamp_enum(value: Any, allowed: frozenset[str]) -> str | None:
    s = _clamp_str(value)
    if s is None or s not in allowed:
        return None
    return s


def _clamp_int(value: Any, *, lo: int = -2_147_483_648, hi: int = 2_147_483_647) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d{1,10}", value.strip()):
        n = int(value.strip())
    else:
        return None
    if n < lo or n > hi:
        return None
    return n


def _clamp_ts(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    # ISO-8601 date/time only (no free text).
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", s):
        return None
    return s[:32]


def token_file_path() -> Path:
    """Path of the reporter token file. Never taken from issue/report content."""
    override = (os.environ.get("ORCHESTRA_GITHUB_REPORTER_TOKEN_FILE") or "").strip()
    # child_env strips this from seats/beats; only a process that already has it (operator
    # shell, tests) honors the override. Default path is under the data dir.
    if override:
        return Path(os.path.expanduser(override))
    data = Path(os.environ.get("ORCHESTRA_DIR") or "")
    if not data.is_dir():
        try:
            from orchestra_cli.settings import data_dir
            data = Path(data_dir())
        except Exception:
            data = ROOT
    return data / "state" / DEFAULT_TOKEN_NAME


def read_reporter_token() -> str:
    """Return the token from the 0600 file, or ''. Never logs the value. Never reads GITHUB_TOKEN."""
    path = token_file_path()
    try:
        if not path.is_file():
            return ""
        mode = path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            # Group/world-readable: refuse to use it (doctor will warn).
            return ""
        token = path.read_text().strip()
        return token
    except OSError:
        return ""


def load_pinned_repo() -> str:
    """owner/name from env, then orchestra.toml, else the public default. Never from report content."""
    env = (os.environ.get("ORCHESTRA_GITHUB_ISSUES_REPO") or "").strip()
    if env and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", env):
        return env
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    cfg = Path(os.environ.get("ORCHESTRA_CONFIG") or (ROOT / "orchestra.toml"))
    if cfg.is_file():
        try:
            raw = tomllib.loads(cfg.read_text())
            repo = ((raw.get("github_issues") or {}).get("repo") or "").strip()
            if repo and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                return repo
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_REPO


def _git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return _clamp_str(r.stdout)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _orchestra_version() -> str | None:
    try:
        from orchestra_cli import upgrade_cmd
        vs = upgrade_cmd.installed_version() if hasattr(upgrade_cmd, "installed_version") else None
        if vs:
            return _clamp_str(str(vs))
    except Exception:
        pass
    # Fallback: VERSION file or package.
    for p in (ROOT / "VERSION", ROOT / "version"):
        try:
            if p.is_file():
                return _clamp_str(p.read_text().splitlines()[0])
        except OSError:
            continue
    return None


def _http_status(report: dict) -> int | None:
    ev = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    for key in ("http_status", "status_code", "exit_http"):
        n = _clamp_int(ev.get(key) if ev else None, lo=100, hi=599)
        if n is not None:
            return n
        n = _clamp_int(report.get(key), lo=100, hi=599)
        if n is not None:
            return n
    return None


def _exit_code(report: dict) -> int | None:
    ev = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    for src in (report, ev or {}):
        n = _clamp_int(src.get("exit_code"), lo=-128, hi=255)
        if n is not None:
            return n
    return None


def structured_payload(report: dict) -> dict[str, Any] | None:
    """Build the allowlisted payload. Returns None if we cannot form a valid alert_type.

    Unknown keys on the report are ignored (dropped, not scrubbed). Free-text fields
    (symptom, diagnosis, evidence dumps, paths, seats names, body) are never copied.
    """
    if not isinstance(report, dict):
        return None
    raw_cls = report.get("class") or report.get("alert_type")
    alert_type = _clamp_enum(raw_cls, ALERT_TYPES)
    if alert_type is None:
        # Human-flagged reports often have no catalogue class.
        if (report.get("reported_by") or report.get("channel") or "") in ("user", "operator"):
            alert_type = "user_report"
        else:
            return None
    component = _COMPONENT_FOR_CLASS.get(alert_type, "unknown")
    created = _clamp_ts(report.get("created_at"))
    seats = report.get("seats") if isinstance(report.get("seats"), list) else []
    seat_count = _clamp_int(len(seats), lo=0, hi=10_000) or 0
    count = _clamp_int(report.get("count"), lo=1, hi=10_000) or 1
    payload = {
        "schema": SCHEMA,
        "alert_type": alert_type,
        "component": component,
        "error_code": alert_type,  # already enum-clamped
        "exit_code": _exit_code(report),
        "http_status": _http_status(report),
        "orchestraos_version": _orchestra_version(),
        "orchestraos_commit": _git_commit(),
        "os": _clamp_str(platform.system().lower()),
        "arch": _clamp_str(platform.machine().lower().replace(" ", "")),
        "count": count,
        "seat_count": seat_count,
        "created_at": created,
    }
    # Fingerprint from structured fields only (never symptom / seats names / logs).
    fp_src = "|".join(
        str(payload[k]) for k in ("alert_type", "component", "error_code", "exit_code", "http_status")
    )
    payload["fingerprint"] = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
    # Drop Nones so the body stays tight; still only allowlisted keys.
    return {k: v for k, v in payload.items() if k in ALLOWLIST_FIELDS and v is not None}


def format_issue(payload: dict[str, Any]) -> tuple[str, str]:
    """Title + body from an already-allowlisted payload. No other keys are rendered."""
    clean = {k: payload[k] for k in ALLOWLIST_FIELDS if k in payload and payload[k] is not None}
    title = f"RED ALERT {clean.get('alert_type', 'unknown')} / {clean.get('component', 'unknown')}"[:240]
    lines = [f"<!-- orchestraos-red-alert-fp:{clean.get('fingerprint', '')} -->", ""]
    for k in ALLOWLIST_FIELDS:
        if k in clean:
            lines.append(f"{k}: {clean[k]}")
    lines += ["", "_Structured auto-file from OrchestraOS. No free text, logs, or user data._"]
    body = "\n".join(lines)[:MAX_BODY]
    return title, body


def _rate_path() -> Path:
    data = Path(os.environ.get("ORCHESTRA_DIR") or "")
    if not data.is_dir():
        data = ROOT / "state"
        # Prefer next to other red-alert state when ORCHESTRA_DIR is set but not yet created.
    base = data / "state" / "red-alert" if (data / "state").exists() or data.name != "state" else data / "red-alert"
    try:
        from red_alert import state_dir
        base = Path(state_dir())
    except Exception:
        base.mkdir(parents=True, exist_ok=True)
    return Path(base) / "auto-file-rate.json"


def _rate_ok(fp: str, now: float | None = None) -> bool:
    now = now or time.time()
    path = _rate_path()
    try:
        doc = json.loads(path.read_text()) if path.is_file() else {"events": []}
    except (OSError, json.JSONDecodeError):
        doc = {"events": []}
    events = [e for e in doc.get("events") or [] if now - float(e.get("t") or 0) < 86400]
    if len(events) >= RATE_GLOBAL_PER_DAY:
        return False
    recent_fp = [e for e in events if e.get("fp") == fp and now - float(e.get("t") or 0) < RATE_COMMENT_PER_FP_HOURS * 3600]
    if recent_fp:
        return False
    return True


def _rate_record(fp: str, now: float | None = None) -> None:
    now = now or time.time()
    path = _rate_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(path.read_text()) if path.is_file() else {"events": []}
    except (OSError, json.JSONDecodeError):
        doc = {"events": []}
    events = [e for e in doc.get("events") or [] if now - float(e.get("t") or 0) < 86400]
    events.append({"t": now, "fp": fp})
    path.write_text(json.dumps({"events": events}))


def maybe_auto_file(report: dict, *, dry_run: bool = False, file_fn=None) -> dict[str, Any]:
    """Build the structured payload and, if a token file is present, file or comment.

    Never raises out to the caller (watch/report paths stay local-first). Returns a
    result dict. file_fn is a test hook: (repo, title, body, payload) -> result.
    """
    out: dict[str, Any] = {"ok": False, "action": "skip"}
    try:
        payload = structured_payload(report)
        if not payload:
            out["reason"] = "no allowlisted alert_type"
            return out
        title, body = format_issue(payload)
        repo = load_pinned_repo()
        # Content cannot retarget: ignore any repo/url on the report.
        out.update({"payload": payload, "title": title, "body": body, "repo": repo})
        if not _rate_ok(payload["fingerprint"]):
            out["reason"] = "rate-limited"
            out["action"] = "rate_limited"
            return out
        token = read_reporter_token()
        # Dry-run always returns the structured payload and never touches the network,
        # even when no token file is present (verification / DISARM boxes).
        if dry_run:
            out.update({"ok": True, "action": "dry_run", "dry_run": True,
                        "would_need_token": not bool(token)})
            return out
        if not token and file_fn is None:
            out["reason"] = "no reporter token file"
            out["action"] = "no_token"
            return out
        if file_fn is not None:
            result = file_fn(repo, title, body, payload)
            _rate_record(payload["fingerprint"])
            out.update({"ok": True, "action": result.get("action", "create")})
            out.update(result)
            return out
        from red_alert_issue import _github_api, find_open_issue
        existing = find_open_issue(repo, payload["fingerprint"])
        owner, name = repo.split("/", 1)
        if existing:
            _, data = _github_api(
                "POST",
                f"/repos/{owner}/{name}/issues/{existing['number']}/comments",
                token,
                {"body": body},
            )
            _rate_record(payload["fingerprint"])
            out.update({"ok": True, "action": "comment", "number": existing["number"],
                        "url": existing.get("url") or (data or {}).get("html_url")})
            return out
        _, data = _github_api(
            "POST", f"/repos/{owner}/{name}/issues", token,
            {"title": title, "body": body, "labels": [ISSUE_LABEL]},
        )
        data = data or {}
        _rate_record(payload["fingerprint"])
        out.update({"ok": True, "action": "create", "number": data.get("number"), "url": data.get("html_url")})
        return out
    except Exception as e:  # noqa: BLE001 — local capture already happened; never break the watch
        out["reason"] = f"auto-file failed: {type(e).__name__}"
        return out


def cmd_payload(rid: str) -> int:
    import red_alert as RA
    try:
        _, doc = RA._load(rid)
    except KeyError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    payload = structured_payload(doc)
    if not payload:
        print(json.dumps({"ok": False, "error": "no allowlisted alert_type"}))
        return 1
    title, body = format_issue(payload)
    print(json.dumps({"ok": True, "repo": load_pinned_repo(), "payload": payload, "title": title, "body": body}, indent=2))
    return 0


def cmd_auto_file(rid: str, *, dry_run: bool) -> int:
    import red_alert as RA
    try:
        _, doc = RA._load(rid)
    except KeyError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    result = maybe_auto_file(doc, dry_run=dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") or result.get("action") in ("no_token", "rate_limited", "skip") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    p_pay = sub.add_parser("payload", help="print the structured auto-file payload (never files)")
    p_pay.add_argument("report_id")
    p_af = sub.add_parser("auto-file", help="file the structured summary (token file required unless --dry-run)")
    p_af.add_argument("report_id")
    p_af.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "payload":
        return cmd_payload(args.report_id)
    if args.cmd == "auto-file":
        return cmd_auto_file(args.report_id, dry_run=args.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
