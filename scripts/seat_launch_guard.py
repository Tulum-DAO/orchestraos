"""The test suite's guard against starting a real seat (installed by the root conftest.py for every run).

Refused, from any test: the real `claude` binary (a stand-in named claude under a temp dir is fine), and
`tmux new-session` on the REAL tmux server (a private server via -L/-S, or a temp TMUX_TMPDIR, is fine). Every
process a test starts goes through subprocess.Popen (subprocess.run and asyncio's subprocesses too); processes a
test's own child process starts are not seen here.
"""
import os
import shlex
import shutil
import subprocess
import tempfile

_TMP_ROOTS = tuple({os.path.realpath(tempfile.gettempdir()), "/tmp", os.path.realpath("/tmp")})
_REAL_TMUX_TMPDIR = os.environ.get("TMUX_TMPDIR") or ""


def _under_tmp(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in _TMP_ROOTS)


def real_seat_launch(args, env=None):
    """Why this command would start a real seat from a test, or None. A `tmux` that resolves to a wrapper under a
    temp dir is a test's own private-server shim."""
    try:
        argv = shlex.split(args) if isinstance(args, str) else [os.fsdecode(a) for a in args]
    except (TypeError, ValueError):
        return None
    if not argv:
        return None
    exe = os.path.basename(argv[0])
    if exe == "claude":
        where = argv[0] if os.sep in argv[0] else (shutil.which(argv[0]) or argv[0])
        if not _under_tmp(where):
            return f"a real claude ({where})"
    if exe == "tmux" and any(a in ("new-session", "new") for a in argv[1:]):
        where = argv[0] if os.sep in argv[0] else (shutil.which(argv[0], path=(env or os.environ).get("PATH"))
                                                    or argv[0])
        private = any(a in ("-L", "-S") for a in argv[1:]) or (os.sep in where and _under_tmp(where))
        tmpdir = (env if env is not None else os.environ).get("TMUX_TMPDIR") or ""
        if not private and (not tmpdir or tmpdir == _REAL_TMUX_TMPDIR):
            return "a tmux session on the real tmux server"
    return None


class GuardedPopen(subprocess.Popen):
    def __init__(self, args, *a, **kw):
        why = real_seat_launch(args, kw.get("env"))
        if why:
            raise AssertionError(f"a test tried to start {why}: {args!r}. Use a stand-in (a fake CLI under a "
                                 f"temp dir, a private tmux server).")
        super().__init__(args, *a, **kw)


def install():
    if subprocess.Popen is not GuardedPopen:
        subprocess.Popen = GuardedPopen
