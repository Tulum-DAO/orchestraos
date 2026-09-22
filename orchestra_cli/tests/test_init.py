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
        self.calls_with_env = []

    def __call__(self, argv, cwd=None, env=None):
        self.calls.append((tuple(argv), str(cwd)))
        self.calls_with_env.append((tuple(argv), str(cwd), dict(env or {})))
        argv = list(argv)
        cwd = Path(cwd) if cwd else Path(".")
        if argv[:2] == ["git", "init"]:
            (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)   # simulate the data-dir repo
        elif argv[:1] == ["git"]:
            pass
        elif argv[-2:] == ["-m", "venv"] or "venv" in argv:
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
    assert [c for c in runner.calls if c[0][0] != "git"] == []   # only the data-dir git init ran


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


def test_init_seeds_the_approval_and_questionnaire_schema(tmp_path):
    """scripts/approval_resume.py (a supervisor beat) and questionnaire_resume read
    approval_requests / questionnaires from <data>/state/tasks.db; only the first
    `approval.py request` created them, so the beat crashed every tick on a fresh install
    until someone asked for a card (B1 finding 6 follow-up, seen by effect 2026-09-16)."""
    root = _repo(tmp_path)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "approval_schema.py").write_text("")       # presence gates the seed
    (root / "scripts" / "questionnaire_schema.py").write_text("")
    data = tmp_path / "data"
    runner = Runner()
    report = I.run_init(root, data_dir=data, run=runner)
    done = {r.step: r for r in report}
    assert done["seed:approval-schema"].did is True
    seed_calls = [(argv, cwd, env) for (argv, cwd, env) in runner.calls_with_env
                  if "ApprovalStore" in " ".join(argv)]
    assert len(seed_calls) == 1
    argv, cwd, env = seed_calls[0]
    assert "QuestionnaireStore" in " ".join(argv) and ".migrate()" in " ".join(argv)
    assert env["ORCHESTRA_DIR"] == str(data)
    assert str(root / "scripts") in env["PYTHONPATH"]


def test_init_schema_seed_arms_the_ruled_gated_migrations(tmp_path):
    """gm ruling msg_3f772533: a fresh data dir must never log an error per minute. The
    approval schema keeps landed contracts behind an operator DDL gate (APPROVAL_DDL_ARMED);
    unarmed, approval_resume logs 'no such column: snoozed_until' every beat. init's seed arms
    them explicitly (never 'all' — a future batch stays gated until ruled)."""
    root = _repo(tmp_path)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "approval_schema.py").write_text("")
    (root / "scripts" / "questionnaire_schema.py").write_text("")
    runner = Runner()
    I.run_init(root, data_dir=tmp_path / "data", run=runner)
    argv, cwd, env = [c for c in runner.calls_with_env if "ApprovalStore" in " ".join(c[0])][0]
    armed = set(env["APPROVAL_DDL_ARMED"].split(","))
    assert armed == {"m20260825_answer_attribution", "m20260825_human_task"}


def test_init_demo_seeds_three_fixture_seats_and_runs_the_card_seeder(tmp_path):
    """B5: `orchestra init --demo` seeds three generic fixture seats into the data-dir
    registry and runs scripts/demo_seed_cards.py (approval, menu, questionnaire, human
    task) so the dashboard is not empty on first open. Idempotent."""
    root = _repo(tmp_path)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "approval_schema.py").write_text("")
    (root / "scripts" / "questionnaire_schema.py").write_text("")
    (root / "scripts" / "demo_seed_cards.py").write_text("")
    data = tmp_path / "data"
    runner = Runner()
    report = I.run_init(root, data_dir=data, run=runner, demo=True)
    done = {r.step: r for r in report}
    assert done["demo:registry"].did is True and done["demo:cards"].did is True
    reg = json.loads((data / "registry.json").read_text())["agents"]
    assert set(reg) == {"demo-planner", "demo-builder", "demo-reviewer"}
    assert all(v["tmux_session"] == k and v["demo"] is True for k, v in reg.items())
    seed = [c for c in runner.calls_with_env if c[0][-1].endswith("scripts/demo_seed_cards.py")]
    assert len(seed) == 1 and seed[0][2]["ORCHESTRA_DIR"] == str(data)
    # second --demo run: seats already present, seeder still invoked (it dedups itself)
    report2 = I.run_init(root, data_dir=data, run=runner, demo=True)
    assert {r.step: r for r in report2}["demo:registry"].did is False
    # without --demo nothing demo-related happens
    report3 = I.run_init(root, data_dir=tmp_path / "data2", run=Runner())
    assert not any(r.step.startswith("demo:") for r in report3)


