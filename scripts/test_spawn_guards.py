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
# These drive the real inject_prompt against a fake composer that loses the first N Enters, on a
# private tmux server (never the operator's).

import shutil
import uuid

import pytest

FAKE_TUI = os.path.join(HERE, "fixtures", "lossy_enter_tui.py")
PROMPT = "You are gm. Read /tmp/agent-init-gm.md and follow all instructions in it."


def _lab(drop_enters):
    sock = f"inject-lab-{uuid.uuid4().hex[:8]}"
    subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "seat", "-x", "200", "-y", "40",
                    f"python3 {FAKE_TUI} {drop_enters}"], check=True)
    return sock


def _inject(sock):
    script = (f"tmux() {{ command tmux -L {sock} \"$@\"; }}; warn() {{ echo \"WARN: $*\" >&2; }}; "
              f"INJECT_POLL_S=0.3 INJECT_POLLS=4; runtime=claude; sleep 0.5; rc=0; "
              f"inject_prompt seat '{PROMPT}' || rc=$?; echo rc=$rc")
    return _bash(script)


def _screen(sock):
    return subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", "seat"],
                          capture_output=True, text=True).stdout


@pytest.fixture
def lab():
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    socks = []

    def make(drop):
        socks.append(_lab(drop))
        return socks[-1]
    yield make
    for s in socks:
        subprocess.run(["tmux", "-L", s, "kill-server"], capture_output=True)


def _composer(screen):
    return [line for line in screen.splitlines() if line.startswith("❯\xa0") or line.startswith("❯ ")][-1]


def test_a_lost_enter_is_pressed_again_and_the_prompt_is_submitted(lab):
    sock = lab(1)
    r = _inject(sock)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert "pressing Enter again" in r.stderr
    screen = _screen(sock)
    assert "● working on it" in screen
    assert PROMPT[:24] not in _composer(screen).replace("\xa0", " ")


def test_a_prompt_that_never_submits_fails_the_spawn_instead_of_reporting_success(lab):
    sock = lab(99)
    r = _inject(sock)
    assert "rc=1" in r.stdout, r.stdout + r.stderr
    assert "NOT SUBMITTED" in r.stderr
    # exactly one copy in the box: a lost Enter is never answered with a second paste
    assert _screen(sock).count("You are gm.") == 1


def test_a_clean_submit_is_one_paste_and_no_retry(lab):
    sock = lab(0)
    r = _inject(sock)
    assert "rc=0" in r.stdout and "WARN" not in r.stderr, r.stdout + r.stderr
    assert _screen(sock).count("You are gm.") == 1
