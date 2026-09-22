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
