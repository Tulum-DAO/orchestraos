"""`orchestra init` — idempotent first-run setup. Every step reports did/skipped."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .settings import DEFAULT_DATA_DIR, read_toml

DATA_SUBDIRS = ("state", "logs", "queue", "state/event-stream", "state/uploads",
                "state/arturo", "logs/arturo", "state/agent-handoffs")


@dataclass
class Step:
    step: str
    did: bool
    detail: str


def default_run(argv, cwd=None, env=None) -> int:
    return subprocess.run(list(argv), cwd=cwd, env=env, check=False).returncode


def _rewrite_data_dir(example_text: str, data_dir: Path) -> str:
    """Set [data] dir in a copy of the example; comments and everything else stay."""
    out, in_data = [], False
    for line in example_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_data = stripped == "[data]"
        if in_data and stripped.startswith("dir") and "=" in stripped:
            line = f'dir = "{data_dir}"'
        out.append(line)
    return "\n".join(out) + "\n"


RULED_APPROVAL_MIGRATIONS = ("m20260825_answer_attribution", "m20260825_human_task")

TASKS_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT,
  task_id         TEXT,
  parent_id       TEXT,
  type            TEXT NOT NULL,
  from_agent      TEXT NOT NULL,
  to_agent        TEXT NOT NULL,
  subject         TEXT,
  body            TEXT,
  priority        TEXT NOT NULL DEFAULT 'medium',
  source          TEXT DEFAULT 'system',
  status          TEXT NOT NULL DEFAULT 'pending',
  retry_count     INTEGER NOT NULL DEFAULT 0,
  max_retries     INTEGER NOT NULL DEFAULT 5,
  metadata        TEXT,
  depends_on      TEXT,
  gather_mode     TEXT DEFAULT 'gather_all',
  tenant_id       TEXT DEFAULT '{operator}',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  attempted_at    TEXT,
  delivered_at    TEXT,
  acknowledged_at TEXT,
  archived_at     TEXT,
  error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(to_agent, status, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type, status);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS conversations (
  id              TEXT PRIMARY KEY,
  subject         TEXT,
  participants    TEXT,
  task_id         TEXT,
  mode            TEXT DEFAULT 'fire_and_forget',
  max_iterations  INTEGER DEFAULT 1,
  iteration_count INTEGER DEFAULT 0,
  completion_condition TEXT,
  status          TEXT DEFAULT 'open',
  tenant_id       TEXT DEFAULT '{operator}',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _has_table(db: Path, name: str) -> bool:
    import sqlite3
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def _has_messages_table(db: Path) -> bool:
    return _has_table(db, "messages")


def _seed_tasks_db(db: Path, operator: str) -> None:
    import sqlite3
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(TASKS_DB_SCHEMA.format(operator=operator.replace("'", "''")))
        conn.commit()
    finally:
        conn.close()


DEMO_SEATS = {
    # Generic fixture seats (B5): visible in the dashboard on first open, never spawned.
    "demo-planner":  {"name": "demo-planner",  "tier": "T1", "runtime": "claude", "machine": "vps",
                      "description": "Fixture: plans work and files decisions"},
    "demo-builder":  {"name": "demo-builder",  "tier": "T2", "runtime": "claude", "machine": "vps",
                      "description": "Fixture: builds and asks for approvals"},
    "demo-reviewer": {"name": "demo-reviewer", "tier": "T2", "runtime": "claude", "machine": "vps",
                      "description": "Fixture: reviews and raises human tasks"},
}


def seed_demo_registry(registry_path: Path) -> list:
    """Merge the three fixture seats into the data-dir registry; returns the ids added."""
    reg = {"agents": {}}
    if registry_path.exists():
        try:
            reg = json.loads(registry_path.read_text()) or {"agents": {}}
        except ValueError:
            reg = {"agents": {}}
    agents = reg.setdefault("agents", {})
    added = []
    for aid, fields in DEMO_SEATS.items():
        if aid in agents:
            continue
        agents[aid] = dict(fields, tmux_session=aid, status="offline", always_on=False, demo=True,
                           cwd=str(registry_path.parent))
        added.append(aid)
    if added:
        registry_path.write_text(json.dumps(reg, indent=2) + "\n")
    return added


def run_init(repo_root: Path, data_dir: Optional[Path] = None, *, run: Callable = default_run,
             skip_npm: bool = False, skip_venv: bool = False, skip_build: bool = False,
             config_path: Optional[Path] = None, demo: bool = False) -> list:
    repo_root = Path(repo_root)
    config_path = Path(config_path or os.environ.get("ORCHESTRA_CONFIG") or repo_root / "orchestra.toml")
    report: list[Step] = []

    # 1. resolve the data dir: flag > existing config > default
    if data_dir is None and config_path.exists():
        try:
            data_dir = Path(os.path.expanduser(str((read_toml(config_path).get("data") or {}).get("dir", "")))) or None
        except Exception:  # noqa: BLE001
            data_dir = None
        if data_dir is not None and str(data_dir) in ("", "."):
            data_dir = None
    data_dir = Path(os.path.expanduser(str(data_dir or DEFAULT_DATA_DIR))).resolve()

    # 2. config file (never overwritten)
    if config_path.exists():
        report.append(Step("config", False, f"kept existing {config_path}"))
    else:
        example = repo_root / "orchestra.example.toml"
        text = _rewrite_data_dir(example.read_text(), data_dir) if example.exists() \
            else f'[data]\ndir = "{data_dir}"\n'
        config_path.write_text(text)
        report.append(Step("config", True, f"wrote {config_path} from orchestra.example.toml (data.dir={data_dir})"))

    # 3. data dir + subdirs
    made = []
    for sub in ("",) + DATA_SUBDIRS:
        p = data_dir / sub if sub else data_dir
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(sub or ".")
    report.append(Step("data-dir", bool(made), f"{data_dir} ({'created ' + ', '.join(made) if made else 'present'})"))

    # 4. seed stores
    for rel, payload in (("registry.json", {"agents": {}}), ("state/agent-sessions.json", {})):
        p = data_dir / rel
        if p.exists():
            report.append(Step(f"seed:{rel}", False, "present"))
        else:
            p.write_text(json.dumps(payload, indent=2) + "\n")
            report.append(Step(f"seed:{rel}", True, f"wrote {p}"))

    # 5. gateway bearer token
    tok = data_dir / "state" / "watch-gateway-token"
    if tok.exists() and tok.read_text().strip():
        report.append(Step("gateway-token", False, "present"))
    else:
        tok.write_text(secrets.token_urlsafe(32))
        os.chmod(tok, 0o600)
        report.append(Step("gateway-token", True, f"wrote {tok} (chmod 600)"))

    # 5b. tasks.db schema — msg_store.py, scripts/message-router.py and the rotation beat
    # open <data>/state/tasks.db expecting messages + conversations; only the api created
    # them (first boot), so beats starting at t=0 under `orchestra up` crashed with
    # "no such table: messages". Same columns as api/src/lib/db.ts (CREATE IF NOT EXISTS
    # there keeps the two in lockstep); the api adds its own tables on top.
    db = data_dir / "state" / "tasks.db"
    if db.exists() and _has_messages_table(db):
        report.append(Step("seed:state/tasks.db", False, "present (messages table exists)"))
    else:
        operator = "operator"
        try:
            operator = str((read_toml(config_path).get("operator") or {}).get("id") or "operator")
        except Exception:  # noqa: BLE001 — config unreadable => default tenant
            pass
        _seed_tasks_db(db, operator)
        report.append(Step("seed:state/tasks.db", True, f"wrote {db} (messages + conversations)"))

    # 5c. approval + questionnaire schema — scripts/approval_resume.py (a supervisor beat) and
    # questionnaire_resume read approval_requests / questionnaires; only the first
    # `approval.py request` created them, so the beat crashed every tick until a card existed.
    schema_mod = repo_root / "scripts" / "approval_schema.py"
    if not schema_mod.exists():
        report.append(Step("seed:approval-schema", False, "skipped (no scripts/approval_schema.py)"))
    elif _has_table(db, "approval_requests") and _has_table(db, "questionnaires"):
        report.append(Step("seed:approval-schema", False, "present"))
    else:
        # Landed contracts that approval_schema keeps behind the operator DDL gate; on a fresh
        # data dir they are armed explicitly (never 'all' — a future batch stays gated until
        # ruled). Unarmed, approval_resume logged 'no such column: snoozed_until' every beat.
        seed_env = dict(os.environ, ORCHESTRA_DIR=str(data_dir),
                        APPROVAL_DDL_ARMED=",".join(RULED_APPROVAL_MIGRATIONS),
                        PYTHONPATH=os.pathsep.join([str(repo_root / "scripts"), str(repo_root),
                                                    os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
        rc = run([sys.executable or "python3", "-c",
                  "from approval_schema import ApprovalStore; ApprovalStore().migrate(); "
                  "from questionnaire_schema import QuestionnaireStore; QuestionnaireStore().migrate()"],
                 cwd=repo_root, env=seed_env)
        report.append(Step("seed:approval-schema", rc == 0,
                           "approval_requests + questionnaires tables ensured" if rc == 0 else f"schema seed failed rc={rc}"))

    # 5d. --demo: fixture seats + one card of each kind so the dashboard is not empty.
    if demo:
        added = seed_demo_registry(data_dir / "registry.json")
        report.append(Step("demo:registry", bool(added),
                           ("added " + ", ".join(added)) if added else "fixture seats present"))
        seeder = repo_root / "scripts" / "demo_seed_cards.py"
        if not seeder.exists():
            report.append(Step("demo:cards", False, "skipped (no scripts/demo_seed_cards.py)"))
        else:
            demo_env = dict(os.environ, ORCHESTRA_DIR=str(data_dir),
                            APPROVAL_DDL_ARMED=",".join(RULED_APPROVAL_MIGRATIONS),
                            PYTHONPATH=os.pathsep.join([str(repo_root / "scripts"), str(repo_root),
                                                        os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
            rc = run([sys.executable or "python3", str(seeder)], cwd=repo_root, env=demo_env)
            report.append(Step("demo:cards", rc == 0,
                               "approval + menu + questionnaire + human task seeded (idempotent)" if rc == 0
                               else f"demo_seed_cards.py failed rc={rc}"))

    # 6. python venv + requirements
    venv = repo_root / ".venv"
    req = repo_root / "requirements.txt"
    if skip_venv:
        report.append(Step("venv", False, "skipped (--no-venv)"))
        report.append(Step("pip", False, "skipped (--no-venv)"))
    else:
        if (venv / "bin" / "python").exists():
            report.append(Step("venv", False, f"present {venv}"))
        else:
            rc = run([sys.executable or "python3", "-m", "venv", str(venv)], cwd=repo_root)
            report.append(Step("venv", rc == 0, f"created {venv}" if rc == 0 else f"python -m venv failed rc={rc}"))
        stamp = venv / ".requirements.sha"
        if not req.exists():
            report.append(Step("pip", False, "no requirements.txt"))
        else:
            import hashlib
            digest = hashlib.sha256(req.read_bytes()).hexdigest()
            if stamp.exists() and stamp.read_text().strip() == digest:
                report.append(Step("pip", False, "requirements unchanged"))
            else:
                pip = venv / "bin" / "python"
                rc = run([str(pip), "-m", "pip", "install", "-q", "-r", str(req)], cwd=repo_root)
                if rc == 0:
                    stamp.write_text(digest)
                report.append(Step("pip", rc == 0, "installed requirements.txt" if rc == 0 else f"pip failed rc={rc}"))

    # 7. npm installs (root = dashboard-proxy deps, api, dashboard)
    for label, sub in (("root", ""), ("api", "api"), ("dashboard", "dashboard")):
        d = repo_root / sub if sub else repo_root
        if not (d / "package.json").exists():
            report.append(Step(f"npm:{label}", False, "no package.json"))
            continue
        if skip_npm:
            report.append(Step(f"npm:{label}", False, "skipped (--no-npm)"))
            continue
        if (d / "node_modules").exists():
            report.append(Step(f"npm:{label}", False, "node_modules present"))
            continue
        rc = run(["npm", "install", "--no-audit", "--no-fund"], cwd=d)
        report.append(Step(f"npm:{label}", rc == 0, "npm install" if rc == 0 else f"npm install failed rc={rc}"))

    # 8. builds (api tsc -> dist/server.js, dashboard vite -> dist/index.html)
    for label, sub, artifact in (("api", "api", "dist/server.js"), ("dashboard", "dashboard", "dist/index.html")):
        d = repo_root / sub
        if not (d / "package.json").exists():
            report.append(Step(f"build:{label}", False, "no package.json"))
            continue
        if skip_build or skip_npm:
            report.append(Step(f"build:{label}", False, "skipped"))
            continue
        if (d / artifact).exists():
            report.append(Step(f"build:{label}", False, f"{artifact} present (delete it to rebuild)"))
            continue
        rc = run(["npm", "run", "build"], cwd=d)
        report.append(Step(f"build:{label}", rc == 0, "built" if rc == 0 else f"npm run build failed rc={rc}"))

    # 9. Claude Code hooks -> the user's settings.json (merge, never clobber; idempotent).
    # Without them a seat only acts on mail when someone presses Enter: the idle-inbox
    # drain (Stop), the pane-state truth the router's idle oracle reads, the rotation
    # self-trigger and the lineage bus feeder all ride these hooks. Skipped with
    # ORCHESTRA_SKIP_HOOKS=1 (containers that run no Claude seats).
    if os.environ.get("ORCHESTRA_SKIP_HOOKS"):
        report.append(Step("hooks", False, "skipped (ORCHESTRA_SKIP_HOOKS)"))
    else:
        try:
            sys.path.insert(0, str(repo_root / "hooks"))
            import install as _hooks  # noqa: WPS433
            settings_path = Path(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))) / "settings.json"
            rep = _hooks.install(settings_path=settings_path, repo_root=repo_root, data_dir=data_dir)
            if rep.get("error"):
                report.append(Step("hooks", False, rep["error"]))
            else:
                report.append(Step("hooks", True, f"{rep['installed']} hook rows -> {settings_path} (replaced {rep['removed']} previous)"))
        except Exception as e:  # noqa: BLE001
            report.append(Step("hooks", False, f"hook install failed: {e}"))

    return report


def render_report(report: list) -> str:
    w = max(len(r.step) for r in report) if report else 10
    lines = [f"{r.step.ljust(w)}  {'did' if r.did else 'skipped':7}  {r.detail}" for r in report]
    return "\n".join(lines)