def test_init_installs_claude_hooks_into_config_dir(tmp_path, monkeypatch):
    """Tier 0 item 1: init writes the shipped hooks into $CLAUDE_CONFIG_DIR/settings.json."""
    root = _repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("ORCHESTRA_SKIP_HOOKS", raising=False)
    # the test repo stub has no hooks/ dir: point the installer at the real one via ORCHESTRA_ROOT-like copy
    import shutil
    real = Path(I.__file__).resolve().parent.parent
    shutil.copytree(real / "hooks", root / "hooks", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (root / "scripts" / "lineage_daemon").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "lineage_daemon" / "bus_feeder.py").write_text("")
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True, yes=True)
    done = {r.step: r for r in report}
    assert done["hooks"].did, done["hooks"].detail
    s = json.loads((cfg / "settings.json").read_text())
    cmds = [h["command"] for rules in s["hooks"].values() for r in rules for h in r["hooks"]]
    assert any("agent-queue-drain.py" in c and f'ORCHESTRA_DIR="{tmp_path / "data"}"' in c for c in cmds)


def test_init_makes_the_data_dir_a_git_repo_for_rotation_artifacts(tmp_path):
    """The promotion gate proves the successor's readback by commit in the data dir."""
    root = _repo(tmp_path)
    data = tmp_path / "data"
    report = I.run_init(root, data_dir=data, run=I.default_run, skip_npm=True, skip_venv=True)
    done = {r.step: r for r in report}
    assert done["data-git"].did, done["data-git"].detail
    assert (data / ".git").is_dir()
    ignored = (data / ".gitignore").read_text()
    assert "!/state/agent-handoffs/**" in ignored and "!/docs/**" in ignored
    # idempotent
    report = I.run_init(root, data_dir=data, run=I.default_run, skip_npm=True, skip_venv=True)
    assert {r.step: r for r in report}["data-git"].detail == "present"


def test_sandbox_fixture_isolates_tmux_and_config_dir():
    """The autouse sandbox: no test can reach the developer's tmux server or Claude config."""
    assert "TMUX" not in os.environ
    assert os.environ["TMUX_TMPDIR"].startswith("/tmp") and "claude-config" in os.environ["CLAUDE_CONFIG_DIR"]


# ---- shared-config footprint (rab msg_2ea45964, gm-accepted Tier 0 item, 2026-09-17) ----
# `orchestra init` on a host that already runs OrchestraOS ignored ORCHESTRA_DIR and wrote the
# operator's real ~/.orchestra + ~/.claude/settings.json without asking. Rulings: (1) env
# ORCHESTRA_DIR / CLAUDE_CONFIG_DIR win over config data.dir; (2) print the hook plan and ask,
# or accept --yes; (3) never touch the default ~/.orchestra when ORCHESTRA_DIR points elsewhere.

def _hooks_repo(tmp_path: Path):
    import shutil
    root = _repo(tmp_path)
    real = Path(I.__file__).resolve().parent.parent
    shutil.copytree(real / "hooks", root / "hooks", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (root / "scripts" / "lineage_daemon").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "lineage_daemon" / "bus_feeder.py").write_text("")
    return root


