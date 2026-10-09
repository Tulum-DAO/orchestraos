"""RED-first (issue #92): spawn-agent.sh printed `Agent ... spawned successfully` in the same
output that said `Injection FAILED twice ... agent may be idle without a task`, and never
checked that a --model belongs to the declared runtime. Both guards live in
scripts/spawn_guards.sh, sourced by spawn-agent.sh, and are exercised here the way
test_spawn_model_verify.py exercises its helper: bash -c with the helper sourced."""
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "spawn_guards.sh")


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f"set -euo pipefail; SCRIPT_DIR='{os.path.dirname(HERE)}'; "
                                          f"err() {{ echo \"ERR: $*\" >&2; }}; source '{HELPER}'; {script}"],
                          capture_output=True, text=True, timeout=30)


def test_inject_or_fail_returns_1_and_says_not_successful_when_injection_fails():
    # the wrapper REPORTS and returns; the caller decides to exit (spawn-agent.sh: `|| exit 1`)
    r = _bash('inject_prompt() { return 1; }; rc=0; inject_or_fail "seat-x" "hello" || rc=$?; echo "rc=$rc"')
    assert r.returncode == 0, r.stderr
    assert "rc=1" in r.stdout
    assert "NOT spawned successfully" in r.stderr and "seat-x" in r.stderr


def test_inject_or_fail_is_silent_and_0_when_injection_succeeds():
    r = _bash('inject_prompt() { return 0; }; rc=0; inject_or_fail "seat-x" "hello" || rc=$?; echo "rc=$rc"')
    assert "rc=0" in r.stdout and "NOT spawned" not in r.stderr


def test_refuse_model_mismatch_returns_3_for_a_claude_model_on_a_codex_seat():
    r = _bash('rc=0; refuse_model_mismatch "codex" "claude-sonnet-5" "codex-helper" || rc=$?; echo "rc=$rc"')
    assert "rc=3" in r.stdout
    assert "claude-sonnet-5" in r.stderr and "codex" in r.stderr


def test_refuse_model_mismatch_passes_matching_empty_and_unknown_models():
    for rt, m in (("codex", "gpt-5.6-terra"), ("claude", "claude-opus-4-8[1m]"), ("gemini", ""),
                  ("claude", "mystery-model")):
        r = _bash(f'rc=0; refuse_model_mismatch "{rt}" "{m}" "seat" || rc=$?; echo "rc=$rc"')
        assert "rc=0" in r.stdout, (rt, m, r.stderr)


# ---------------------------------------------------------------- inject_prompt, by effect
# A new user's first gm sat with its init prompt typed in the composer and NOT sent, and the spawn
# said success: the old check only asked whether the text was visible, and unsent text is visible.
# These drive the real inject_prompt -> scripts/boot_inject.py against a fake composer that loses
# the first N Enters, on a PRIVATE tmux server (its own TMUX_TMPDIR, $TMUX unset), never the
# operator's.

import shutil
import tempfile

import pytest

FAKE_TUI = os.path.join(HERE, "fixtures", "lossy_enter_tui.py")
PROMPT = "You are lab-gm. Read /tmp/agent-init-lab-gm.md and follow all instructions in it."


@pytest.fixture
def lab(tmp_path):
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    sock_dir = tempfile.mkdtemp(prefix="ibl.", dir="/tmp")    # short: a socket path has a size limit
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.update(TMUX_TMPDIR=sock_dir, ORCHESTRA_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")

    def tmux(*args):
        return subprocess.run(["tmux", *args], env=env, capture_output=True, text=True)

    def start(drop):
        tmux("new-session", "-d", "-s", "lab-gm", "-x", "200", "-y", "40", f"python3 {FAKE_TUI} {drop}")
        return tmux, env
    yield start
    tmux("kill-session", "-t", "=lab-gm")
    shutil.rmtree(sock_dir, ignore_errors=True)


def _inject(env):
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail; SCRIPT_DIR='{os.path.dirname(HERE)}'; "
                       f"err() {{ echo \"ERR: $*\" >&2; }}; source '{HELPER}'; sleep 0.5; "
                       f"runtime=claude; rc=0; inject_prompt lab-gm '{PROMPT}' || rc=$?; echo rc=$rc"],
        env=env, capture_output=True, text=True, timeout=120)


def test_a_lost_enter_is_pressed_again_and_the_prompt_is_submitted(lab):
    tmux, env = lab(1)
    r = _inject(env)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    screen = tmux("capture-pane", "-p", "-t", "=lab-gm:").stdout
    assert "● working on it" in screen
    assert screen.count("You are lab-gm.") == 1          # one paste, never a second copy


def test_a_clean_submit_is_one_paste_on_the_first_enter(lab):
    tmux, env = lab(0)
    r = _inject(env)
    assert "rc=0" in r.stdout and "enter-1" in r.stderr, r.stdout + r.stderr
    assert tmux("capture-pane", "-p", "-t", "=lab-gm:").stdout.count("You are lab-gm.") == 1
# the stuck case (and every lost-Enter count) is exercised on boot_inject itself, by effect, in
# scripts/test_boot_inject.py; these two prove spawn's inject_prompt is wired to it.
