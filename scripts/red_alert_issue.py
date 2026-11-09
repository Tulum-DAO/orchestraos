#!/usr/bin/env python3
"""red_alert_issue.py — human-only GitHub issue filing from a RED ALERT report.

Never auto-files. The dashboard (or CLI) shows a scrubbed preview; the operator
confirms; then we create an issue or comment on an open one with the same
fingerprint. Target repo comes from orchestra.toml [github_issues] repo
(owner/name). Default is unset — feature visible, filing disabled until configured.

    python3 scripts/red_alert_issue.py preview <report-id>
    python3 scripts/red_alert_issue.py create  <report-id> [--yes]
    python3 scripts/red_alert_issue.py scrub   --text-file -

Auth: `gh` on PATH (box login) or GITHUB_TOKEN / GH_TOKEN. Clear error if neither.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import red_alert as RA  # noqa: E402

# Patterns scrubbed before anything leaves the box. Order matters for nested matches.
_SCRUBBERS: list[tuple[re.Pattern[str], str]] = [
    # Tailscale / private IPs
    (re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<tailscale-ip>"),
    (re.compile(r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<private-ip>"),
    # Absolute home paths
    (re.compile(r"(?i)/(?:Users|home)/[A-Za-z0-9._-]+"), "<home>"),
    (re.compile(r"(?i)\b(?:Users|home)/[A-Za-z0-9._-]+"), "<home>"),
    # Chat / Telegram / Slack-ish ids
    (re.compile(r"(?i)\b(?:chat|channel|thread)[_-]?(?:id)?[=:\s]+\-?\d{6,}\b"), "<chat-id>"),
    (re.compile(r"(?i)\b(?:tg|telegram)[-_]?id[=:\s]+\-?\d+\b"), "<chat-id>"),
    (re.compile(r"\b-\d{9,}\b"), "<chat-id>"),  # telegram group ids often -100...
    # Secrets-ish
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,})\b"), "<secret>"),
    # Tailnet / MagicDNS hostnames (*.ts.net and common fleet host labels)
    (re.compile(r"\b[a-z0-9-]+\.ts\.net\b", re.I), "<tailnet-host>"),
    (re.compile(r"\b(?:orchestra-vps|shoreline-office|srv\d{5,})\b", re.I), "<host>"),
]

# Optional person/client names from env ORCHESTRA_SCRUB_NAMES=comma,separated
def _name_scrubbers() -> list[tuple[re.Pattern[str], str]]:
    raw = os.environ.get("ORCHESTRA_SCRUB_NAMES", "")
    out = []
    for name in [n.strip() for n in raw.split(",") if n.strip()]:
        out.append((re.compile(re.escape(name), re.I), "<name>"))
    return out


def scrub(text: str) -> str:
    if not text:
        return text
    out = text
    for rx, repl in _SCRUBBERS + _name_scrubbers():
        out = rx.sub(repl, out)
    return out


def fingerprint(report: dict) -> str:
    """Stable id for dedupe: seat + class + symptom shape (scrubbed)."""
    seats = ",".join(report.get("seats") or [])
    cls = report.get("class") or report.get("severity") or ""
    symptom = scrub(str(report.get("symptom") or ""))[:200]
    raw = f"{seats}|{cls}|{symptom}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_repo_setting() -> str | None:
    """owner/name from orchestra.toml [github_issues] repo, else env, else None."""
    env = (os.environ.get("ORCHESTRA_GITHUB_ISSUES_REPO") or "").strip()
    if env:
        return env
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    cfg = Path(os.environ.get("ORCHESTRA_CONFIG") or (ROOT / "orchestra.toml"))
    if not cfg.is_file():
        return None
    try:
        raw = tomllib.loads(cfg.read_text())
    except Exception:  # noqa: BLE001
        return None
    repo = ((raw.get("github_issues") or {}).get("repo") or "").strip()
    return repo or None


def _gh_auth_ok() -> tuple[bool, str]:
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        return True, "token"
    gh = shutil.which("gh")
    if not gh:
        return False, "neither `gh` nor GITHUB_TOKEN/GH_TOKEN is available"
    try:
        r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "gh"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"gh auth check failed: {e}"
    return False, "gh is installed but not logged in (run: gh auth login), and no GITHUB_TOKEN/GH_TOKEN"


def build_preview(report: dict) -> dict[str, Any]:
    rid = report.get("id") or "?"
    seats = ", ".join(report.get("seats") or []) or "(none)"
    cls = report.get("class") or report.get("severity") or "unknown"
    symptom = scrub(str(report.get("symptom") or ""))
    diagnosis = scrub(str(report.get("diagnosis") or ""))
    fp = fingerprint(report)
    title = scrub(f"RED ALERT {cls}: {seats} — {symptom[:80]}")
    body_parts = [
        f"<!-- orchestraos-red-alert-fp:{fp} -->",
        f"**Report id:** `{rid}`",
        f"**Class:** `{cls}`",
        f"**Seats:** {seats}",
        f"**Status:** {report.get('status')}",
        "",
        "## Symptom",
        symptom or "(empty)",
    ]
    if diagnosis:
        body_parts += ["", "## Diagnosis", diagnosis]
    # Evidence: only scrubbed keys, drop pane dumps / paths
    ev = report.get("evidence") or {}
    if isinstance(ev, dict):
        safe = {}
        for k, v in ev.items():
            if k in ("pane_snapshot", "screen", "log_excerpt", "registry_rows"):
                continue
            safe[k] = scrub(json.dumps(v, default=str)[:500]) if not isinstance(v, (str, int, float, bool, type(None))) else scrub(str(v)[:500])
        if safe:
            body_parts += ["", "## Evidence (scrubbed)", "```json", scrub(json.dumps(safe, indent=2)[:3000]), "```"]
    body_parts += [
        "",
        "---",
        f"_Fingerprint `{fp}` — open issues with this marker get a comment instead of a new issue._",
        "_Filed manually from an OrchestraOS red-alert card. Never auto-filed._",
    ]
    repo = load_repo_setting()
    auth_ok, auth_how = _gh_auth_ok()
    return {
        "ok": True,
        "report_id": rid,
        "fingerprint": fp,
        "repo": repo,
        "repo_configured": bool(repo),
        "auth_ok": auth_ok,
        "auth": auth_how,
        "title": title[:240],
        "body": "\n".join(body_parts),
        "can_file": bool(repo) and auth_ok,
        "blocked_reason": (
            None if (repo and auth_ok)
            else ("set orchestra.toml [github_issues] repo = \"owner/name\" (default unset)" if not repo
                  else auth_how)
        ),
    }


def find_open_issue(repo: str, fp: str) -> dict | None:
    """Return {number, url, title} if an open issue carries our fingerprint marker."""
    gh = shutil.which("gh")
    if not gh:
        return None
    marker = f"orchestraos-red-alert-fp:{fp}"
    try:
        r = subprocess.run(
            [gh, "issue", "list", "--repo", repo, "--state", "open",
             "--search", marker, "--json", "number,url,title,body", "--limit", "20"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for it in items:
        if marker in (it.get("body") or ""):
            return {"number": it["number"], "url": it["url"], "title": it.get("title")}
    return None


def create_or_comment(preview: dict, *, dry_run: bool = False) -> dict:
    if not preview.get("repo_configured"):
        return {"ok": False, "error": preview.get("blocked_reason") or "repo not configured"}
    if not preview.get("auth_ok"):
        return {"ok": False, "error": preview.get("auth") or "no github auth"}
    repo = preview["repo"]
    fp = preview["fingerprint"]
    existing = find_open_issue(repo, fp)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": "comment" if existing else "create",
            "existing": existing,
            "title": preview["title"],
            "body": preview["body"],
            "repo": repo,
        }
    gh = shutil.which("gh")
    if not gh:
        # token-only path: use API via curl-ish — require gh for now
        return {"ok": False, "error": "`gh` CLI required to file (token alone not wired yet)"}
    if existing:
        body = scrub(
            f"### Update from red-alert `{preview['report_id']}`\n\n"
            f"{preview['body']}"
        )
        r = subprocess.run(
            [gh, "issue", "comment", str(existing["number"]), "--repo", repo, "--body", body],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
        return {"ok": True, "action": "comment", "number": existing["number"], "url": existing["url"]}
    r = subprocess.run(
        [gh, "issue", "create", "--repo", repo, "--title", preview["title"], "--body", preview["body"]],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
    url = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    return {"ok": True, "action": "create", "url": url}


def cmd_preview(rid: str) -> int:
    try:
        _, doc = RA._load(rid)
    except KeyError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps(build_preview(doc), indent=2))
    return 0


def cmd_create(rid: str, *, yes: bool, dry_run: bool) -> int:
    try:
        _, doc = RA._load(rid)
    except KeyError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    prev = build_preview(doc)
    if not yes and not dry_run:
        print(json.dumps({"ok": False, "error": "refusing without --yes (use preview first, then create --yes)"}))
        return 2
    result = create_or_comment(prev, dry_run=dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_scrub() -> int:
    text = sys.stdin.read()
    print(scrub(text), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    p_prev = sub.add_parser("preview")
    p_prev.add_argument("report_id")
    p_cr = sub.add_parser("create")
    p_cr.add_argument("report_id")
    p_cr.add_argument("--yes", action="store_true")
    p_cr.add_argument("--dry-run", action="store_true")
    sub.add_parser("scrub")
    args = p.parse_args(argv)
    if args.cmd == "preview":
        return cmd_preview(args.report_id)
    if args.cmd == "create":
        return cmd_create(args.report_id, yes=args.yes, dry_run=args.dry_run)
    if args.cmd == "scrub":
        return cmd_scrub()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
