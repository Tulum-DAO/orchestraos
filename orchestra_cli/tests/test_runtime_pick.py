"""RED-first (PR 0, Codex seats start on the first try): which runtime a new seat gets, the
refusal when the chosen runtime cannot start, and the "mail it" line spawn prints.

Gate container finding (Codex-only image, 2026-10-10): with [runtimes] enabled = ["claude",
"gemini", "codex"] (init's default) and only codex installed, `orchestra spawn x` registered a
CLAUDE seat (first enabled), the login guard passed because codex was logged in, and the seat
could never start. The printed "mail it" command failed with `unable to open database file`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestra_cli import __main__ as M
from orchestra_cli import seats as SE

REAL_PROVIDERS = Path(__file__).resolve().parents[2] / "config" / "providers.json"
CHECKOUT = Path(__file__).resolve().parents[2]


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A checkout with the REAL providers.json, a fake HOME, and a `which` we control."""
    root = tmp_path / "repo"; (root / "prompts").mkdir(parents=True); (root / "config").mkdir()
    shutil.copy(REAL_PROVIDERS, root / "config" / "providers.json")
    (root / "spawn-agent.sh").write_text("#!/bin/bash\nexit 0\n")
    data = tmp_path / "data"; data.mkdir()
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n[notify]\nchannel = "none"\n'
        '[runtimes]\nenabled = ["claude", "gemini", "codex"]\n')
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ORCHESTRA_ROOT", str(root))
    monkeypatch.delenv("ORCHESTRA_CONFIG", raising=False)
    installed = set()
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in installed else None)

    real_popen_init = subprocess.Popen.__init__
    cli_calls = []

    def no_cli_commands(self, args, *a, **k):
        first = (args if isinstance(args, str) else (list(args) or [""])[0]).split()[0]
        if os.path.basename(first) in ("claude", "codex", "agy", "gemini"):
            cli_calls.append(args)      # recorded, not just raised: the pick swallows errors
            raise AssertionError(f"the runtime pick must not run an agent CLI (network/time): {args}")
        return real_popen_init(self, args, *a, **k)
    monkeypatch.setattr(subprocess.Popen, "__init__", no_cli_commands)
    ran = []
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: ran.append((argv, env)) or 0)
    monkeypatch.setattr(SE, "_tmux_has_session", lambda name: True)
    monkeypatch.setattr(SE, "_authed_runtimes", lambda st: (["codex"], None))
    # claude's login: a Linux box with no key or token in the env, and `claude auth status`
    # answered by the test (None = undetermined, the default) instead of the real CLI.
    for k in SE._CLAUDE_AUTH_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))   # isolated, and the path read
    monkeypatch.setattr(SE, "_platform", lambda: "linux")
    status = {"answer": None}
    status_calls = []
    monkeypatch.setattr(SE, "_claude_auth_status", lambda: status_calls.append(1) or status["answer"])
    yield type("Box", (), {"root": root, "data": data, "home": home, "installed": installed, "ran": ran,
                           "status": status, "status_calls": status_calls})
    assert cli_calls == [], f"an agent CLI was run while picking/refusing (must be local only): {cli_calls}"


def codex_logged_in(box):
    (box.home / ".codex").mkdir(exist_ok=True)
    (box.home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"id_token": "x"}}))


def st():
    from orchestra_cli import settings as S
    return S.load_settings()


def reg(box):
    return json.loads((box.data / "registry.json").read_text())["agents"]


# --- the pick -----------------------------------------------------------------------------

def test_codex_only_box_gives_a_new_seat_codex_not_claude(box):
    box.installed.add("codex"); codex_logged_in(box)
    assert SE.default_runtime(st()) == "codex"
    assert M.main(["spawn", "w1"]) == 0
    assert reg(box)["w1"]["runtime"] == "codex"
    assert box.ran[-1][1]["AGENT_RUNTIME"] == "codex"


def test_claude_installed_and_first_keeps_claude_exactly_as_today(box):
    """gm condition 3: on a claude box the choice is unchanged — even with codex logged in too and
    even when claude's local login file is absent. Local doubt never moves the pick off claude;
    only `claude auth status` saying "signed out" does, and here it is undetermined."""
    box.installed.update({"claude", "codex"}); codex_logged_in(box)
    assert not (box.home / ".claude.json").exists()
    assert SE.default_runtime(st()) == "claude"
    assert M.main(["spawn", "w2"]) == 0
    assert reg(box)["w2"]["runtime"] == "claude"


def test_a_codex_install_that_is_logged_out_is_skipped(box):
    box.installed.update({"codex", "agy"})
    tok = box.home / ".gemini" / "antigravity-cli"; tok.mkdir(parents=True)
    (tok / "antigravity-oauth-token").write_text(json.dumps({"token": {"expiry": "2999-01-01T00:00:00Z"}}))
    assert SE.default_runtime(st()) == "gemini"


def test_nothing_usable_keeps_the_old_rule(box):
    assert SE.default_runtime(st()) == "claude"   # first enabled; the login guard explains


