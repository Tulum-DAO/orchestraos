"""Every seat's tmux session gets mouse on (operator finding, 2026-10-08).

With mouse mode off the terminal turns the scroll wheel into arrow keys; in Claude Code Up
walks the prompt history shared by every seat in the same directory, so a scroll and an Enter
re-sent ANOTHER seat's init prompt. The behaviour test runs a REAL tmux on a private server:
TMUX is removed from the environment and TMUX_TMPDIR points at tmp_path, so nothing here can
reach a live tmux server (asserted before anything else runs).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "tmux_session_defaults.sh"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


@pytest.fixture()
def private_tmux(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env["TMUX_TMPDIR"] = str(tmp_path)
    sock = tmp_path / f"tmux-{os.getuid()}" / "default"

    def run(*args, check=True):
        return subprocess.run(["tmux", *args], env=env, capture_output=True, text=True,
                              check=check, timeout=10)

    run("-f", "/dev/null", "new-session", "-d", "-s", "seat-x")
    # FENCE: the server we just started must be the private one.
    assert sock.exists(), "tmux did not start on the private socket; refusing to go on"
    try:
        yield env, run
    finally:
        subprocess.run(["tmux", "-S", str(sock), "kill-server"], env=env,
                       capture_output=True, timeout=10)


def _helper(env, session):
    return subprocess.run(["bash", "-c", f"set -euo pipefail; source '{HELPER}'; "
                           f"orch_tmux_session_defaults \"$1\"; echo rc=$?", "_", session],
                          env=env, capture_output=True, text=True, timeout=10)


def test_the_seat_session_gets_mouse_on_and_nothing_else_does(private_tmux):
    env, run = private_tmux
    run("new-session", "-d", "-s", "seat-xy")          # a prefix-sharing neighbour
    assert run("show-options", "-t", "=seat-x:", "-v", "mouse").stdout.strip() in ("", "off")
    out = _helper(env, "seat-x")
    assert out.stdout.strip() == "rc=0", out.stderr
    assert run("show-options", "-t", "=seat-x:", "-v", "mouse").stdout.strip() == "on"
    # per SESSION: the global option and the neighbour are untouched
    assert run("show-options", "-g", "-v", "mouse").stdout.strip() == "off"
    assert run("show-options", "-t", "=seat-xy:", "-v", "mouse").stdout.strip() in ("", "off")


def test_a_gone_session_never_prefix_matches_a_neighbour(private_tmux):
    """seat-x died and seat-xy lives: the defaults for seat-x must not land on seat-xy."""
    env, run = private_tmux
    run("new-session", "-d", "-s", "seat-xy")
    run("kill-session", "-t", "=seat-x")
    assert _helper(env, "seat-x").stdout.strip() == "rc=0"
    assert run("show-options", "-t", "=seat-xy:", "-v", "mouse").stdout.strip() in ("", "off")


def test_a_missing_session_never_fails_the_caller(private_tmux):
    """Fail-soft under set -euo pipefail: spawn and recovery must not abort on it."""
    env, _ = private_tmux
    assert _helper(env, "no-such-seat").stdout.strip() == "rc=0"
    assert _helper(env, "").stdout.strip() == "rc=0"


def test_every_seat_session_site_applies_the_defaults():
    """Derived from the code, not a list: every `tmux new-session` that creates a seat in the
    two seat launchers is followed (within 6 lines) by orch_tmux_session_defaults, and each
    launcher sources the helper."""
    for rel in ("spawn-agent.sh", "scripts/agent-recovery.sh"):
        lines = (ROOT / rel).read_text().splitlines()
        assert any("tmux_session_defaults.sh" in l and l.lstrip().startswith("source") for l in lines), rel
        sites = [i for i, l in enumerate(lines)
                 if re.match(r"\s*tmux new-session\b", l)]
        assert sites, f"{rel}: no new-session found; did the launcher move?"
        for i in sites:
            window = "\n".join(lines[i + 1:i + 7])
            assert 'orch_tmux_session_defaults "$tmux_name"' in window, f"{rel}:{i + 1}"
