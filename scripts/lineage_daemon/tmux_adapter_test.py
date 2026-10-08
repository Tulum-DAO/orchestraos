"""RED tests for the concrete Tmux adapter feeding pipe_pane.AttachSweep.

AttachSweep requires an injected object providing:
  list_sessions() -> [(name, session_id)]
  pipe_pane(session, sink)
Only test fakes existed; this is the production adapter. The command runner is
injectable so parsing/argv are tested WITHOUT a live tmux server.
"""
import pytest

from .tmux_adapter import Tmux


class FakeRunner:
    """Records argv and returns scripted (rc, stdout) per invocation."""
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if not self._script:
            return (0, "")
        return self._script.pop(0)


def test_list_sessions_parses_name_and_session_id_pairs():
    runner = FakeRunner([(0, "gm $1\ncodex-dev-1 $2\ngemini-gm $3\n")])
    tmux = Tmux(runner=runner)
    assert tmux.list_sessions() == [
        ("gm", "$1"), ("codex-dev-1", "$2"), ("gemini-gm", "$3")]
    # uses a single list-sessions call with both fields in the format string
    assert runner.calls[0][0:2] == ["tmux", "list-sessions"]
    fmt = runner.calls[0][-1]
    assert "#{session_name}" in fmt and "#{session_id}" in fmt


def test_list_sessions_no_server_returns_empty():
    # `tmux list-sessions` exits non-zero with no server; must not raise.
    runner = FakeRunner([(1, "no server running on /tmp/tmux-1001/default\n")])
    assert Tmux(runner=runner).list_sessions() == []


def test_list_sessions_skips_malformed_lines():
    runner = FakeRunner([(0, "gm $1\n\n   \nbadline_without_id\nfoo $9\n")])
    assert Tmux(runner=runner).list_sessions() == [("gm", "$1"), ("foo", "$9")]


def test_pane_map_maps_window0_pane0_id_and_pid_per_session_in_one_call():
    # the batched per-slow-tick resolve: ONE `tmux list-panes -a` mapping each
    # session's agent pane (window 0 / pane 0) to (pane_id, pane_pid).
    runner = FakeRunner([(0,
        "gm 0 0 %1 100\n"
        "codex-dev-1 0 0 %2 200\n"
        "gm 1 0 %8 999\n"          # a second window of gm -> NOT the agent pane, skip
        "gemini-gm 0 1 %9 888\n"   # pane 1 -> not the agent pane, skip
    )])
    tmux = Tmux(runner=runner)
    assert tmux.pane_map() == {"gm": ("%1", 100), "codex-dev-1": ("%2", 200)}
    assert runner.calls[0][0:3] == ["tmux", "list-panes", "-a"]   # single batched call
    assert len(runner.calls) == 1
    fmt = runner.calls[0][-1]
    assert "#{pane_id}" in fmt and "#{pane_pid}" in fmt        # both keys in one call


def test_pane_map_no_server_returns_empty():
    runner = FakeRunner([(1, "no server running\n")])
    assert Tmux(runner=runner).pane_map() == {}


def test_pane_map_skips_malformed_and_non_integer_pids():
    runner = FakeRunner([(0, "gm 0 0 %1 100\nbadline\nfoo 0 0 %3 notanint\nbar 0 0 %4 300\n")])
    assert Tmux(runner=runner).pane_map() == {"gm": ("%1", 100), "bar": ("%4", 300)}


def test_pipe_pane_uses_injection_safe_attach_command():
    runner = FakeRunner([(0, "0\n"), (0, "")])        # pane_pipe=0, then the attach
    tmux = Tmux(runner=runner)
    assert tmux.pipe_pane("gm", "/home/testuser/.orchestra/realtime/panes/gm.pipe") is True
    assert runner.calls[0] == ["tmux", "display-message", "-p", "-t", "gm", "#{pane_pipe}"]
    argv = runner.calls[1]
    # reuses pipe_pane.attach_command shape: session is a tmux argv element,
    # the sink is shell-quoted inside the `cat >>` body (never interpolated raw).
    assert argv[0:4] == ["tmux", "pipe-pane", "-t", "gm"]
    assert "-o" not in argv                            # -o TOGGLES: it would close a live pipe
    assert argv[-1].startswith("cat >> ")
    assert "gm.pipe" in argv[-1]


def test_pipe_pane_leaves_an_existing_pipe_alone():
    """An already-piped pane (ours from a previous telemetryd run, or an operator's own
    logger) must not be closed (-o toggles) or replaced (a bare pipe-pane replaces)."""
    runner = FakeRunner([(0, "1\n")])
    tmux = Tmux(runner=runner)
    assert tmux.pipe_pane("v", "/home/testuser/.orchestra/realtime/panes/v.pipe") is False
    assert runner.calls == [["tmux", "display-message", "-p", "-t", "v", "#{pane_pipe}"]]


def test_pipe_pane_raises_when_pane_state_is_unreadable():
    """No evidence of the pane's pipe state must not be read as 'unpiped' (that would
    replace someone's pipe). The sweep isolates the error to this one seat."""
    runner = FakeRunner([(1, "")])
    with pytest.raises(RuntimeError):
        Tmux(runner=runner).pipe_pane("gm", "/tmp/x.pipe")
    assert len(runner.calls) == 1


def test_pipe_pane_refuses_unsafe_session_name():
    from .realtime.pipe_pane import UnsafeSessionName
    runner = FakeRunner([(0, "")])
    tmux = Tmux(runner=runner)
    with pytest.raises(UnsafeSessionName):
        tmux.pipe_pane("gm; rm -rf ~", "/tmp/whatever.pipe")
    # nothing was executed for the unsafe name
    assert runner.calls == []
