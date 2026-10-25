"""ONE cross-process lock per tmux pane for everything that TYPES into it (gm msg_2291dee3, 2026-10-14).

Before: the gateway's send lock was a threading.Lock (in-process only); the message-router cron
pasted + pressed Enter with no lock at all, so a router delivery and a gateway inject on the same
pane in the same second could glue or split each other's text (06:44:04/05 10-14 was exactly that
coincidence and got lucky). Now every typer holds this flock for its whole type -> Enter -> verify
burst: the gateway (verified_inject, suggest_accept), the message router's deliver, approval_resume
(it calls verified_inject), the idle-stall nudge.

Blocking: the wait is a polling LOCK_NB loop on the CALLER'S thread. The gateway only takes it on
executor threads (never the event loop); the router passes a short wait so a busy pane holds that
row for the next tick instead of stretching the cron past its slot.
"""
import fcntl
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path


class PaneBusy(RuntimeError):
    """Another typer holds this pane past our wait."""


def lock_dir():
    return Path(os.environ.get("PANE_LOCK_DIR") or (Path.home() / "runtime" / "locks"))


def lock_path(session):
    name = str(session or "").lstrip("=").split(":", 1)[0]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "_"
    return lock_dir() / f"pane-{safe}.lock"


@contextmanager
def pane_lock(session, wait_s=8.0, poll_s=0.05):
    p = lock_path(session)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + max(0.0, wait_s)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PaneBusy(f"pane {session} is being typed into by another sender")
                time.sleep(poll_s)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())     # who holds it, for a person reading the file
        except OSError:
            pass
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