def test_no_providers_file_keeps_the_old_rule(box):
    (box.root / "config" / "providers.json").unlink()
    box.installed.add("codex"); codex_logged_in(box)
    assert SE.default_runtime(st()) == "claude"


def test_an_existing_row_keeps_its_runtime(box):
    box.installed.add("codex"); codex_logged_in(box)
    (box.data / "registry.json").write_text(json.dumps({"agents": {"old": {"name": "old", "runtime": "gemini"}}}))
    box.installed.add("agy")
    tok = box.home / ".gemini" / "antigravity-cli"; tok.mkdir(parents=True)
    (tok / "antigravity-oauth-token").write_text(json.dumps({"token": {"expiry": "2999-01-01T00:00:00Z"}}))
    assert M.main(["spawn", "old"]) == 0
    assert reg(box)["old"]["runtime"] == "gemini"


# --- a signed-out claude does not take the seat from a signed-in runtime -------------------
# Workbench finding (2026-10-11): a box signed in to Codex only, with the claude CLI installed but
# never logged in. enabled = ["claude", "gemini", "codex"]; the starter team put gm on CLAUDE, which
# stopped at Claude Code's first-run screen, so pm-<project> and dev-<project> were never made.

def claude_signed_out(box):
    box.installed.update({"claude", "codex"}); codex_logged_in(box)
    box.status["answer"] = False


def test_codex_only_linux_box_with_claude_installed_gets_codex(box):
    claude_signed_out(box)
    assert SE.default_runtime(st()) == "codex"
    assert box.status_calls == [1]
    assert M.main(["spawn", "w9"]) == 0
    assert reg(box)["w9"]["runtime"] == "codex"
    assert box.ran[-1][1]["AGENT_RUNTIME"] == "codex"
    assert M.main(["agent", "create", "w10"]) in (0, 1)
    assert reg(box)["w10"]["runtime"] == "codex"


def test_the_starter_team_gets_codex_on_that_box(box, monkeypatch):
    claude_signed_out(box)
    for name in ("_pm-template.md", "_dev-template.md"):
        shutil.copy(CHECKOUT / "prompts" / name, box.root / "prompts" / name)
    alive = set()
    monkeypatch.setattr(SE, "_run", lambda argv, env=None, cwd=None: alive.add(argv[1]) or box.ran.append((argv, env)) or 0)
    monkeypatch.setattr(SE, "_pane_alive", lambda name: name in alive)
    assert M.main(["starter", "--project", "test"]) == 0
    agents = reg(box)
    assert {agents[n]["runtime"] for n in ("gm", "pm-test", "dev-test")} == {"codex"}


def test_macos_keeps_claude_without_asking(box, monkeypatch):
    claude_signed_out(box)
    monkeypatch.setattr(SE, "_platform", lambda: "darwin")   # the login is in the Keychain
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == []


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"])
def test_an_api_key_or_token_in_the_env_keeps_claude(box, monkeypatch, var):
    claude_signed_out(box)
    monkeypatch.setenv(var, "x")
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == []


@pytest.mark.parametrize("settings", [{"apiKeyHelper": "/usr/local/bin/key"},
                                      {"env": {"ANTHROPIC_API_KEY": "x"}},
                                      {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "x"}}])
def test_a_key_or_token_in_claude_settings_keeps_claude(box, settings):
    claude_signed_out(box)
    (box.home / ".claude").mkdir()
    (box.home / ".claude" / "settings.json").write_text(json.dumps(settings))
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == []


def test_a_credentials_file_keeps_claude(box):
    claude_signed_out(box)
    (box.home / ".claude").mkdir()
    (box.home / ".claude" / ".credentials.json").write_text("{}")
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == []


def test_unreadable_claude_settings_keep_claude(box):
    claude_signed_out(box)
    (box.home / ".claude").mkdir()
    (box.home / ".claude" / "settings.json").write_text("{not json")
    assert SE.default_runtime(st()) == "claude"


def test_auth_status_timeout_or_doubt_keeps_claude(box):
    claude_signed_out(box)
    box.status["answer"] = None              # what _claude_auth_status returns on a timeout/error
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == [1]


def test_a_logged_in_claude_keeps_claude(box):
    claude_signed_out(box)
    box.status["answer"] = True
    assert SE.default_runtime(st()) == "claude"


def test_nothing_else_logged_in_keeps_the_old_rule_without_asking(box):
    box.installed.update({"claude", "codex"})    # codex installed, NOT logged in
    box.status["answer"] = False
    assert SE.default_runtime(st()) == "claude"
    assert box.status_calls == []                # no other runtime proven, so no network call


def test_claude_not_first_is_unchanged(box):
    claude_signed_out(box)
    (box.root / "orchestra.toml").write_text((box.root / "orchestra.toml").read_text().replace(
        'enabled = ["claude", "gemini", "codex"]', 'enabled = ["codex", "claude"]'))
    box.status["answer"] = True
    assert SE.default_runtime(st()) == "codex"
    assert box.status_calls == []


