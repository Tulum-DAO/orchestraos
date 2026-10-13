"""Hermetic containment tests for automatic lifecycle runtime gating."""
import json
import os
import subprocess
import sys
import time

import pytest

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECOVERY_SCRIPT = os.path.join(ORCH, "scripts", "agent-recovery.sh")
RUNTIME_HELPER = os.path.join(ORCH, "scripts", "recovery_runtime.py")
sys.path.insert(0, os.path.join(ORCH, "scripts"))

from recovery_runtime import AutomaticRecoveryRefused, automatic_resume_runtime
from runtime_signatures import RuntimeResolutionError


@pytest.mark.parametrize("entry", [
    {},
    {"runtime": ""},
    {"runtime": "rustlang"},
])
def test_automatic_lifecycle_never_defaults_zero_or_unknown_runtime_to_claude(entry):
    with pytest.raises((RuntimeResolutionError, AutomaticRecoveryRefused)):
        automatic_resume_runtime(entry, agent_id="seat")


@pytest.mark.parametrize("runtime", ["codex", "CoDeX", " CODEX "])
def test_automatic_lifecycle_refuses_declared_codex_until_c_r7(runtime):
    with pytest.raises(AutomaticRecoveryRefused, match="C-R7"):
        automatic_resume_runtime({"runtime": runtime}, agent_id="codex-seat")


@pytest.mark.parametrize(("entry", "expected"), [
    ({"runtime": "Claude"}, "claude"),
    ({"runtime": " AGY "}, "gemini"),
    ({"model": "claude-opus-test"}, "claude"),
    ({"model": "gemini-pro"}, "gemini"),
])
def test_automatic_lifecycle_normalizes_and_preserves_supported_runtimes(entry, expected):
    assert automatic_resume_runtime(entry, agent_id="seat") == expected


