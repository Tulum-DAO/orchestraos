#!/usr/bin/env python3
"""red_alert_issue.py — human-only GitHub issue filing from a RED ALERT report.

Never auto-files. The dashboard (or CLI) shows a scrubbed preview; the operator
confirms; then we create an issue or comment on an open one with the same
fingerprint, or (with no GitHub auth) return a prefilled github.com issue URL.

Target repo: orchestra.toml [github_issues] repo, else ORCHESTRA_GITHUB_ISSUES_REPO,
else Tulum-DAO/orchestraos (public default, overridable).

    python3 scripts/red_alert_issue.py preview <report-id>
    python3 scripts/red_alert_issue.py create  <report-id> [--yes]
    python3 scripts/red_alert_issue.py scrub   --text-file -

Auth: `gh` on PATH or GITHUB_TOKEN / GH_TOKEN files via the API. With neither, a prefilled /issues/new URL is returned so no token is required.
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
from urllib.parse import urlencode

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


DEFAULT_REPO = "Tulum-DAO/orchestraos"
ISSUE_LABEL = "red-alert"


def load_repo_setting() -> str:
    """owner/name: env, then orchestra.toml [github_issues] repo, else public default."""
    env = (os.environ.get("ORCHESTRA_GITHUB_ISSUES_REPO") or "").strip()
    if env:
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
            if repo:
                return repo
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_REPO


def new_issue_url(repo: str, title: str, body: str, labels: str = ISSUE_LABEL) -> str:
    """Prefilled github.com /issues/new URL (no token required). Title/body must already be scrubbed."""
    q = urlencode({"title": title, "body": body, "labels": labels})
    return f"https://github.com/{repo}/issues/new?{q}"


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
    title = title[:240]
    body = "\n".join(body_parts)
    url = new_issue_url(repo, title, body)
    return {
        "ok": True,
        "report_id": rid,
        "fingerprint": fp,
        "repo": repo,
        "repo_configured": True,
        "auth_ok": auth_ok,
        "auth": auth_how,
        "title": title,
        "body": body,
        "new_issue_url": url,
        "can_file": auth_ok,
        "can_open_url": True,
        "blocked_reason": None if auth_ok else (
            "no GitHub auth — open the prefilled issue URL (nothing is filed automatically)"
        ),
    }


def _github_token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _github_api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, Any]:
    """Minimal GitHub REST call. Never logs the token. path may include a query string."""
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "orchestraos-red-alert",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return int(resp.status), (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        err = (e.read() or b"").decode(errors="replace")[-400:]
        raise RuntimeError(f"GitHub API {e.code}: {err}") from e


def find_open_issue(repo: str, fp: str) -> dict | None:
    """Return {number, url, title} if an open issue carries our fingerprint marker."""
    marker = f"orchestraos-red-alert-fp:{fp}"
    gh = shutil.which("gh")
    if gh:
        try:
            r = subprocess.run(
                [gh, "issue", "list", "--repo", repo, "--state", "open",
                 "--search", marker, "--json", "number,url,title,body", "--limit", "20"],
                capture_output=True, text=True, timeout=60,
                env={**os.environ},
            )
        except (OSError, subprocess.SubprocessError):
            r = None
        else:
            if r.returncode == 0:
                try:
                    items = json.loads(r.stdout or "[]")
                except json.JSONDecodeError:
                    items = []
                for it in items:
                    if marker in (it.get("body") or ""):
                        return {"number": it["number"], "url": it["url"], "title": it.get("title")}
    token = _github_token()
    if not token:
        return None
    q = urlencode({"q": f'repo:{repo} is:issue is:open "{marker}"'})
    try:
        _, data = _github_api("GET", f"/search/issues?{q}", token)
    except (OSError, RuntimeError):
        return None
    for it in (data or {}).get("items") or []:
        if marker in (it.get("body") or ""):
            return {"number": it["number"], "url": it.get("html_url") or it.get("url"), "title": it.get("title")}
    return None


def create_or_comment(preview: dict, *, dry_run: bool = False) -> dict:
    repo = preview.get("repo") or DEFAULT_REPO
    url = preview.get("new_issue_url") or new_issue_url(repo, preview.get("title") or "", preview.get("body") or "")
    # No auth: never file; return the prefilled URL so a human can open it.
    if not preview.get("auth_ok"):
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "action": "open_url",
            "url": url,
            "repo": repo,
            "title": preview.get("title"),
            "body": preview.get("body"),
            "auth": preview.get("auth"),
        }
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
            "new_issue_url": url,
        }
    comment_body = scrub(
        f"### Update from red-alert `{preview['report_id']}`\n\n"
        f"{preview['body']}"
    )
    gh = shutil.which("gh")
    if gh:
        if existing:
            r = subprocess.run(
                [gh, "issue", "comment", str(existing["number"]), "--repo", repo, "--body", comment_body],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
            return {"ok": True, "action": "comment", "number": existing["number"], "url": existing["url"]}
        r = subprocess.run(
            [gh, "issue", "create", "--repo", repo, "--title", preview["title"],
             "--body", preview["body"], "--label", ISSUE_LABEL],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or r.stdout)[-400:]}
        created = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        return {"ok": True, "action": "create", "url": created}

    token = _github_token()
    if not token:
        return {"ok": False, "error": "neither `gh` nor GITHUB_TOKEN/GH_TOKEN is available"}
    try:
        owner, name = repo.split("/", 1)
    except ValueError:
        return {"ok": False, "error": f"invalid repo {repo!r} (expected owner/name)"}
    try:
        if existing:
            _, data = _github_api(
                "POST", f"/repos/{owner}/{name}/issues/{existing['number']}/comments",
                token, {"body": comment_body},
            )
            return {
                "ok": True, "action": "comment",
                "number": existing["number"],
                "url": existing.get("url") or (data or {}).get("html_url"),
            }
        _, data = _github_api(
            "POST", f"/repos/{owner}/{name}/issues", token,
            {"title": preview["title"], "body": preview["body"], "labels": [ISSUE_LABEL]},
        )
        data = data or {}
        return {"ok": True, "action": "create", "number": data.get("number"), "url": data.get("html_url")}
    except (OSError, RuntimeError) as e:
        return {"ok": False, "error": str(e)[:400]}


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