def test_init_honors_ORCHESTRA_DIR_over_the_default_and_never_touches_home_orchestra(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    data = tmp_path / "elsewhere"
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    root = _repo(tmp_path)
    report = I.run_init(root, data_dir=None, run=Runner(), skip_npm=True, skip_venv=True)
    assert (data / "state").is_dir()
    assert not (tmp_path / "home" / ".orchestra").exists()
    assert f'dir = "{data}"' in (root / "orchestra.toml").read_text()
    assert str(data) in {r.step: r for r in report}["data-dir"].detail


def test_init_honors_ORCHESTRA_DIR_over_an_existing_config_and_says_so(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    from_config = tmp_path / "from-config"
    (root / "orchestra.toml").write_text(f'[data]\ndir = "{from_config}"\n')
    data = tmp_path / "from-env"
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    report = I.run_init(root, data_dir=None, run=Runner(), skip_npm=True, skip_venv=True)
    done = {r.step: r for r in report}
    assert (data / "state").is_dir()
    assert not from_config.exists()
    # config is never rewritten, but the operator is told the env override applied
    assert f'dir = "{from_config}"' in (root / "orchestra.toml").read_text()
    assert "ORCHESTRA_DIR" in done["config"].detail and str(data) in done["config"].detail


def test_init_explicit_flag_still_beats_ORCHESTRA_DIR(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "env"))
    root = _repo(tmp_path)
    I.run_init(root, data_dir=tmp_path / "flag", run=Runner(), skip_npm=True, skip_venv=True)
    assert (tmp_path / "flag" / "state").is_dir() and not (tmp_path / "env").exists()


def test_init_hooks_are_skipped_non_interactively_without_yes(tmp_path, monkeypatch):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("ORCHESTRA_YES", raising=False)
    (cfg).mkdir()
    (cfg / "settings.json").write_text('{"model": "keep-me", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}')
    before = (cfg / "settings.json").read_text()
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True,
                        confirm=None, interactive=False)
    done = {r.step: r for r in report}
    assert not done["hooks"].did
    assert "--yes" in done["hooks"].detail and str(cfg / "settings.json") in done["hooks"].detail
    assert (cfg / "settings.json").read_text() == before


def test_init_hooks_prompt_shows_the_plan_and_a_no_writes_nothing(tmp_path, monkeypatch):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    seen = []

    def decline(prompt: str) -> bool:
        seen.append(prompt)
        return False

    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True,
                        confirm=decline)
    done = {r.step: r for r in report}
    assert not done["hooks"].did and "declined" in done["hooks"].detail
    assert not (cfg / "settings.json").exists()
    assert len(seen) == 1
    plan = seen[0]
    assert str(cfg / "settings.json") in plan and "12 hook rows" in plan
    assert f'ORCHESTRA_DIR="{tmp_path / "data"}"' in plan          # the exact command text it will write
    assert "agent-queue-drain.py" in plan


def test_init_hooks_prompt_yes_installs(tmp_path, monkeypatch):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True,
                        confirm=lambda _p: True)
    assert {r.step: r for r in report}["hooks"].did
    assert (cfg / "settings.json").exists()


def test_init_yes_flag_installs_hooks_without_asking(tmp_path, monkeypatch):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    asked = []
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True,
                        yes=True, confirm=lambda p: asked.append(p) or True, interactive=False)
    assert {r.step: r for r in report}["hooks"].did and asked == []


def test_init_ORCHESTRA_YES_env_counts_as_yes(tmp_path, monkeypatch):
    root = _hooks_repo(tmp_path)
    cfg = tmp_path / "claude-cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("ORCHESTRA_YES", "1")
    report = I.run_init(root, data_dir=tmp_path / "data", run=Runner(), skip_npm=True, skip_venv=True,
                        interactive=False)
    assert {r.step: r for r in report}["hooks"].did


def test_init_creates_facts_and_memory_dirs(tmp_path):
    """Gate step 7 (fact written -> restart -> Arturo recalls it) needs the facts
    store dir to exist for POST /api/facts, and the per-agent memory convention
    (docs/MEMORY.md) needs its root — both are data, created by init."""
    root = _repo(tmp_path)
    data = tmp_path / "data"
    I.run_init(root, data_dir=data, run=Runner())
    assert (data / "facts").is_dir()
    assert (data / "memory").is_dir()
    ignored = (data / ".gitignore").read_text()
    # memory is per-agent durable knowledge — committed with the handoffs, not ignored
    assert "!/memory/**" in ignored


