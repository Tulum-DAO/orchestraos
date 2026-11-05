"""The suite-wide guard (conftest.py): a test can never start the real `claude`, or a tmux session on the real tmux
server. Stand-ins are fine: a fake named claude under a temp dir, a private tmux server."""
import os
import subprocess

import pytest

import seat_launch_guard as C


def test_the_real_claude_is_refused_and_a_stand_in_is_not(tmp_path):
    assert C.real_seat_launch(["claude", "-p"]) and C.real_seat_launch("claude --resume x")
    assert C.real_seat_launch(["/usr/local/bin/claude"])
    fake = tmp_path / "claude"
    assert C.real_seat_launch([str(fake), "-p"]) is None
    with pytest.raises(AssertionError, match="real claude"):
        subprocess.run(["claude", "--version"], capture_output=True)


def test_tmux_new_session_only_on_a_private_server(tmp_path):
    assert C.real_seat_launch(["tmux", "new-session", "-d", "-s", "x"], env={})
    assert C.real_seat_launch(["tmux", "-S", str(tmp_path / "s"), "new-session", "-d"]) is None
    assert C.real_seat_launch(["tmux", "-L", "t", "new", "-d"]) is None
    assert C.real_seat_launch(["tmux", "new-session", "-d"], env={"TMUX_TMPDIR": str(tmp_path)}) is None
    assert C.real_seat_launch(["tmux", "has-session", "-t", "x"]) is None, "only a new session is a launch"
    with pytest.raises(AssertionError, match="real tmux server"):
        subprocess.run(["tmux", "new-session", "-d", "-s", "zz-never"], env={k: v for k, v in os.environ.items()
                                                                         if k != "TMUX_TMPDIR"})
