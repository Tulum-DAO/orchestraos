"""check_disk_free.py: a test run refuses to start when the temp dir's or root's disk is low."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_disk_free as C  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_disk_free.py")


def _run(**env):
    return subprocess.run([sys.executable, SCRIPT], env=dict(os.environ, **env), capture_output=True, text=True)


def test_plenty_of_space_passes(tmp_path):
    assert C.low_disk_message([str(tmp_path)], need_gb=0.000001) is None


def test_too_little_space_names_the_path_and_the_override(tmp_path):
    msg = C.low_disk_message([str(tmp_path)], need_gb=10 ** 9)
    assert msg and str(tmp_path) in msg and C.ENV in msg


def test_zero_turns_the_check_off(tmp_path):
    assert C.low_disk_message([str(tmp_path)], need_gb=0) is None


def test_cli_exit_codes():
    assert _run(ORCHESTRA_MIN_FREE_GB="0").returncode == 0
    r = _run(ORCHESTRA_MIN_FREE_GB="1000000000")
    assert r.returncode == 1 and "Not enough free disk space" in r.stderr
    assert _run(ORCHESTRA_MIN_FREE_GB="lots").returncode == 2
    assert _run(ORCHESTRA_MIN_FREE_GB="-1").returncode == 2
