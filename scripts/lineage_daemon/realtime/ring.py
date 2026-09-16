"""Crash-isolated non-blocking sink primitives (pipe-pane gotcha 2).

The pipe-pane sink writes into a tiny O_NONBLOCK drop-oldest ring. Rationale: a
wedged full-pipe sink can stall writes to the PANE ITSELF (backpressure through
tmux) — which would freeze the live agent. So the sink NEVER blocks and NEVER
raises: on a full ring it drops the OLDEST bytes; on a full/broken pipe fd it
drops the write. Heavy work (ANSI-strip, tokenize, court-scrub) runs in a
SEPARATE consumer that drains the ring — never inline in the pipe process.
"""
import errno
import os


class RingBuffer:
    """A bounded byte ring. append() never blocks/raises; when the buffer would
    exceed `capacity`, the OLDEST bytes are evicted so the NEWEST always survive
    (drop-oldest). read_all() drains."""

    def __init__(self, capacity):
        self._cap = int(capacity)
        self._buf = bytearray()

    def append(self, data):
        if not data:
            return
        self._buf.extend(data)
        if len(self._buf) > self._cap:
            # keep only the newest `capacity` bytes (drop-oldest)
            del self._buf[:len(self._buf) - self._cap]

    def read_all(self):
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def __len__(self):
        return len(self._buf)


_DROP_ERRNOS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EPIPE, errno.EBADF}


def write_nonblocking(fd, data):
    """Write to a non-blocking fd, returning bytes written. On EAGAIN/EWOULDBLOCK
    (full pipe) or EPIPE (reader gone), DROP the write and return 0 — never block,
    never raise, so the sink can never stall the pane."""
    try:
        return os.write(fd, data)
    except BlockingIOError:
        return 0
    except BrokenPipeError:
        return 0
    except OSError as e:
        if e.errno in _DROP_ERRNOS:
            return 0
        raise
