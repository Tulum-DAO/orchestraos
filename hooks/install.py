#!/usr/bin/env python3
"""install.py — put the shipped Claude Code hooks into the user's ~/.claude/settings.json.

`orchestra init` calls install(); `orchestra doctor` calls status(). Rules:
  * MERGE, never clobber: the user's own hooks, model, permissions, everything else stays.
  * Idempotent: our rows are tagged with MARKER; a re-install removes our old rows (any
    data dir / any repo path) and writes the current set once. Nothing else is touched.
  * A settings file that does not parse is left alone (report["error"]), never overwritten.
  * The data dir is baked into each command as ORCHESTRA_DIR=<data> so a hook fired from a
    seat whose cwd is a client repo still finds registry.json / state/tasks.db.

Gemini CLI and Codex have no hook layer here yet (docs/HOOKS.md).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "#orchestraos-hook"
PY = shutil.which("python3") or sys.executable or "python3"
NODE = "node"

# (event, matcher, script relative to repo, runner)
HOOKS = [
    ("SessionStart", "", "hooks/state-event-hook.py", "py"),
    ("UserPromptSubmit", "", "hooks/state-event-hook.py", "py"),
    ("PreToolUse", "", "hooks/state-event-hook.py", "py"),
    ("PostToolUse", "", "hooks/state-event-hook.py", "py"),
    ("PostToolUseFailure", "", "hooks/state-event-hook.py", "py"),   # closes the open call too
    ("PermissionDenied", "", "hooks/state-event-hook.py", "py"),
    ("Notification", "", "hooks/state-event-hook.py", "py"),
    ("Stop", "", "hooks/state-event-hook.py", "py"),
    ("SessionEnd", "", "hooks/state-event-hook.py", "py"),
    ("PostToolUse", "", "hooks/rotation-self-trigger.js", "node"),
    ("Stop", "", "scripts/lineage_daemon/bus_feeder.py", "py"),
    ("SessionEnd", "", "scripts/lineage_daemon/bus_feeder.py", "py"),
    ("Notification", "", "scripts/lineage_daemon/bus_feeder.py", "py"),
    ("Stop", "", "hooks/agent-queue-drain.py", "py"),   # LAST on Stop: the digest may block
]


def _command(repo_root: Path, data_dir: Path, rel: str, runner: str) -> str:
    """Fail OPEN at runtime: if the script is gone (checkout moved/deleted) the hook exits 0 and
    Claude proceeds; a present script runs normally and its verdict reaches Claude unchanged.
    (A missing script under a bare interpreter call blocked every tool on a host, 2026-09-17.)"""
    script = str(Path(repo_root) / rel)
    run = f'"{NODE}" "{script}"' if runner == "node" else f'"{PY}" "{script}"'
    return (f'[ -f "{script}" ] && ORCHESTRA_DIR="{data_dir}" ORCHESTRA_ROOT="{repo_root}" {run} '
            f'|| exit 0 {MARKER}')


def _load(settings_path: Path):
    if not settings_path.exists():
        return {}
    text = settings_path.read_text()
    if not text.strip():
        return {}
    return json.loads(text)          # raises on a broken file: the caller refuses to clobber


def _is_ours(cmd: str) -> bool:
    return isinstance(cmd, str) and MARKER in cmd


# Set to 1 to install a checkout that lives under a temp dir into the real settings file on purpose.
ALLOW_TEMP_ENV = "ORCHESTRA_ALLOW_TEMP_INSTALL"


def _real_settings() -> Path:
    return Path(os.path.expanduser("~/.claude/settings.json")).resolve()


def _temp_roots() -> list:
    roots = {Path(tempfile.gettempdir()).resolve()}
    for d in ("/tmp", "/var/tmp", "/private/tmp"):
        if os.path.isdir(d):
            roots.add(Path(d).resolve())
    return sorted(roots)


def _under_temp(p: Path):
    """The temp root `p` lives under, or None."""
    for root in _temp_roots():
        if p == root or root in p.parents:
            return root
    return None


def _strip_ours(hooks: dict) -> int:
    """Drop every row tagged MARKER (any data dir, any repo path); keep every user rule intact."""
    removed = 0
    for ev, rules in list(hooks.items()):
        kept_rules = []
        for rule in rules or []:
            hk = [h for h in (rule.get("hooks") or []) if not _is_ours(h.get("command", ""))]
            removed += len(rule.get("hooks") or []) - len(hk)
            if hk:
                rule = dict(rule); rule["hooks"] = hk; kept_rules.append(rule)
        if kept_rules:
            hooks[ev] = kept_rules
        else:
            hooks.pop(ev, None)
    return removed


def _write(settings_path: Path, settings: dict) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, settings_path)


def install(*, settings_path: Path, repo_root: Path, data_dir: Path, dry_run: bool = False) -> dict:
    """dry_run=True computes the merge and reports it (rows, removed, kept user hooks) WITHOUT
    writing — `orchestra init` shows this plan to the operator before touching their file."""
    settings_path = Path(os.path.expanduser(str(settings_path)))
    repo_root = Path(repo_root).resolve()
    data_dir = Path(os.path.expanduser(str(data_dir))).resolve()
    try:
        settings = _load(settings_path)
    except ValueError as e:
        return {"installed": 0, "removed": 0, "error": f"{settings_path} is not valid JSON ({e}); left untouched"}
    if not isinstance(settings, dict):
        return {"installed": 0, "removed": 0, "error": f"{settings_path} top level is not an object; left untouched"}
    # GUARD 1: every shipped script must exist on disk at repo_root, else a hook row would
    # error on every tool call and block the whole host (2026-09-17 incident).
    missing = [rel for _ev, _m, rel, _r in HOOKS if not (repo_root / rel).is_file()]
    if missing:
        return {"installed": 0, "removed": 0,
                "error": f"refusing to install: hook scripts missing under {repo_root}: {', '.join(sorted(set(missing)))}"}
    # GUARD 2: under pytest never write the developer's real settings file.
    real = _real_settings()
    if os.environ.get("PYTEST_CURRENT_TEST") and settings_path.resolve() == real:
        return {"installed": 0, "removed": 0,
                "error": "refusing to write the real ~/.claude/settings.json from inside a test (set CLAUDE_CONFIG_DIR)"}
    # GUARD 3: a checkout (or data dir) under a temp dir is a scratch copy: a proof, a red-team clone, a
    # throwaway test. Installing it into the real settings file makes every Claude session on the host run
    # hooks out of a directory that is deleted later and that anyone may re-create with other code; and
    # step 1 below would replace a real install's rows with it. 2026-09-20: `orchestra init --yes` run from
    # a scratchpad clone, outside pytest, left 12 such rows in an operator's live file for 19 days.
    if settings_path.resolve() == real and os.environ.get(ALLOW_TEMP_ENV) != "1":
        scratch = next(((label, p) for label, p in (("checkout", repo_root), ("data dir", data_dir))
                        if _under_temp(p)), None)
        if scratch:
            return {"installed": 0, "removed": 0,
                    "error": f"refusing to install hooks from a {scratch[0]} under a temp dir ({scratch[1]}) into the "
                             f"real {real}: point CLAUDE_CONFIG_DIR at a scratch dir for a throwaway install, or set "
                             f"{ALLOW_TEMP_ENV}=1 if this really is the install every Claude session should run"}
    hooks = settings.setdefault("hooks", {})
    # 1. drop our previous rows (identified by MARKER only), keep every user rule intact
    removed = _strip_ours(hooks)
    # 2. append ours, one rule per (event, matcher), in HOOKS order
    installed = 0
    for ev, matcher, rel, runner in HOOKS:
        rules = hooks.setdefault(ev, [])
        target = next((r for r in rules if (r.get("matcher") or "") == matcher and any(_is_ours(h.get("command", "")) for h in r.get("hooks", []))), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []} if matcher else {"hooks": []}
            rules.append(target)
        target["hooks"].append({"type": "command", "command": _command(repo_root, data_dir, rel, runner)})
        installed += 1
    rows = [h["command"] for rules in hooks.values() for r in rules for h in r.get("hooks", []) if _is_ours(h.get("command", ""))]
    user_rows = sum(1 for rules in hooks.values() for r in rules for h in r.get("hooks", []) if not _is_ours(h.get("command", "")))
    rep = {"installed": installed, "removed": removed, "settings": str(settings_path), "data_dir": str(data_dir),
           "rows": rows, "user_rows_kept": user_rows, "existed": settings_path.exists()}
    if dry_run:
        return rep
    _write(settings_path, settings)
    return rep


def remove(*, settings_path: Path, dry_run: bool = False) -> dict:
    """Take every OrchestraOS row out (by MARKER, from any checkout or data dir, including a stray test or
    scratch install); nothing else in the file changes. The cleanup for an install that should not be there."""
    settings_path = Path(os.path.expanduser(str(settings_path)))
    try:
        settings = _load(settings_path)
    except ValueError as e:
        return {"removed": 0, "error": f"{settings_path} is not valid JSON ({e}); left untouched"}
    if not isinstance(settings, dict):
        return {"removed": 0, "error": f"{settings_path} top level is not an object; left untouched"}
    hooks = settings.get("hooks")
    removed = _strip_ours(hooks) if isinstance(hooks, dict) else 0
    rep = {"removed": removed, "settings": str(settings_path)}
    if removed and not dry_run:
        _write(settings_path, settings)
    return rep


def plan(*, settings_path: Path, repo_root: Path, data_dir: Path) -> dict:
    """What install() would write, without writing it."""
    return install(settings_path=settings_path, repo_root=repo_root, data_dir=data_dir, dry_run=True)


def render_plan(rep: dict) -> str:
    if rep.get("error"):
        return rep["error"]
    verb = "update" if rep["existed"] else "create"
    lines = [f"orchestra init will {verb} your Claude Code settings file:",
             f"  {rep['settings']}",
             f"  {len(rep['rows'])} hook rows tagged {MARKER} (replacing {rep['removed']} previous OrchestraOS rows;"
             f" {rep['user_rows_kept']} of your own hook rows kept; model/permissions untouched)",
             "  each row runs (fail-open, exit 0 if the script is gone):"]
    lines += [f"    {c}" for c in rep["rows"]]
    return "\n".join(lines)


def status(*, settings_path: Path, repo_root: Path) -> dict:
    settings_path = Path(os.path.expanduser(str(settings_path)))
    try:
        settings = _load(settings_path)
    except ValueError:
        return {"installed": [], "missing": [f"{ev}:{rel}" for ev, _, rel, _ in HOOKS], "error": "settings.json not valid JSON"}
    present = set()
    for ev, rules in (settings.get("hooks") or {}).items():
        for rule in rules or []:
            for h in rule.get("hooks") or []:
                cmd = h.get("command", "")
                if _is_ours(cmd):
                    for _ev, _m, rel, _r in HOOKS:
                        if _ev == ev and rel in cmd:
                            present.add(f"{ev}:{rel}")
    want = [f"{ev}:{rel}" for ev, _, rel, _ in HOOKS]
    return {"installed": [w for w in want if w in present], "missing": [w for w in want if w not in present]}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="install/inspect the OrchestraOS Claude Code hooks")
    ap.add_argument("--settings", default=os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "settings.json"))
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--data-dir", default=os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/orchestra"))
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--remove", action="store_true", help=f"remove every row tagged {MARKER}, nothing else")
    ns = ap.parse_args(argv)
    if ns.remove:
        rep = remove(settings_path=Path(ns.settings))
        print(json.dumps(rep, indent=2)); return 1 if rep.get("error") else 0
    if ns.status:
        print(json.dumps(status(settings_path=Path(ns.settings), repo_root=Path(ns.repo_root)), indent=2)); return 0
    rep = install(settings_path=Path(ns.settings), repo_root=Path(ns.repo_root), data_dir=Path(ns.data_dir))
    print(json.dumps(rep, indent=2))
    return 1 if rep.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
