#!/usr/bin/env python3
"""Refuse to start a test run when the disk it writes to is nearly full.

Test runs write temp dirs, SQLite files and logs under the temp dir. A run that starts with
the disk nearly full can fill it, and a full root disk takes down everything else on the
host (the supervisor, the API, the gateway). This check runs before `npm test` in api/ and
dashboard/ and when pytest loads the root conftest.py, and fails fast with a clear message.

Checks the temp dir ($TMPDIR, else the platform default) and the root filesystem, once per
filesystem. Uses shutil.disk_usage, so it works the same on Linux and macOS.

  ORCHESTRA_MIN_FREE_GB   minimum free space in GB on each checked filesystem (default 5;
                          0 turns the check off)

Exit 0 when there is enough space, 1 when there is not, 2 on a bad setting.
"""
import os
import shutil
import sys
import tempfile

DEFAULT_MIN_FREE_GB = 5.0
ENV = "ORCHESTRA_MIN_FREE_GB"


def min_free_gb():
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_FREE_GB
    value = float(raw)          # ValueError on garbage: main() turns it into exit 2
    if value < 0:
        raise ValueError(f"{ENV} must be >= 0, got {raw!r}")
    return value


def low_disk_message(paths=None, need_gb=None):
    """None when every checked filesystem has at least need_gb free, else a message."""
    need_gb = min_free_gb() if need_gb is None else need_gb
    if need_gb == 0:
        return None
    paths = paths or [tempfile.gettempdir(), os.path.abspath(os.sep)]
    seen, low = set(), []
    for p in paths:
        try:
            dev = os.stat(p).st_dev
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        free_gb = shutil.disk_usage(p).free / 1024 ** 3
        if free_gb < need_gb:
            low.append(f"{p}: {free_gb:.1f} GB free")
    if not low:
        return None
    return (f"Not enough free disk space to run tests (need {need_gb:g} GB on each): "
            + "; ".join(low)
            + f". Free some space (old test temp dirs in the temp dir are a common cause), "
            f"or set {ENV} to a lower value (0 turns this check off).")


def main():
    try:
        msg = low_disk_message()
    except ValueError as e:
        print(f"check_disk_free: bad {ENV}: {e}", file=sys.stderr)
        return 2
    if msg:
        print(f"check_disk_free: {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
