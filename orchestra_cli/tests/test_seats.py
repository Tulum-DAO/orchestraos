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


def test_spawn_gm_uses_gm_prompt_tier0_always_on(repo, tmp_path, monkeypatch):
    """The manager seat is T0 (Shaw, 2026-09-22): docs/REFERENCE_INSTALL.md already defines
    T0 as "the always-on manager seat" and T1 as coordinators, but --gm registered T1."""
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    assert M.main(["spawn", "gm", "--gm"]) == 0
    a = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]["gm"]
    assert a["tier"] == "T0" and a["always_on"] is True and a["system_prompt"] == "prompts/gm.md"


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


# --- P0-2: spawn refuses when NO runtime is authenticated, and FAILS OPEN on any doubt -------------
# A stranger who has not logged in to their CLI gets, today, two injection retries and
# "Injection FAILED twice ... agent may be idle without a task" — the word "login" never appears,
# while the CLI's own sign-in screen waits unread in the pane. The guard says what doctor already
# knows. It must refuse ONLY on a positive determination that nothing is authed: probe error,
# timeout, unknown, unexpected output or missing probe all PROCEED exactly as before, because a
# false refusal breaks every spawn for everyone.

def test_spawn_refuses_when_no_runtime_is_authenticated(repo, tmp_path, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: ran.append(argv) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_authed_runtimes", lambda st: ([], None))  # positive: probed, none authed
    rc = M.main(["spawn", "hello", "--task", "Say hello, then park."])
    assert rc == 2, "spawn must refuse rather than burn two injection retries and exit 1"
    assert ran == [], "spawn-agent.sh must not run at all"
    err = capsys.readouterr().err
    assert "log" in err.lower(), "the refusal must name the login — that is the whole point"
    # doctor's own remedy, verbatim — one probe, one wording
    assert "At least one of [runtimes] enabled must be installed and logged in" in err


def test_spawn_proceeds_when_a_runtime_is_authenticated(repo, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: ran.append(argv) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_authed_runtimes", lambda st: (["claude"], None))
    assert M.main(["spawn", "hello"]) == 0
    assert ran and ran[0][0].endswith("spawn-agent.sh")


@pytest.mark.parametrize("failure", ["raises", "unknown", "no-probe"])
def test_spawn_FAILS_OPEN_when_the_probe_cannot_determine_auth(repo, tmp_path, monkeypatch, failure):
    """The hard property: any doubt proceeds. A false refusal is worse than a bad message."""
    ran = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: ran.append(argv) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    if failure == "raises":
        def boom(st):
            raise RuntimeError("probe blew up")
        monkeypatch.setattr(SE, "_authed_runtimes", boom)
    elif failure == "unknown":
        monkeypatch.setattr(SE, "_authed_runtimes", lambda st: ([], "providers.json missing"))
    else:
        monkeypatch.setattr(SE, "_authed_runtimes", lambda st: ([], "probe unavailable"))
    assert M.main(["spawn", "hello"]) == 0, f"must proceed when auth is undetermined ({failure})"
    assert ran and ran[0][0].endswith("spawn-agent.sh")


# --- issue #93: `orchestra agent create` ---------------------------------------------------
# Fresh-install DX report (2026-09-20): creating an agent meant copying a template, sed-ing
# placeholders nothing checks, editing registry.json with a Python snippet, then spawning.
# One verb: fill the template (refuse an unfilled {TOKEN}), record the parent, validate the
# runtime/model pair, register, spawn, and verify the seat is ALIVE (pane + process), not
# merely that a tmux session name exists.

@pytest.fixture
def repo_with_templates(repo):
    (repo / "prompts" / "_dev-template.md").write_text(
        "# You are: {DEV_NAME}\n# Parent: {PARENT_PM}\n# Project: {PROJECT}\ncwd {CWD}\n")
    (repo / "prompts" / "_pm-template.md").write_text(
        "# {PM_NAME} for {CLIENT_NAME} ({CLIENT_SLUG}) on {PROJECT} branch {BRANCH}; id {YOUR_ID}\n")
    return repo


def test_parse_agent_create():
    ns = M.parse_args(["agent", "create", "dev-x", "--tier", "T2", "--runtime", "claude",
                       "--parent", "pm-y", "--template", "dev", "--set", "PROJECT=demo"])
    assert ns.command == "agent" and ns.agent_command == "create" and ns.name == "dev-x"
    assert ns.parent == "pm-y" and ns.template == "dev" and ns.set == ["PROJECT=demo"]


def test_agent_create_fills_template_records_parent_and_spawns(repo_with_templates, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: calls.append(argv) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: True)
    rc = M.main(["agent", "create", "dev-x", "--parent", "pm-y", "--template", "dev",
                 "--set", "PROJECT=demo", "--model", "claude-opus-5[1m]"])
    assert rc == 0
    prompt = (repo_with_templates / "prompts" / "dev-x.md").read_text()
    assert "You are: dev-x" in prompt and "Parent: pm-y" in prompt and "Project: demo" in prompt
    assert "{" not in prompt                                  # every token filled
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]["dev-x"]
    assert reg["reports_to"] == "pm-y" and reg["system_prompt"] == "prompts/dev-x.md"
    assert calls and calls[-1][0].endswith("spawn-agent.sh") and calls[-1][1] == "dev-x"


def test_agent_create_refuses_an_unfilled_placeholder_before_registering(repo_with_templates, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    rc = M.main(["agent", "create", "pm-z", "--template", "pm", "--set", "PROJECT=demo"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "CLIENT_NAME" in err and "CLIENT_SLUG" in err and "BRANCH" in err
    assert not (repo_with_templates / "prompts" / "pm-z.md").exists()
    assert "pm-z" not in json.loads((tmp_path / "data" / "registry.json").read_text()).get("agents", {}) \
        if (tmp_path / "data" / "registry.json").exists() else True


def test_agent_create_refuses_a_model_of_another_runtime(repo_with_templates, monkeypatch, capsys):
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    rc = M.main(["agent", "create", "codex-helper", "--runtime", "codex", "--model", "claude-sonnet-5",
                 "--template", "dev", "--set", "PROJECT=demo", "--parent", "gm"])
    assert rc == 2 and "claude-sonnet-5" in capsys.readouterr().err


def test_agent_create_fails_by_effect_when_the_seat_is_not_alive(repo_with_templates, monkeypatch, capsys):
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: False)     # session exists, CLI died
    rc = M.main(["agent", "create", "dev-dead", "--template", "dev", "--set", "PROJECT=demo", "--parent", "gm"])
    assert rc == 1 and "not alive" in capsys.readouterr().err
