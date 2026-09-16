"""RED-first: `orchestra init` is idempotent and hermetic (fake subprocess runner)."""
import json
import os
import stat
from pathlib import Path

from orchestra_cli import init_cmd as I


def _repo(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "orchestra.example.toml").write_text(
        '# example\n[data]\ndir = "/var/lib/orchestraos"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    (root / "requirements.txt").write_text("aiohttp\n")
    (root / "api").mkdir()
    (root / "api" / "package.json").write_text("{}")
    (root / "dashboard").mkdir()
    (root / "dashboard" / "package.json").write_text("{}")
    (root / "package.json").write_text("{}")
    return root


class Runner:
    """Records commands; simulates their side effects so idempotence is testable."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd=None, env=None):
        self.calls.append((tuple(argv), str(cwd)))
        argv = list(argv)
        cwd = Path(cwd)
        if argv[-2:] == ["-m", "venv"] or "venv" in argv:
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("")
        elif argv[:2] == ["npm", "install"] or argv[:2] == ["npm", "ci"]:
            (cwd / "node_modules").mkdir(exist_ok=True)
        elif argv[:3] == ["npm", "run", "build"]:
            if cwd.name == "api":
                (cwd / "dist").mkdir(exist_ok=True)
                (cwd / "dist" / "server.js").write_text("")
            else:
                (cwd / "dist").mkdir(exist_ok=True)
                (cwd / "dist" / "index.html").write_text("")
        return 0


def test_init_creates_everything_and_reports(tmp_path):
    root = _repo(tmp_path)
    data = tmp_path / "data"
    runner = Runner()
    report = I.run_init(root, data_dir=data, run=runner)
    done = {r.step: r for r in report}
    assert done["config"].did is True
    cfg = (root / "orchestra.toml").read_text()
    assert f'dir = "{data}"' in cfg and "/var/lib/orchestraos" not in cfg
    assert "# example" in cfg  # comments preserved
    for sub in ("state", "logs", "queue", "state/event-stream", "state/uploads"):
        assert (data / sub).is_dir()
    assert json.loads((data / "registry.json").read_text()) == {"agents": {}}
    assert json.loads((data / "state" / "agent-sessions.json").read_text()) == {}
    tok = data / "state" / "watch-gateway-token"
    assert len(tok.read_text().strip()) >= 32
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600
    assert done["venv"].did and done["pip"].did
    assert done["npm:api"].did and done["npm:dashboard"].did and done["npm:root"].did
    assert done["build:api"].did and done["build:dashboard"].did
    cmds = [c[0] for c in runner.calls]
    assert any("venv" in c for c in cmds)
    assert any(c[:2] == ("npm", "install") for c in cmds)
    assert any(c[:3] == ("npm", "run", "build") for c in cmds)


def test_init_is_idempotent_and_never_overwrites_config(tmp_path):
    root = _repo(tmp_path)
    data = tmp_path / "data"
    runner = Runner()
    I.run_init(root, data_dir=data, run=runner)
    (root / "orchestra.toml").write_text("# my edits\n[data]\ndir = \"%s\"\n" % data)
    first_token = (data / "state" / "watch-gateway-token").read_text()
    runner2 = Runner()
    report = I.run_init(root, data_dir=data, run=runner2)
    assert all(r.did is False for r in report), [r for r in report if r.did]
    assert (root / "orchestra.toml").read_text().startswith("# my edits")
    assert (data / "state" / "watch-gateway-token").read_text() == first_token
    assert runner2.calls == []


def test_init_skips_npm_and_venv_when_asked(tmp_path):
    root = _repo(tmp_path)
    runner = Runner()
    report = I.run_init(root, data_dir=tmp_path / "d", run=runner, skip_npm=True, skip_venv=True, skip_build=True)
    done = {r.step: r for r in report}
    assert done["venv"].did is False and "skipped" in done["venv"].detail
    assert done["npm:api"].did is False
    assert runner.calls == []


def test_init_reads_data_dir_from_existing_config(tmp_path):
    root = _repo(tmp_path)
    data = tmp_path / "from-config"
    (root / "orchestra.toml").write_text(f'[data]\ndir = "{data}"\n')
    I.run_init(root, data_dir=None, run=Runner())
    assert (data / "state").is_dir()


def test_init_data_dir_defaults_to_home_orchestra(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = _repo(tmp_path)
    I.run_init(root, data_dir=None, run=Runner())
    assert (tmp_path / "home" / ".orchestra" / "state").is_dir()
    assert str(tmp_path / "home" / ".orchestra") in (root / "orchestra.toml").read_text()


def test_render_report_lists_did_and_skipped(tmp_path):
    root = _repo(tmp_path)
    report = I.run_init(root, data_dir=tmp_path / "d", run=Runner())
    text = I.render_report(report)
    assert "did" in text and "config" in text


def test_init_seeds_tasks_db_schema_for_msg_store_and_router(tmp_path):
    """msg_store.py, scripts/message-router.py and the rotation beat all open
    <data>/state/tasks.db expecting the messages + conversations tables; only the api
    created them (on its first boot). Under `orchestra up` the beats start at t=0 and
    crashed with 'no such table: messages' until the api came up. init seeds the schema
    (same columns as api/src/lib/db.ts) so every consumer works from the first tick."""
    import sqlite3
    root = _repo(tmp_path)
    data = tmp_path / "data"
    report = I.run_init(root, data_dir=data, run=Runner())
    done = {r.step: r for r in report}
    assert done["seed:state/tasks.db"].did is True
    conn = sqlite3.connect(data / "state" / "tasks.db")
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"messages", "conversations"} <= tables
    cols = {r[1] for r in conn.execute("pragma table_info(messages)")}
    assert {"id", "from_agent", "to_agent", "type", "status", "depends_on", "gather_mode",
            "tenant_id", "created_at"} <= cols
    # idempotent: a second init keeps the db and reports it present
    report2 = I.run_init(root, data_dir=data, run=Runner())
    assert {r.step: r for r in report2}["seed:state/tasks.db"].did is False
