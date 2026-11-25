"""RED tests for the pipe-pane attach mechanics (audit criteria #4 + #5) and the
command-injection gotchas (#1 sink constant, #4 sh -c template).

#4: pipe-pane DROPS on session recreation (rotation/respawn) — spawn-time attach
    ALONE is insufficient; the daemon attach-SWEEP is the invariant-keeper. The
    sweep re-attaches when a session's tmux identity changes under a stable name.
#5: fail-isolation — a pipe-pane attach error on ONE seat skips only that seat;
    it must NEVER wedge the sweep / fleet / beat.
"""
import os

import pytest

from lineage_daemon.realtime import pipe_pane as _pp
from lineage_daemon.realtime.pipe_pane import (
    AttachSweep, sink_path, attach_command, UnsafeSessionName, SINK_DIR)


@pytest.fixture(autouse=True)
def _tmp_default_sink_dir(tmp_path, monkeypatch):
    """No test sweep may touch the live sink dir: the sweep persists its pane->sink
    bindings and rebinds names there. Point the constructor default at a tmp dir."""
    d = tmp_path / "panes"
    d.mkdir()
    init = AttachSweep.__init__

    def _init(self, tmux, *, sink_dir=None, **kw):
        init(self, tmux, sink_dir=sink_dir or str(d), **kw)
    monkeypatch.setattr(AttachSweep, "__init__", _init)


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


def test_attach_command_never_toggles():
    """`pipe-pane -o` is a TOGGLE: on an already-piped pane it CLOSES the pipe (measured,
    tmux 3.4). The attach must never carry it; the adapter checks #{pane_pipe} first."""
    assert "-o" not in attach_command("gm", sink_path("gm"))


# --- a renamed session's pipe still writes the OLD name's sink ---------------

def test_renamed_session_is_unbound_and_not_reattached():
    """Blue-green swap: the green is spawned as `gm-g6` (piped into gm-g6.pipe) and tmux
    RENAMES it to `gm`. Its pipe keeps writing gm-g6.pipe, so gm.pipe never grows. The
    sweep must not call pipe-pane on it (the pane is already piped) and must report the
    name as UNBOUND, so the deriver never reads its silent gm.pipe as idle."""
    tmux = FakeTmux([("gm-g6", "$5")])
    sweep = AttachSweep(tmux)
    sweep.sweep()
    assert not sweep.is_unbound("gm-g6")
    tmux.sessions = [("gm", "$5")]                # renamed; same tmux session-id
    sweep.sweep()
    assert tmux.attach_calls == ["gm-g6"]         # no pipe-pane under the new name
    # This fake never creates gm-g6.pipe, so there is no file gm.pipe could be rebound to:
    # unbound. With a live sink it is rebound instead (test_rename_mid_generation_...).
    assert sweep.is_unbound("gm")
    assert not sweep.is_unbound("other")          # a name never seen is not unbound


# --- (a) follow the pane, not the name ---------------------------------------

class FileTmux:
    """A tmux stand-in over REAL tmp files. Each pane is keyed by its session-id; an
    attach opens an O_APPEND writer on the sink path exactly like `cat >> <sink>` (it
    follows a symlink, as the shell redirect does). pipe_pane returns False for an
    already-piped pane (tmux_adapter.Tmux semantics). `writers()` is the injected
    /proc writer scan: the (dev, ino) of every file a pane's pipe holds open."""
    def __init__(self, sessions, server="srv-1"):
        self.sessions = list(sessions)
        self.fds = {}                       # sid -> O_APPEND fd
        self.attach_calls = []
        self.server = server

    def list_sessions(self):
        return list(self.sessions)

    def server_id(self):
        return self.server

    def pipe_pane(self, session, sink):
        sid = dict(self.sessions)[session]
        if sid in self.fds:
            return False
        self.fds[sid] = os.open(sink, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        self.attach_calls.append(session)
        return True

    def pipe_out(self, sid, data):
        os.write(self.fds[sid], data)

    def writers(self):
        return {(st.st_dev, st.st_ino) for st in (os.fstat(fd) for fd in self.fds.values())}

    def close(self):
        for fd in self.fds.values():
            os.close(fd)


def _same_file(a, b):
    sa, sb = os.stat(a), os.stat(b)
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def test_rename_mid_generation_keeps_the_sink_and_root_pipe_resolves_to_it(tmp_path):
    """gm's ruling: a session renamed mid-generation (`gm-g6` -> `gm`) keeps the SAME
    sink, and `gm.pipe` resolves to the growing file, so the seat ring for `gm` (which
    opens by name) drains the pane's live output. Not unbound, no second pipe-pane."""
    from lineage_daemon.pane_sink_tailer import PaneSinkTailer
    d = tmp_path / "sinks"; d.mkdir()
    (d / "gm.pipe").write_bytes(b"")                  # the old blue's dead sink
    tmux = FileTmux([("gm", "$1"), ("gm-g6", "$5")])
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()                                     # both piped under their own names
    ring = PaneSinkTailer(str(d / "gm.pipe"))         # the seat's ring opens by NAME
    tmux.sessions = [("gm", "$5")]                    # swap: blue gone, green renamed gm
    sweep.sweep()
    assert tmux.attach_calls == ["gm", "gm-g6"]       # no pipe-pane under the new name
    assert not sweep.is_unbound("gm")
    assert _same_file(d / "gm.pipe", d / "gm-g6.pipe")   # gm.pipe resolves to the live sink
    tmux.pipe_out("$5", b"green is streaming")
    assert ring.read_all() == b"green is streaming"
    tmux.close()


def test_binding_survives_a_restart_after_the_rename(tmp_path):
    """tmux cannot report a pipe's target, so the {session-id: sink} map is persisted: a
    NEW sweep (telemetryd restart) after the rename still knows where gm's pipe goes."""
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("gm-g6", "$5")])
    AttachSweep(tmux, sink_dir=str(d), writers=lambda: set()).sweep()
    tmux.sessions = [("gm", "$5")]
    fresh = AttachSweep(tmux, sink_dir=str(d), writers=lambda: set())   # restart
    fresh.sweep()
    assert not fresh.is_unbound("gm")
    assert _same_file(d / "gm.pipe", d / "gm-g6.pipe")
    tmux.close()


