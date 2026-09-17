"""RED-first: `orchestra spawn <seat> [--gm]` and `orchestra rotate <seat>` (Tier 0 item 2).

spawn registers the seat in <data>/registry.json when absent (runtime/model/prompt/tier),
then runs spawn-agent.sh with the child env (ORCHESTRA_DIR=data, AGENT_RUNTIME/AGENT_MODEL)
and verifies the tmux session by effect. rotate refuses without a banked handoff unless
--synthesize, then runs scripts/rotate_agent.py under the same env.
"""
import json
from pathlib import Path

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import seats as SE


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"; (root / "prompts").mkdir(parents=True); (root / "scripts").mkdir()
    (root / "prompts" / "gm.md").write_text("# gm\n")
    (root / "spawn-agent.sh").write_text("#!/bin/bash\nexit 0\n")
    (root / "scripts" / "rotate_agent.py").write_text("print('rotate stub')\n")
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{tmp_path / "data"}"\n[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n')
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("ORCHESTRA_ROOT", str(root))
    monkeypatch.delenv("ORCHESTRA_CONFIG", raising=False)
    return root


def test_parse_spawn_and_rotate():
    ns = M.parse_args(["spawn", "gm", "--gm", "--task", "hello"])
    assert ns.command == "spawn" and ns.seat == "gm" and ns.gm and ns.task == "hello"
    ns = M.parse_args(["rotate", "gm", "--dry-run"])
    assert ns.command == "rotate" and ns.seat == "gm" and ns.dry_run


def test_spawn_registers_seat_and_runs_spawn_agent_with_child_env(repo, tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, env=None, cwd=None):
        calls.append((argv, env, cwd)); return 0
    monkeypatch.setattr(SE, "_run", fake_run)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    rc = M.main(["spawn", "planner", "--task", "plan the week", "--model", "claude-opus-5[1m]"])
    assert rc == 0
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())
    a = reg["agents"]["planner"]
    assert a["runtime"] == "claude" and a["model"] == "claude-opus-5[1m]" and a["tier"] == "T2"
    assert a["tmux_session"] == "planner" and a["system_prompt"] == "prompts/planner.md"
    argv, env, cwd = calls[-1]
    assert argv[0].endswith("spawn-agent.sh") and argv[1] == "planner" and "--task" in argv
    assert env["ORCHESTRA_DIR"] == str(tmp_path / "data") and env["AGENT_RUNTIME"] == "claude"
    assert env["AGENT_MODEL"] == "claude-opus-5[1m]" and env["ORCHESTRA_ROOT"] == str(repo)


def test_spawn_gm_uses_gm_prompt_tier1_always_on(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    assert M.main(["spawn", "gm", "--gm"]) == 0
    a = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]["gm"]
    assert a["tier"] == "T1" and a["always_on"] is True and a["system_prompt"] == "prompts/gm.md"


def test_spawn_fails_by_effect_when_no_tmux_session_appears(repo, monkeypatch, capsys):
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: False)
    assert M.main(["spawn", "planner"]) != 0
    assert "no tmux session" in capsys.readouterr().err


def test_spawn_keeps_existing_registry_row(repo, tmp_path, monkeypatch):
    (tmp_path / "data" / "registry.json").write_text(json.dumps({"agents": {"planner": {"name": "planner", "runtime": "codex", "model": "gpt-x", "tier": "T2", "tmux_session": "planner"}}}))
    seen = {}
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: seen.update(env=env) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    assert M.main(["spawn", "planner"]) == 0
    assert seen["env"]["AGENT_RUNTIME"] == "codex" and seen["env"]["AGENT_MODEL"] == "gpt-x"


def test_rotate_refuses_without_handoff_then_runs_rotate_agent(repo, tmp_path, monkeypatch, capsys):
    (tmp_path / "data" / "registry.json").write_text(json.dumps({"agents": {"gm": {"name": "gm", "runtime": "claude", "generation": 1, "tmux_session": "gm"}}}))
    calls = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: calls.append((argv, env)) or 0)
    assert M.main(["rotate", "gm"]) == 2
    assert "handoff" in capsys.readouterr().err.lower() and not calls
    (tmp_path / "data" / "docs").mkdir()
    (tmp_path / "data" / "docs" / "HANDOFF_gm-next.md").write_text("# baton\n## canary_questions\n- {id: q1, question: \"x\", expected_answer: \"y\"}\n")
    assert M.main(["rotate", "gm", "--dry-run"]) == 0
    argv, env = calls[-1]
    assert argv[1].endswith("scripts/rotate_agent.py") and argv[2] == "gm" and "--dry-run" in argv
    assert env["ORCHESTRA_DIR"] == str(tmp_path / "data") and env["AGENT_RUNTIME"] == "claude"


def test_rotate_synthesize_writes_a_minimal_baton(repo, tmp_path, monkeypatch):
    (tmp_path / "data" / "registry.json").write_text(json.dumps({"agents": {"gm": {"name": "gm", "runtime": "claude", "generation": 1, "tmux_session": "gm"}}}))
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    assert M.main(["rotate", "gm", "--synthesize", "--dry-run"]) == 0
    doc = (tmp_path / "data" / "docs" / "HANDOFF_gm-next.md").read_text()
    assert "canary_questions" in doc and "q1" in doc