# --- `claude auth status` is read strictly ---------------------------------------------------

class _Done:
    def __init__(self, stdout, rc):
        self.stdout, self.returncode = stdout, rc


def _fake_claude(monkeypatch, answer):
    """subprocess.run answers for `claude ...` only; every other command runs for real."""
    real = subprocess.run

    def fake_run(argv, *a, **k):
        if list(argv)[:1] != ["claude"]:
            return real(argv, *a, **k)
        return answer(argv, k)
    monkeypatch.setattr(SE.subprocess, "run", fake_run)


@pytest.mark.parametrize("stdout,rc,want", [
    ('{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}', 1, False),
    ('{"loggedIn": true, "authMethod": "claude.ai"}', 0, True),
    ('{"loggedIn": false, "authMethod": "oauth_token"}', 1, None),   # false yet naming a method
    ('{"authMethod": "none"}', 1, None),
    ("Not logged in. Run claude /login", 1, None),
    ("", 1, None),
    ("[]", 0, None),
])
def test_auth_status_is_definitive_only_on_its_json(monkeypatch, stdout, rc, want):
    seen = {}

    def answer(argv, k):
        seen["argv"], seen["timeout"] = argv, k.get("timeout")
        return _Done(stdout, rc)
    _fake_claude(monkeypatch, answer)
    assert SE._claude_auth_status() is want
    assert seen["argv"] == ["claude", "auth", "status", "--json"]
    assert seen["timeout"] == SE.CLAUDE_AUTH_STATUS_TIMEOUT_S <= 5


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["claude"], 5), FileNotFoundError("claude"),
                                 OSError("boom")])
def test_auth_status_that_cannot_answer_is_undetermined(monkeypatch, exc):
    def answer(argv, k):
        raise exc
    _fake_claude(monkeypatch, answer)
    assert SE._claude_auth_status() is None


# --- the refusal names the runtime it checked and why -------------------------------------

def test_explicit_codex_that_is_logged_out_is_refused_by_name(box, capsys):
    box.installed.add("codex")                      # installed, no auth.json
    assert M.main(["spawn", "w3", "--runtime", "codex"]) == 2
    err = capsys.readouterr().err
    assert "refusing to spawn w3 on codex" in err and "not logged in" in err and "auth-file-missing" in err
    assert "codex login --device-auth" in err
    assert box.ran == [] and not (box.data / "registry.json").exists()


def test_explicit_runtime_not_installed_is_refused_by_name(box, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    assert M.main(["spawn", "w4", "--runtime", "gemini"]) == 2
    err = capsys.readouterr().err
    assert "refusing to spawn w4 on gemini" in err and "`agy` is not installed" in err
    assert box.ran == []


def test_installed_claude_is_never_refused_on_local_doubt(box):
    box.installed.add("claude")
    assert M.main(["spawn", "w5", "--runtime", "claude"]) == 0


def test_agent_create_uses_the_same_pick_and_refusal(box, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    assert M.main(["agent", "create", "w6"]) in (0, 1)   # 1 = not alive by effect (stubbed pane)
    assert reg(box)["w6"]["runtime"] == "codex"
    assert M.main(["agent", "create", "w7", "--runtime", "gemini"]) == 2
    assert "refusing to spawn w7 on gemini" in capsys.readouterr().err


# --- the "mail it" line works when pasted verbatim ------------------------------------------

@pytest.mark.parametrize("runtime", ["claude", "codex", "gemini"])
def test_the_printed_mail_command_works_verbatim_from_anywhere(tmp_path, monkeypatch, capsys, runtime):
    """gm condition 4: run the PRINTED line as-is, from a neutral cwd, ORCHESTRA_DIR unset."""
    data = tmp_path / "data"; (data / "state").mkdir(parents=True)
    from orchestra_cli import init_cmd
    init_cmd._seed_tasks_db(data / "state" / "tasks.db", "operator")   # what `orchestra init` makes
    fake = type("St", (), {"data_dir": data, "repo_root": CHECKOUT})
    line = SE.mail_hint(fake, f"seat-{runtime}")
    neutral = tmp_path / "elsewhere"; neutral.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("ORCHESTRA_DIR", "ORCH_DIR", "MSG_DB_PATH", "ORCHESTRA_ROOT", "ORCHESTRA_CONFIG")}
    env["HOME"] = str(tmp_path / "home")
    r = subprocess.run(["bash", "-c", line], cwd=neutral, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"printed command failed verbatim:\n{line}\n{r.stderr[-800:]}"
    assert json.loads(r.stdout)["sent"] is True
    assert (data / "state" / "tasks.db").exists(), "the message must land in THIS install's data dir"


def test_spawn_prints_the_verbatim_mail_command(box, capsys):
    box.installed.add("codex"); codex_logged_in(box)
    assert M.main(["spawn", "w8"]) == 0
    out = capsys.readouterr().out
    assert f"ORCHESTRA_DIR={box.data}" in out and str(box.root / "msg_store.py") in out
    assert "python3 msg_store.py send" not in out
