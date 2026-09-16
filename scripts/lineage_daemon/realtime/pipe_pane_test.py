"""RED tests for the pipe-pane attach mechanics (audit criteria #4 + #5) and the
command-injection gotchas (#1 sink constant, #4 sh -c template).

#4: pipe-pane DROPS on session recreation (rotation/respawn) — spawn-time attach
    ALONE is insufficient; the daemon attach-SWEEP is the invariant-keeper. The
    sweep re-attaches when a session's tmux identity changes under a stable name.
#5: fail-isolation — a pipe-pane attach error on ONE seat skips only that seat;
    it must NEVER wedge the sweep / fleet / beat.
"""
import pytest

from lineage_daemon.realtime.pipe_pane import (
    AttachSweep, sink_path, attach_command, UnsafeSessionName, SINK_DIR)


class FakeTmux:
    """Injectable tmux: a list of (name, session_id) 'live' sessions and a
    recorder of pipe-pane attach calls. `fail_on` names raise (fault injection)."""
    def __init__(self, sessions, fail_on=()):
        self.sessions = list(sessions)          # [(name, sid)]
        self.fail_on = set(fail_on)
        self.attach_calls = []                  # [name]

    def list_sessions(self):
        return list(self.sessions)

    def pipe_pane(self, session, sink):
        if session in self.fail_on:
            raise RuntimeError(f"pipe-pane failed for {session}")
        self.attach_calls.append(session)


# --- gotcha #1/#4: constant sink path + command-injection safety ------------

def test_sink_path_is_a_constant_template_prefix():
    p1 = sink_path("gm")
    p2 = sink_path("acme-merge")
    assert p1.startswith(SINK_DIR) and p2.startswith(SINK_DIR)
    assert p1 != p2                              # per-seat, but constant prefix


def test_sink_path_refuses_agent_controlled_metacharacters():
    # never interpolate an agent-controlled string into the sh -c command.
    for evil in ("a; rm -rf ~", "$(touch pwned)", "a`id`", "../../etc/passwd",
                 "a b", "a\nb", "a|b"):
        with pytest.raises(UnsafeSessionName):
            sink_path(evil)


def test_attach_command_shell_quotes_the_sink():
    import shlex
    sink = sink_path("gm")
    argv = attach_command("gm", sink)
    assert "pipe-pane" in argv
    assert "gm" in argv                            # session is a tmux argv element, not via sh -c
    # the sh -c PIPE body must route the sink through shlex.quote (idempotent for
    # a safe path, but the ONLY thing standing between a path and the shell).
    pipe_body = argv[-1]
    assert pipe_body == f"cat >> {shlex.quote(sink)}"


# --- audit criterion #4: sweep survives a simulated rotation ----------------

def test_sweep_attaches_new_sessions():
    tmux = FakeTmux([("gm", "$1"), ("acme-merge", "$2")])
    sweep = AttachSweep(tmux)
    sweep.sweep()
    assert set(tmux.attach_calls) == {"gm", "acme-merge"}


def test_sweep_does_not_reattach_stable_sessions():
    tmux = FakeTmux([("gm", "$1")])
    sweep = AttachSweep(tmux)
    sweep.sweep()
    sweep.sweep()                                 # nothing changed
    assert tmux.attach_calls == ["gm"]            # attached exactly once


def test_sweep_reattaches_on_session_recreation_rotation():
    # THE rotation gotcha: pipe-pane silently drops when the session is recreated
    # (same NAME, new tmux session-id). The sweep must detect the identity change
    # and RE-ATTACH — spawn-time attach alone would leave it dark.
    tmux = FakeTmux([("gm", "$1")])
    sweep = AttachSweep(tmux)
    sweep.sweep()
    assert tmux.attach_calls == ["gm"]
    # rotation recreates gm's session -> new session-id, pipe-pane dropped
    tmux.sessions = [("gm", "$7")]
    sweep.sweep()
    assert tmux.attach_calls == ["gm", "gm"]      # re-attached after recreation


def test_sweep_prunes_dead_sessions():
    tmux = FakeTmux([("gm", "$1"), ("dead", "$2")])
    sweep = AttachSweep(tmux)
    sweep.sweep()
    tmux.sessions = [("gm", "$1")]                # 'dead' gone
    sweep.sweep()
    assert "dead" not in sweep.attached
    # gm not re-attached (stable)
    assert tmux.attach_calls == ["gm", "dead"]


# --- audit criterion #5: fail-isolation -------------------------------------

def test_attach_error_on_one_seat_skips_only_that_seat():
    tmux = FakeTmux([("a", "$1"), ("boom", "$2"), ("c", "$3")], fail_on=("boom",))
    sweep = AttachSweep(tmux)
    result = sweep.sweep()                         # MUST NOT raise
    assert set(tmux.attach_calls) == {"a", "c"}    # healthy seats attached
    assert "boom" in result["errors"]              # error recorded, isolated
    assert "boom" not in sweep.attached            # not marked attached


def test_a_failing_seat_is_retried_next_sweep_not_permanently_dark():
    tmux = FakeTmux([("boom", "$1")], fail_on=("boom",))
    sweep = AttachSweep(tmux)
    sweep.sweep()
    assert "boom" not in sweep.attached
    tmux.fail_on = set()                           # fault clears
    sweep.sweep()
    assert "boom" in sweep.attached                # recovered on retry


def test_sweep_never_propagates_and_returns_summary():
    tmux = FakeTmux([("boom", "$1")], fail_on=("boom",))
    sweep = AttachSweep(tmux)
    r = sweep.sweep()
    assert set(r.keys()) >= {"attached", "errors", "pruned"}
