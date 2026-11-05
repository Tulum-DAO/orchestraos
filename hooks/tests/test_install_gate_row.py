"""The supervision gate's Claude Code row (Attention Part 2a, Task 6).

The gate row FAILS OPEN: a hook that crashes, is killed or is missing lets the call run. It carries
no `onFailure` key (an invalid value silently drops the row; "block" would fail closed) and uses the
same `[ -f ] && ... || exit 0` command form as every other row, with a 600 s timeout. The hook speaks
only through stdout JSON on exit 0, which must reach Claude Code unchanged.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import install as H  # noqa: E402

GATE = "hooks/gate-hook.py"
DENY = '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "probe"}}'
USER_PRE = {"type": "command", "command": "bash /home/me/guard.sh"}
USER_STOP = {"type": "command", "command": "node /home/me/my-stop.js"}


@pytest.fixture
def repo(tmp_path):
    """A fake checkout with a harmless stub for every shipped script."""
    root = tmp_path / "repo"
    for row in H.HOOKS:
        p = root / row[2]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("import sys; sys.exit(0)\n" if p.suffix == ".py" else "process.exit(0)\n")
    return root


@pytest.fixture
def tmp_settings(tmp_path):
    p = tmp_path / "claude-config" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"model": "keep", "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [dict(USER_PRE)]}],
        "Stop": [{"hooks": [dict(USER_STOP)]}]}}))
    return p


def _hooks(settings_path):
    return json.loads(settings_path.read_text())["hooks"]


def _gate_rows(settings_path):
    return [h for r in _hooks(settings_path).get("PreToolUse", []) for h in r["hooks"] if GATE in h["command"]]


def _count(settings_path):
    return sum(len(r["hooks"]) for v in _hooks(settings_path).values() for r in v)


def _user_rows(settings_path):
    return [h["command"] for v in _hooks(settings_path).values() for r in v for h in r["hooks"]
            if not H._is_ours(h["command"])]


def _install(settings, repo):
    rep = H.install(settings_path=settings, repo_root=repo, data_dir=repo / "data")
    assert not rep.get("error"), rep
    return rep


def test_gate_row_sits_right_after_the_state_event_pretooluse_row():
    pre = [row[2] for row in H.HOOKS if row[0] == "PreToolUse"]
    assert pre.index(GATE) == pre.index("hooks/state-event-hook.py") + 1
    i = [row[2] if row[0] == "PreToolUse" else None for row in H.HOOKS].index(GATE)
    assert H.HOOKS[i - 1][:3] == ("PreToolUse", "", "hooks/state-event-hook.py")
    assert H.HOOKS[i] == ("PreToolUse", "", GATE, "py", {"timeout": 600})


def test_gate_row_installed_once_with_timeout_and_no_onfailure(tmp_settings, repo):
    _install(tmp_settings, repo)
    gate = _gate_rows(tmp_settings)
    assert len(gate) == 1
    row = gate[0]
    assert row["timeout"] == 600 and H.MARKER in row["command"] and row["type"] == "command"
    assert "onFailure" not in row                       # invalid on 2.1.295 (drops the row); "block" fails closed
    assert set(row) == {"type", "command", "timeout"}
    # the standard fail-open form, identical in shape to every other row
    script = str(repo.resolve() / GATE)
    assert row["command"] == H._command(repo.resolve(), (repo / "data").resolve(), GATE, "py")
    assert row["command"].startswith(f'[ -f "{script}" ] && ORCHESTRA_DIR=')
    assert row["command"].endswith(f"|| exit 0 {H.MARKER}") and " exec " not in row["command"]
    # other rows carry no extra keys
    others = [h for v in _hooks(tmp_settings).values() for r in v for h in r["hooks"]
              if H._is_ours(h["command"]) and GATE not in h["command"]]
    assert others and all(set(h) == {"type", "command"} for h in others)


def _run_gate(settings, body=None, remove=False):
    """Run the installed gate command line through sh, as Claude Code does."""
    cmd = _gate_rows(settings)[0]["command"]
    script = Path(cmd.split('"')[1])
    if remove:
        script.unlink()
    else:
        script.write_text(body)
    return subprocess.run(["sh", "-c", cmd], input='{"tool_name": "Bash"}', capture_output=True, text=True,
                          timeout=30)


@pytest.mark.parametrize("body", [
    "import sys; sys.exit(1)\n",
    "raise RuntimeError('crash')\n",
    "import os, signal; os.kill(os.getpid(), signal.SIGKILL)\n",
], ids=["exit1", "traceback", "sigkill"])
def test_a_failing_gate_fails_open(tmp_settings, repo, body):
    _install(tmp_settings, repo)
    r = _run_gate(tmp_settings, body)
    assert r.returncode == 0 and r.stdout == ""


def test_a_missing_gate_script_fails_open(tmp_settings, repo):
    _install(tmp_settings, repo)
    r = _run_gate(tmp_settings, remove=True)
    assert r.returncode == 0 and r.stdout == ""


def test_a_deny_on_exit_0_reaches_claude_unchanged(tmp_settings, repo):
    _install(tmp_settings, repo)
    r = _run_gate(tmp_settings, f"print({DENY!r})\n")
    assert r.returncode == 0 and json.loads(r.stdout) == json.loads(DENY)


def test_remove_gate_only(tmp_settings, repo):
    _install(tmp_settings, repo)
    before = _count(tmp_settings)
    users = _user_rows(tmp_settings)
    rep = H.remove_gate(settings_path=tmp_settings)
    assert rep["removed"] == 1 and not rep.get("error")
    assert _gate_rows(tmp_settings) == []
    assert _count(tmp_settings) == before - 1
    assert _user_rows(tmp_settings) == users
    assert any("state-event-hook.py" in h["command"] for r in _hooks(tmp_settings)["PreToolUse"] for h in r["hooks"])
    assert json.loads(tmp_settings.read_text())["model"] == "keep"
    assert H.remove_gate(settings_path=tmp_settings)["removed"] == 0          # idempotent
    assert H.status(settings_path=tmp_settings, repo_root=repo)["gate"] == "absent"


def test_remove_gate_cli(tmp_settings, repo):
    _install(tmp_settings, repo)
    before = _count(tmp_settings)
    assert H.main(["--settings", str(tmp_settings), "--remove-gate"]) == 0
    assert _gate_rows(tmp_settings) == [] and _count(tmp_settings) == before - 1


def test_remove_gate_leaves_a_broken_file_alone(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text("{not json")
    assert "not valid JSON" in H.remove_gate(settings_path=s)["error"] and s.read_text() == "{not json"


def test_reinstall_is_idempotent_and_keeps_user_rows(tmp_settings, repo):
    _install(tmp_settings, repo)
    first = _hooks(tmp_settings)
    rep = _install(tmp_settings, repo)
    assert rep["removed"] == len(H.HOOKS) and rep["installed"] == len(H.HOOKS)
    assert _hooks(tmp_settings) == first
    assert len(_gate_rows(tmp_settings)) == 1
    assert sorted(_user_rows(tmp_settings)) == sorted([USER_PRE["command"], USER_STOP["command"]])
    # a re-install after remove_gate puts the gate back once
    H.remove_gate(settings_path=tmp_settings)
    _install(tmp_settings, repo)
    assert len(_gate_rows(tmp_settings)) == 1 and _hooks(tmp_settings) == first


def test_remove_takes_the_gate_row_out(tmp_settings, repo):
    _install(tmp_settings, repo)
    rep = H.remove(settings_path=tmp_settings)
    assert rep["removed"] == len(H.HOOKS)
    assert _gate_rows(tmp_settings) == []
    assert sorted(_user_rows(tmp_settings)) == sorted([USER_PRE["command"], USER_STOP["command"]])


def test_status_reports_gate(tmp_settings, repo):
    assert H.status(settings_path=tmp_settings, repo_root=repo)["gate"] == "absent"
    _install(tmp_settings, repo)
    st = H.status(settings_path=tmp_settings, repo_root=repo)
    assert st["gate"] == "installed" and st["missing"] == [] and f"PreToolUse:{GATE}" in st["installed"]
    H.remove_gate(settings_path=tmp_settings)
    st = H.status(settings_path=tmp_settings, repo_root=repo)
    assert st["gate"] == "absent" and st["missing"] == [f"PreToolUse:{GATE}"]
