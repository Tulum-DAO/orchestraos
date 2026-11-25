"""RED tests — PaneSinkTailer: the live `ring_for` that drains the pipe-pane sink.

The merged pipe_pane attach runs `cat >> <sink>` (an O_APPEND writer). The fast
tick drains new bytes and TRUNCATES the sink to bound growth — safe precisely
because O_APPEND repositions every write to EOF, so after truncate-to-0 the next
pane write lands at offset 0 (no sparse file, no stale offset). A real-time overlay
is intentionally lossy (the WAL is the durable lane); drop-oldest keeps the newest.
"""
import os

from .pane_sink_tailer import PaneSinkTailer


def test_absent_sink_returns_empty(tmp_path):
    t = PaneSinkTailer(str(tmp_path / "nope.pipe"))
    assert t.read_all() == b""


def test_reads_bytes_then_truncates(tmp_path):
    p = tmp_path / "gm.pipe"
    p.write_bytes(b"hello tokens")
    t = PaneSinkTailer(str(p))
    assert t.read_all() == b"hello tokens"
    assert os.path.getsize(p) == 0            # truncated to bound growth
    p.write_bytes(b"more")                     # (write_bytes truncates+writes here)
    assert t.read_all() == b"more"


def test_drop_oldest_cap_keeps_newest(tmp_path):
    p = tmp_path / "gm.pipe"
    p.write_bytes(b"abcdefgh")
    t = PaneSinkTailer(str(p), capacity=4)
    assert t.read_all() == b"efgh"            # newest capacity bytes survive


def test_truncate_is_safe_for_an_append_mode_writer(tmp_path):
    # emulate `cat >> sink`: an O_APPEND fd whose writes always seek to EOF.
    p = tmp_path / "gm.pipe"
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, b"AB")
        t = PaneSinkTailer(str(p))
        assert t.read_all() == b"AB"          # drains + truncates to 0
        os.write(fd, b"Z")                     # append writer: lands at new EOF (0)
        assert t.read_all() == b"Z"           # no corruption, no sparse gap
    finally:
        os.close(fd)


def test_read_all_never_raises_on_a_directory(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    assert PaneSinkTailer(str(d)).read_all() == b""   # fail-soft, never raises


def test_a_rebound_name_reopens_from_the_end_dropping_the_new_files_backlog(tmp_path):
    """The sweep rebinds `gm.pipe` (a symlink swap) to a renamed pane's live sink. That
    file may hold a backlog nobody drained; replaying it would read as a fresh burst. On
    an inode change the tailer drains-and-drops once, then reads new bytes normally."""
    p = tmp_path / "gm.pipe"
    p.write_bytes(b"old")
    t = PaneSinkTailer(str(p))
    assert t.read_all() == b"old"
    live = tmp_path / "gm-g6.pipe"
    live.write_bytes(b"undrained backlog")
    tmp = tmp_path / "gm.pipe.tmp"
    os.symlink(str(live), str(tmp))
    os.replace(str(tmp), str(p))                 # the sweep's atomic rebind
    assert t.read_all() == b""                   # reopened from the end
    with open(live, "ab") as fh:
        fh.write(b"fresh")
    assert t.read_all() == b"fresh"