def test_a_new_tmux_server_discards_the_persisted_map(tmp_path):
    """Session-ids restart at $0 on a new tmux server: a map from the old server would
    bind a new session to a stranger's sink. A server change drops it."""
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("gm-g6", "$5")])
    AttachSweep(tmux, sink_dir=str(d), writers=lambda: set()).sweep()
    other = FileTmux([("gm", "$5")], server="srv-2")   # same id, different server
    s2 = AttachSweep(other, sink_dir=str(d), writers=lambda: set())
    s2.sweep()
    assert other.attach_calls == ["gm"]               # treated as a fresh pane
    assert not os.path.islink(d / "gm.pipe")
    tmux.close(); other.close()


def test_unknown_piped_session_is_unbound_without_evidence(tmp_path):
    """A pane already piped (pipe_pane False) whose session-id is in no map, and whose
    own sink has no live writer: its pipe goes somewhere unknown -> unbound."""
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("gm", "$5")])
    tmux.fds["$5"] = os.open(str(d / "elsewhere.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    assert sweep.is_unbound("gm")
    tmux.close()


def test_unknown_piped_session_adopts_its_own_sink_when_a_writer_holds_it(tmp_path):
    """Spawn-time attach (spawn_pipe_attach) and a pre-fix telemetryd both pipe into
    `<name>.pipe`; a live writer on that file is the evidence -> bound, not unbound."""
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("planner", "$3")])
    tmux.fds["$3"] = os.open(str(d / "planner.pipe"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    assert not sweep.is_unbound("planner")
    tmux.close()


def test_unknown_renamed_root_adopts_its_orphan_green_sink(tmp_path):
    """The first run after this fix lands with no map: gm was renamed from gm-g140 by a
    swap under the old code, so its pipe writes gm-g140.pipe (live writer, no live session
    of that name) while gm.pipe is silent. Exactly one such orphan green sink -> bind it."""
    d = tmp_path / "sinks"; d.mkdir()
    (d / "gm.pipe").write_bytes(b"")
    tmux = FileTmux([("gm", "$9")])
    tmux.fds["$9"] = os.open(str(d / "gm-g140.pipe"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    assert not sweep.is_unbound("gm")
    assert _same_file(d / "gm.pipe", d / "gm-g140.pipe")
    tmux.close()


def test_rebind_moves_a_live_blue_sink_aside_instead_of_orphaning_it(tmp_path):
    """rotate_agent renames the blue `gm` -> an archive name BEFORE renaming the green to
    `gm`. The blue still pipes into the REAL file gm.pipe; rebinding gm.pipe must not
    unlink it from under the blue: it is moved aside and the archive name follows it."""
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("gm", "$1"), ("gm-g6", "$5")])
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    tmux.sessions = [("gm-old", "$1"), ("gm", "$5")]
    sweep.sweep()
    assert _same_file(d / "gm.pipe", d / "gm-g6.pipe")
    tmux.pipe_out("$1", b"blue")
    assert (d / "gm-old.pipe").read_bytes() == b"blue"
    tmux.close()


def test_gone_session_ids_are_pruned_from_the_persisted_map(tmp_path):
    import json
    d = tmp_path / "sinks"; d.mkdir()
    tmux = FileTmux([("gm", "$1"), ("dead", "$2")])
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    tmux.sessions = [("gm", "$1")]
    sweep.sweep()
    saved = json.loads((d / _pp.BINDINGS_FILE).read_text())
    assert set(saved["sinks"]) == {"$1"}
    tmux.close()


def test_proc_writer_scan_reads_fds_that_point_into_the_sink_dir(tmp_path):
    """The default writer evidence: /proc/<pid>/fd/<n> links into the sink dir (a fake
    proc tree here). A link elsewhere is ignored; an unreadable proc entry is skipped."""
    d = tmp_path / "sinks"; d.mkdir()
    sink = d / "gm.pipe"; sink.write_bytes(b"")
    other = tmp_path / "elsewhere.log"; other.write_bytes(b"")
    proc = tmp_path / "proc"
    (proc / "123" / "fd").mkdir(parents=True)
    (proc / "456").mkdir()                         # no fd dir: skipped, never raises
    (proc / "self").mkdir()                        # not a pid
    os.symlink(str(sink), str(proc / "123" / "fd" / "1"))
    os.symlink(str(other), str(proc / "123" / "fd" / "2"))
    st = os.stat(sink)
    assert _pp._proc_writer_inodes(str(d), proc_root=str(proc)) == {(st.st_dev, st.st_ino)}


def test_a_map_with_no_server_id_is_not_trusted_once_the_server_is_known(tmp_path):
    """A map saved without a server id (a tmux adapter with no server_id, or a stray
    fixture map) cannot prove its session-ids belong to this server: dropped."""
    import json
    d = tmp_path / "sinks"; d.mkdir()
    (d / _pp.BINDINGS_FILE).write_text(json.dumps({"server": None, "sinks": {"$5": "seat5.pipe"}}))
    tmux = FileTmux([("gm", "$5")])
    sweep = AttachSweep(tmux, sink_dir=str(d), writers=tmux.writers)
    sweep.sweep()
    assert tmux.attach_calls == ["gm"]                # a fresh pane, not seat5's
    assert not sweep.is_unbound("gm")
    tmux.close()