def test_init_wires_the_shipped_pre_push_hook_when_root_is_a_git_checkout(tmp_path):
    """The repo ships .git-hooks/pre-push (secret scan) but core.hooksPath is unset in a fresh
    clone, so the hook never runs for anyone who did not read its header comment. `init` is
    the one command every clone runs: it must point core.hooksPath at .git-hooks."""
    root = _repo(tmp_path)
    (root / ".git").mkdir()
    (root / ".git-hooks").mkdir()
    (root / ".git-hooks" / "pre-push").write_text("#!/bin/sh\nexit 0\n")
    runner = Runner()
    report = I.run_init(root, data_dir=tmp_path / "data", run=runner, skip_npm=True, skip_venv=True,
                        skip_build=True)
    done = {r.step: r for r in report}
    assert done["git-hooks"].did is True
    assert (("git", "-C", str(root), "config", "core.hooksPath", ".git-hooks"), str(root)) in runner.calls


def test_init_skips_hook_wiring_outside_a_git_checkout(tmp_path):
    """A tarball / demo-box install has no .git — nothing to configure, never an error."""
    root = _repo(tmp_path)
    runner = Runner()
    report = I.run_init(root, data_dir=tmp_path / "data", run=runner, skip_npm=True, skip_venv=True,
                        skip_build=True)
    done = {r.step: r for r in report}
    assert done["git-hooks"].did is False
    assert not any(c[0][:1] == ("git",) and "core.hooksPath" in c[0] for c in runner.calls)


# ---- item C: `orchestra init --stt` installs the opt-in local speech-to-text extra ------------------
def test_init_without_stt_never_touches_requirements_stt(tmp_path):
    root = _repo(tmp_path)
    (root / "requirements-stt.txt").write_text("faster-whisper\n")
    r = Runner()
    report = I.run_init(root, data_dir=tmp_path / "d", run=r, skip_npm=True, skip_build=True)
    assert not any("requirements-stt.txt" in " ".join(c[0]) for c in r.calls)
    assert not any(s.step == "pip:stt" for s in report)


def test_init_stt_installs_the_extra_and_prefetches_the_model(tmp_path):
    root = _repo(tmp_path)
    (root / "requirements-stt.txt").write_text("faster-whisper\n")
    r = Runner()
    report = I.run_init(root, data_dir=tmp_path / "d", run=r, skip_npm=True, skip_build=True, stt=True)
    pip_calls = [c for c in r.calls if "requirements-stt.txt" in " ".join(c[0])]
    assert len(pip_calls) == 1 and "-r" in pip_calls[0][0]
    fetch = [c for c in r.calls_with_env if any("local_stt" in a for a in c[0])]
    assert len(fetch) == 1 and fetch[0][2]["ORCHESTRA_DIR"] == str((tmp_path / "d").resolve())
    by = {s.step: s for s in report}
    assert by["pip:stt"].did and by["stt:model"].did
    # idempotent: second run reports present, no second pip
    report2 = I.run_init(root, data_dir=tmp_path / "d", run=r, skip_npm=True, skip_build=True, stt=True)
    assert {s.step: s.detail for s in report2}["pip:stt"] == "local speech-to-text present"
    assert len([c for c in r.calls if "requirements-stt.txt" in " ".join(c[0])]) == 1


def test_init_stt_from_config_flag(tmp_path):
    root = _repo(tmp_path)
    (root / "requirements-stt.txt").write_text("faster-whisper\n")
    (root / "orchestra.toml").write_text('[arturo]\nlocal_stt = true\n')
    r = Runner()
    I.run_init(root, data_dir=tmp_path / "d", run=r, skip_npm=True, skip_build=True)
    assert any("requirements-stt.txt" in " ".join(c[0]) for c in r.calls)
