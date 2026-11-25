"""RED-first: the registered hook command must hand the hook's exit code to Claude Code unchanged.

The row form was `[ -f X ] && run X || exit 0`. When a PreToolUse guard exits 2 (block), the `|| exit 0`
branch runs and Claude Code sees 0, so every exit-2 guard installed this way (git_state_guard,
worktree_path_guard, send_shape_guard, body_backtick_guard) never blocked anything. Found by effect on
2026-11-25: a guard logged REFUSE and the command ran anyway.

Each test runs the REGISTERED command string through `sh -c`, the way Claude Code runs it.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import install as H  # noqa: E402

PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}})


def _run(cmd):
    return subprocess.run(["sh", "-c", cmd], input=PAYLOAD, capture_output=True, text=True)


def _stub(tmp_path, rel, body):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return rel


def test_a_py_guard_that_exits_2_blocks_through_the_registered_command(tmp_path):
    rel = _stub(tmp_path, "hooks/blocker.py",
                "import sys\nsys.stderr.write('BLOCKED by stub')\nsys.exit(2)\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "py"))
    assert r.returncode == 2, f"exit 2 must reach Claude Code, got {r.returncode}"
    assert "BLOCKED by stub" in r.stderr


def test_a_bash_guard_that_exits_2_blocks_through_the_registered_command(tmp_path):
    rel = _stub(tmp_path, "hooks/blocker.sh", "echo 'BLOCKED by stub' >&2\nexit 2\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "bash"))
    assert r.returncode == 2, f"exit 2 must reach Claude Code, got {r.returncode}"


def test_a_missing_script_still_fails_open(tmp_path):
    r = _run(H._command(tmp_path, tmp_path / "data", "hooks/gone.py", "py"))
    assert r.returncode == 0 and r.stdout == ""


def test_a_crashing_hook_still_fails_open(tmp_path):
    rel = _stub(tmp_path, "hooks/crash.py", "raise SystemExit(1)\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "py"))
    assert r.returncode == 0


def test_a_json_decision_hook_is_unchanged(tmp_path):
    decision = json.dumps({"decision": "block", "reason": "digest"})
    rel = _stub(tmp_path, "hooks/decider.py", f"print({decision!r})\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "py"))
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"decision": "block", "reason": "digest"}


def test_an_allowing_hook_exits_0_and_sees_the_data_dir(tmp_path):
    rel = _stub(tmp_path, "hooks/env.py", "import os\nprint(os.environ['ORCHESTRA_DIR'])\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "py"))
    assert r.returncode == 0 and r.stdout.strip() == str(tmp_path / "data")


def test_every_shipped_row_uses_the_exit_code_preserving_form(tmp_path):
    settings = tmp_path / "settings.json"
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    s = json.loads(settings.read_text())
    cmds = [h["command"] for rules in s["hooks"].values() for r in rules for h in r["hooks"]
            if H._is_ours(h["command"])]
    assert cmds
    for c in cmds:
        assert "|| exit 0" not in c, c


def test_a_bash_hook_that_does_not_parse_fails_open_instead_of_blocking(tmp_path):
    rel = _stub(tmp_path, "hooks/broken.sh", "if then fi (\n")       # bash exits 2 on a syntax error
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "bash"))
    assert r.returncode == 0


def test_a_bash_guard_block_keeps_its_stderr(tmp_path):
    rel = _stub(tmp_path, "hooks/blocker.sh", "echo 'BLOCKED by stub' >&2\nexit 2\n")
    r = _run(H._command(tmp_path, tmp_path / "data", rel, "bash"))
    assert r.returncode == 2 and "BLOCKED by stub" in r.stderr


def _old_form_settings(tmp_path):
    settings = tmp_path / "settings.json"
    old = f'[ -f "{REPO}/hooks/send_shape_guard.py" ] && python3 "{REPO}/hooks/send_shape_guard.py" {H.OLD_FORM}'
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": old}]}]}}))
    return settings


def test_status_reports_old_form_rows_as_stale(tmp_path):
    settings = _old_form_settings(tmp_path)
    assert H.status(settings_path=settings, repo_root=REPO)["stale"] == ["PreToolUse"]


def test_reinstall_replaces_old_form_rows(tmp_path):
    settings = _old_form_settings(tmp_path)
    H.install(settings_path=settings, repo_root=REPO, data_dir=tmp_path / "data")
    text = settings.read_text()
    assert H.OLD_FORM not in text
    assert H.status(settings_path=settings, repo_root=REPO)["stale"] == []
