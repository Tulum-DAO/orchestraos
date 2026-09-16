"""RED tests — spawn-time pipe-pane attach helper (Decision 3).

spawn-agent.sh calls this at session-create (the latency-optimization half of the
BOTH-asymmetric placement; the daemon attach-SWEEP is the rotation invariant-
keeper). All command-injection defense + shell-quoting stays in Python (reused
from pipe_pane): a validated [A-Za-z0-9_-] session token, never raw interpolation.
Fail-soft: an attach failure NEVER blocks the spawn.
"""
import os

from .spawn_pipe_attach import attach


class Runner:
    def __init__(self, rc=0, boom=False):
        self.calls = []
        self._rc = rc
        self._boom = boom
    def __call__(self, argv):
        self.calls.append(list(argv))
        if self._boom:
            raise OSError("tmux exploded")
        return (self._rc, "")


_LIVE = lambda: True
_DOWN = lambda: False


def test_attach_skips_when_daemon_not_live(tmp_path):
    # INERT-safety: attaching pipe-pane with no consumer grows the sink file
    # unbounded. Attach ONLY when telemetryd is live (status.json fresh). The
    # daemon's own attach-sweep re-attaches within one slow tick regardless, so
    # gating loses no capture.
    r = Runner()
    ok = attach("gm", runner=r, sink_dir=str(tmp_path), is_live=_DOWN)
    assert ok is False
    assert r.calls == []                    # nothing attached while the daemon is down


def test_attach_builds_injection_safe_argv(tmp_path):
    r = Runner()
    ok = attach("gm", runner=r, sink_dir=str(tmp_path), is_live=_LIVE)
    assert ok is True
    argv = r.calls[0]
    assert argv[0:5] == ["tmux", "pipe-pane", "-o", "-t", "gm"]
    assert argv[-1].startswith("cat >> ")
    assert argv[-1].rstrip().endswith("gm.pipe'") or "gm.pipe" in argv[-1]


def test_attach_creates_sink_dir(tmp_path):
    sink = tmp_path / "panes"
    attach("gm", runner=Runner(), sink_dir=str(sink), is_live=_LIVE)
    assert sink.is_dir()


def test_attach_refuses_unsafe_session_name_without_executing(tmp_path):
    r = Runner()
    ok = attach("gm; rm -rf ~", runner=r, sink_dir=str(tmp_path), is_live=_LIVE)
    assert ok is False
    assert r.calls == []                    # nothing executed for an unsafe name


def test_attach_is_fail_soft_on_runner_error(tmp_path):
    # a wedged/failed tmux must NOT raise into the spawn path.
    ok = attach("gm", runner=Runner(boom=True), sink_dir=str(tmp_path), is_live=_LIVE)
    assert ok is False                       # reported, not raised


def test_spawn_agent_sh_wires_the_attach_fail_soft():
    # structural guard (reviewer ask): the shell calls the Python helper — which
    # owns the injection defense — right after session-create, fail-soft (`||`),
    # passing the tmux session name (never interpolating it into a shell command).
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sh = open(os.path.join(root, "spawn-agent.sh")).read()
    lines = sh.splitlines()
    new_i = next(i for i, l in enumerate(lines) if "tmux new-session -d -s" in l)
    attach_i = next(i for i, l in enumerate(lines) if "spawn_pipe_attach.py" in l)
    assert attach_i > new_i and attach_i - new_i < 15    # right after new-session
    call = lines[attach_i]
    assert '"$tmux_name"' in call                        # the validated session name
    # fail-soft: the invocation must not abort the spawn on failure
    assert "||" in call or "||" in lines[attach_i + 1]
