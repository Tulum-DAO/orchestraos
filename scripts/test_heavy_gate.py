"""scripts/heavy-gate.sh: one machine-wide flock around a heavy gate command (test suites, tsc,
builds, scans), the same lock the pre-push hook takes, so runs queue instead of stacking on the CPU.

Runs the real wrapper with the lock isolated in a tmp dir through ORCHESTRA_GATE_LOCK."""
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "heavy-gate.sh"


def start(tmp_path, *cmd, lock=None):
    env = {**os.environ, "ORCHESTRA_GATE_LOCK": str(lock or tmp_path / "gate.lock")}
    return subprocess.Popen([str(GATE), *cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, cwd=tmp_path)


def run(tmp_path, *cmd, lock=None):
    p = start(tmp_path, *cmd, lock=lock)
    out, err = p.communicate(timeout=60)
    return p.returncode, out, err


def test_the_wrapper_runs_the_command_and_returns_its_exit_code(tmp_path):
    assert GATE.exists() and os.access(GATE, os.X_OK), "scripts/heavy-gate.sh is missing or not executable"
    rc, out, _ = run(tmp_path, "sh", "-c", "echo ran; exit 7")
    assert rc == 7
    assert out == "ran\n"
    assert run(tmp_path, "true")[0] == 0


def test_the_lock_is_held_for_the_whole_life_of_the_command(tmp_path):
    first = start(tmp_path, "sleep", "3")
    pid_file = tmp_path / "gate.lock.pid"
    deadline = time.time() + 10
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    # The wrapper execs the command, so the pid it wrote is the sleep's pid: the one Popen started.
    assert pid_file.read_text().strip() == str(first.pid)
    t0 = time.time()
    rc, out, err = run(tmp_path, "true")
    first.wait(timeout=30)
    assert rc == 0 and first.returncode == 0
    assert f"waiting for the heavy-gate lock held by pid {first.pid}" in err, err
    assert time.time() - t0 >= 2, "the second command ran only after the first released the lock"


def test_no_arguments_is_a_usage_error(tmp_path):
    rc, out, err = run(tmp_path)
    assert rc == 2
    assert "usage: scripts/heavy-gate.sh <command> [args...]" in err


def test_orchestra_gate_lock_overrides_the_lock_path(tmp_path):
    lock = tmp_path / "elsewhere" / "custom.lock"
    lock.parent.mkdir()
    rc, _, _ = run(tmp_path, "true", lock=lock)
    assert rc == 0
    assert lock.exists()
    assert (tmp_path / "elsewhere" / "custom.lock.pid").exists()
    assert not (tmp_path / "gate.lock").exists()
