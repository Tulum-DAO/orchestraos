"""PaneSinkTailer — the live `ring_for`: drains the pipe-pane sink file.

The merged pipe_pane attach runs `cat >> <sink>` (an O_APPEND writer). read_all()
returns the bytes appended since the last drain and TRUNCATES the sink to 0 to
bound growth — safe because O_APPEND repositions every write to EOF, so the pane's
next write lands at offset 0 (no sparse file / stale offset). drop-oldest caps the
returned chunk (the newest `capacity` bytes survive) — a real-time overlay is
intentionally lossy; the WAL is the durable lane. read_all() NEVER raises (a
missing/broken sink is just an empty read — fail-soft, never wedges the tick).

It opens the sink BY NAME on every read (no cached fd), so when the attach sweep rebinds
`<name>.pipe` to a renamed pane's live sink (an atomic symlink swap) the very next read
follows it. On that inode change it reopens FROM THE END: the new file's undrained backlog
is dropped once (truncated, returns b"") instead of replayed as a fresh burst, then new
bytes read normally. The first read of a tailer has no previous inode and reads normally.
"""
import os

DEFAULT_CAPACITY = 256 * 1024        # 256 KiB newest-bytes window per seat


class PaneSinkTailer:
    def __init__(self, path, capacity=DEFAULT_CAPACITY):
        self._path = path
        self._cap = int(capacity)
        self._ident = None            # (st_dev, st_ino) the last read drained

    def read_all(self):
        try:
            fd = os.open(self._path, os.O_RDWR)
        except OSError:
            return b""                # absent/not-yet-created sink => empty read
        try:
            st = os.fstat(fd)
            if not (st.st_mode & 0o170000) == 0o100000:   # only a regular file
                return b""
            ident, prev = (st.st_dev, st.st_ino), self._ident
            self._ident = ident
            if prev is not None and ident != prev:
                os.ftruncate(fd, 0)   # rebound to another file: reopen from the end
                return b""
            data = b""
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data += chunk
            os.ftruncate(fd, 0)       # bound growth (O_APPEND writer -> safe)
        except OSError:
            return b""                # fail-soft: never raise into the tick
        finally:
            os.close(fd)
        if len(data) > self._cap:     # drop-oldest: keep the newest capacity bytes
            data = data[-self._cap:]
        return data
