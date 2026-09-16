"""RED tests for the crash-isolated non-blocking sink ring (pipe-pane gotcha 2).

A wedged full-pipe sink can stall writes to the PANE itself — so the sink must be
a tiny O_NONBLOCK drop-oldest ring: it NEVER blocks and NEVER raises on a full or
broken sink; heavy work (ANSI-strip, tokenize) happens in a SEPARATE consumer
reading the ring, never inline in the pipe process.
"""
import errno
import os

from lineage_daemon.realtime.ring import RingBuffer, write_nonblocking


def test_ring_drops_oldest_when_full_never_raises():
    ring = RingBuffer(capacity=10)
    ring.append(b"aaaaa")
    ring.append(b"bbbbb")
    ring.append(b"ccccc")     # would exceed 10 -> oldest dropped
    data = ring.read_all()
    assert len(data) <= 10
    assert data.endswith(b"ccccc")      # newest always retained
    assert b"aaaaa" not in data         # oldest evicted


def test_ring_append_larger_than_capacity_keeps_tail():
    ring = RingBuffer(capacity=4)
    ring.append(b"0123456789")
    assert ring.read_all() == b"6789"   # only the newest `capacity` bytes


def test_read_all_drains_the_ring():
    ring = RingBuffer(capacity=16)
    ring.append(b"hello")
    assert ring.read_all() == b"hello"
    assert ring.read_all() == b""       # drained


def test_write_nonblocking_drops_on_would_block_never_stalls():
    # a full O_NONBLOCK pipe raises EAGAIN; write_nonblocking must swallow it and
    # report 0 written (drop), NEVER block or raise — so the pane never stalls.
    r, w = os.pipe()
    try:
        os.set_blocking(w, False)
        # fill the pipe buffer until it would block
        filled = 0
        try:
            while filled < (1 << 22):
                filled += os.write(w, b"x" * 65536)
        except OSError as e:
            assert e.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
        # now a write MUST NOT raise; it drops and returns 0
        n = write_nonblocking(w, b"more")
        assert n == 0
    finally:
        os.close(r); os.close(w)


def test_write_nonblocking_on_broken_pipe_drops():
    # reader closed => EPIPE. The sink must drop, not crash the pipe process.
    r, w = os.pipe()
    os.set_blocking(w, False)
    os.close(r)
    n = write_nonblocking(w, b"data")
    assert n == 0
    os.close(w)


def test_write_nonblocking_happy_path_writes():
    r, w = os.pipe()
    try:
        os.set_blocking(w, False)
        n = write_nonblocking(w, b"hi")
        assert n == 2
        assert os.read(r, 8) == b"hi"
    finally:
        os.close(r); os.close(w)