def test_cli_refuses_codex_without_emitting_a_resume_command(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"agents": {"codex-seat": {"runtime": "CoDeX"}}}))
    proc = subprocess.run(
        [sys.executable, os.path.join(ORCH, "scripts", "recovery_runtime.py"),
         "--registry", str(registry), "--agent", "codex-seat"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 3
    assert "C-R7" in proc.stderr
    assert "codex --yolo resume" not in proc.stdout + proc.stderr


def test_recovery_holder_scan_enumerates_each_runtime_and_detects_codex_holder(tmp_path):
    """Regression for the invalid ``pgrep -x claude agy codex`` invocation.

    The fake pgrep only returns the holder for the exact ``codex`` query.  The
    holder is a real process whose argv contains the SID, so this proves both
    the per-runtime enumeration and the subsequent argv SID match.
    """
    orch = tmp_path / "orch"
    (orch / "state" / "quarantine").mkdir(parents=True)
    (orch / "state" / "recovery-attempts").mkdir()
    (orch / "logs").mkdir()
    (orch / "registry.json").write_text('{"agents": {}}')
    (orch / "state" / "agent-sessions.json").write_text('{}')
    sid = "holder-sid-123"
    holder = subprocess.Popen(["bash", "-c", "sleep 30 & wait", "holder", sid])
    try:
        calls = tmp_path / "pgrep.calls"
        fake_pgrep = tmp_path / "pgrep"
        fake_pgrep.write_text(
            "#!/bin/sh\n"
            "echo \"$@\" >> \"$PGREP_CALLS\"\n"
            "if [ \"$2\" = codex ]; then echo \"$HOLDER_PID\"; exit 0; fi\n"
            "exit 1\n"
        )
        fake_pgrep.chmod(0o755)
        env = os.environ | {
            "ORCHESTRA_DIR": str(orch),
            "PGREP_BIN": str(fake_pgrep),
            "PGREP_CALLS": str(calls),
            "HOLDER_PID": str(holder.pid),
        }
        proc = subprocess.run(
            ["bash", os.path.join(ORCH, "scripts", "agent-recovery.sh"),
             f"--check-sid-held={sid}"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0
        assert calls.read_text().splitlines() == ["-x claude", "-x agy", "-x codex",
                                                  "-x grok", "-x grok-linux-x86_64"]
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def _automatic_lifecycle_world(tmp_path, runtime):
    orch = tmp_path / "orch"
    state = orch / "state"
    (state / "quarantine").mkdir(parents=True)
    (state / "recovery-attempts").mkdir()
    (state / "reincarnation-attempts").mkdir()
    (state / "agent-handoffs").mkdir()
    (orch / "logs").mkdir()
    transcript = tmp_path / "sid.jsonl"
    transcript.write_text('{"ok": true}\n')
    registry = {"agents": {"seat": {
        "runtime": runtime, "status": "online", "always_on": True,
        "machine": "vps", "tmux_session": "seat", "cwd": str(tmp_path),
    }}}
    sessions = {"seat": {
        "resumable": True, "machine": "vps", "session_id": "sid",
        "conversation_path": str(transcript),
    }}
    (orch / "registry.json").write_text(json.dumps(registry))
    (state / "agent-sessions.json").write_text(json.dumps(sessions))
    return orch


def test_recovery_script_skips_codex_before_any_resume_command(tmp_path):
    orch = _automatic_lifecycle_world(tmp_path, "CoDeX")
    env = os.environ | {
        "ORCHESTRA_DIR": str(orch), "RECOVERY_RUNTIME_SCRIPT": RUNTIME_HELPER,
    }
    proc = subprocess.run(["bash", RECOVERY_SCRIPT, "--dry-run"], env=env,
                          capture_output=True, text=True, check=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0
    assert "automatic recovery REFUSED" in combined
    assert "codex --yolo resume" not in combined
    assert "WOULD RECOVER: seat" not in combined


@pytest.mark.parametrize("runtime", [None, "rustlang"])
def test_recovery_script_skips_unresolved_runtime_before_claude_fallback(tmp_path, runtime):
    orch = _automatic_lifecycle_world(tmp_path, runtime)
    env = os.environ | {
        "ORCHESTRA_DIR": str(orch), "RECOVERY_RUNTIME_SCRIPT": RUNTIME_HELPER,
    }
    proc = subprocess.run(["bash", RECOVERY_SCRIPT, "--dry-run"], env=env,
                          capture_output=True, text=True, check=False)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0
    assert "automatic recovery REFUSED" in combined
    assert "WOULD RECOVER: seat" not in combined



def test_recovery_lock_is_per_install_not_box_wide(tmp_path):
    """The run lock lives in the install's own state dir.

    It used to be a fixed /tmp/agent-recovery.lock, so any agent-recovery.sh on the box (another
    install, another checkout's test suite, the cron run) made this one exit 0 silently and these
    tests fail with empty output. A fresh lock in THIS install blocks; one in another install does not.
    """
    orch = _automatic_lifecycle_world(tmp_path, "CoDeX")
    other = _automatic_lifecycle_world(tmp_path / "other", "CoDeX")
    env = os.environ | {
        "ORCHESTRA_DIR": str(orch), "RECOVERY_RUNTIME_SCRIPT": RUNTIME_HELPER,
    }

    (other / "state" / "agent-recovery.lock").write_text("1\n")
    proc = subprocess.run(["bash", RECOVERY_SCRIPT, "--dry-run"], env=env,
                          capture_output=True, text=True, check=False)
    assert "automatic recovery REFUSED" in proc.stdout + proc.stderr
    assert not (orch / "state" / "agent-recovery.lock").exists()   # released on exit

    (orch / "state" / "agent-recovery.lock").write_text("1\n")
    proc = subprocess.run(["bash", RECOVERY_SCRIPT, "--dry-run"], env=env,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert proc.stdout + proc.stderr == ""                          # another run holds it


def test_recovery_lock_one_of_concurrent_runs_proceeds_and_stale_lock_is_replaced(tmp_path):
    """Same install: of several runs started together, exactly one does the work, and a lock older
    than 120 s (a dead run's) is replaced. The lock is an exclusive create (noclobber), so no two
    runs can both take it; that interleaving is microseconds wide and this test cannot force it
    (it passes against the old check-then-write too), so it checks the outcome only."""
    orch = _automatic_lifecycle_world(tmp_path, "CoDeX")
    slow = tmp_path / "slow_runtime.py"      # holds the lock long enough for the others to arrive
    slow.write_text(
        "import sys, time, runpy\n"
        "time.sleep(2)\n"
        f"sys.argv[0] = {RUNTIME_HELPER!r}\n"
        f"runpy.run_path({RUNTIME_HELPER!r}, run_name='__main__')\n"
    )
    env = os.environ | {"ORCHESTRA_DIR": str(orch), "RECOVERY_RUNTIME_SCRIPT": str(slow)}
    procs = [subprocess.Popen(["bash", RECOVERY_SCRIPT, "--dry-run"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
             for _ in range(6)]
    outs = [p.communicate(timeout=60)[0] for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum("automatic recovery REFUSED" in o for o in outs) == 1, outs

    lock = orch / "state" / "agent-recovery.lock"
    lock.write_text("1\n")
    old = time.time() - 300
    os.utime(lock, (old, old))
    proc = subprocess.run(["bash", RECOVERY_SCRIPT, "--dry-run"],
                          env=env | {"RECOVERY_RUNTIME_SCRIPT": RUNTIME_HELPER},
                          capture_output=True, text=True, check=False)
    assert "automatic recovery REFUSED" in proc.stdout + proc.stderr
    assert not lock.exists()
