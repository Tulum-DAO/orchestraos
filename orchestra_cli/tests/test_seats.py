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


# ---- `orchestra starter`: the first-install default team (Shaw, 2026-10-08) ----
# "give them a gm, a project manager, and a worker under that project manager. That way they
# have one t0, one t1, and one t2 agent from the start" — then: "MAKE THAT THE DEFAULT".

def _starter_env(monkeypatch, alive=lambda name: True):
    calls = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: calls.append(argv) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_pane_alive", alive)
    monkeypatch.setattr(SE, "_refuse_if_no_runtime_authed", lambda st: None)
    return calls


def test_parse_starter():
    ns = M.parse_args(["starter"])
    assert ns.command == "starter" and ns.project == "first-project"
    assert M.parse_args(["starter", "--project", "website"]).project == "website"


def test_starter_creates_one_seat_per_tier_linked_gm_pm_worker(repo_with_templates, tmp_path, monkeypatch):
    spawned = set()
    calls = _starter_env(monkeypatch, alive=lambda name: name in spawned)
    real_run = SE._run
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    rc = M.main(["starter"])
    assert rc == 0
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]
    gm, pm, dev = reg["gm"], reg["pm-first-project"], reg["dev-first-project"]
    assert (gm["tier"], pm["tier"], dev["tier"]) == ("T0", "T1", "T2")
    assert gm["always_on"] is True
    assert pm["reports_to"] == "gm" and dev["reports_to"] == "pm-first-project"
    assert [c[1] for c in calls] == ["gm", "pm-first-project", "dev-first-project"]   # top-down
    for name in ("pm-first-project", "dev-first-project"):
        assert "{" not in (repo_with_templates / "prompts" / f"{name}.md").read_text()  # every token filled
    assert "Parent: pm-first-project" in (repo_with_templates / "prompts" / "dev-first-project.md").read_text()


def test_starter_is_idempotent_when_the_team_is_already_alive(repo_with_templates, tmp_path, monkeypatch, capsys):
    calls = _starter_env(monkeypatch, alive=lambda name: False)
    spawned = set()
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter"]) == 0
    calls.clear()
    assert M.main(["starter"]) == 0
    assert calls == []                                        # nothing re-spawned
    assert "already running" in capsys.readouterr().out


def test_starter_relaunches_a_registered_seat_that_died_without_rewriting_its_prompt(repo_with_templates, tmp_path, monkeypatch):
    spawned = set()
    calls = _starter_env(monkeypatch)
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter"]) == 0
    spawned.discard("dev-first-project"); calls.clear()        # the worker's CLI exited
    assert M.main(["starter"]) == 0
    assert [c[1] for c in calls] == ["dev-first-project"]


def test_starter_project_names_the_pm_and_worker(repo_with_templates, tmp_path, monkeypatch):
    spawned = set()
    calls = _starter_env(monkeypatch)
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter", "--project", "website"]) == 0
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]
    assert reg["pm-website"]["tier"] == "T1" and reg["dev-website"]["reports_to"] == "pm-website"


def test_starter_stops_at_the_first_seat_that_fails(repo_with_templates, tmp_path, monkeypatch, capsys):
    _starter_env(monkeypatch, alive=lambda name: False)     # nothing ever comes alive
    rc = M.main(["starter"])
    assert rc != 0
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]
    assert "pm-first-project" not in reg                     # did not build on a dead gm


def test_starter_corrects_a_starter_seat_registered_with_the_wrong_tier(repo_with_templates, tmp_path, monkeypatch, capsys):
    """register_seat keeps an existing row verbatim, so a gm row written as T2 (e.g. by
    `orchestra spawn gm` without --gm) stayed T2 forever. The starter team owns these tiers."""
    (tmp_path / "data" / "registry.json").write_text(json.dumps(
        {"agents": {"gm": {"name": "gm", "tier": "T2", "tmux_session": "gm", "runtime": "claude"}}}))
    spawned = {"gm"}
    calls = _starter_env(monkeypatch)
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter"]) == 0
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]
    assert reg["gm"]["tier"] == "T0" and reg["gm"]["always_on"] is True
    assert "gm tier T2 -> T0" in capsys.readouterr().out


# ---- starter follow-ups (orchestraos-builder review of #243) ----

@pytest.mark.parametrize("bad", ["my project", "a.b", "Site", "x/y", "", "-lead"])
def test_starter_refuses_a_project_name_tmux_would_mangle(repo_with_templates, tmp_path, monkeypatch, capsys, bad):
    calls = _starter_env(monkeypatch)
    assert M.main(["starter", f"--project={bad}"]) == 2
    assert calls == [] and "lowercase letters, digits and dashes" in capsys.readouterr().err


def test_starter_worker_works_in_its_own_project_dir_not_the_harness_checkout(repo_with_templates, tmp_path, monkeypatch):
    spawned = set()
    calls = _starter_env(monkeypatch)
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: (calls.append(argv), spawned.add(argv[1]))[0] or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in spawned)
    assert M.main(["starter"]) == 0
    proj = tmp_path / "data" / "projects" / "first-project"
    reg = json.loads((tmp_path / "data" / "registry.json").read_text())["agents"]
    assert proj.is_dir()
    assert reg["dev-first-project"]["cwd"] == str(proj)
    assert reg["gm"]["cwd"] == str(repo_with_templates)               # gm and the PM stay in the checkout
    assert f"cwd {proj}" in (repo_with_templates / "prompts" / "dev-first-project.md").read_text()
