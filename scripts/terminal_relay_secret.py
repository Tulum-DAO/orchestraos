"""Shared credential for the local gateway -> API terminal relay.

The gateway creates this 0600 reference once under the Orchestra data directory;
the API reads the same file.  It is deliberately neither a browser credential nor
a value that travels in a URL or an Origin header.
"""
import os
import secrets
from pathlib import Path


def path() -> Path:
    explicit = os.environ.get("TERMINAL_RELAY_SECRET_FILE")
    if explicit:
        return Path(explicit)
    base = os.environ.get("ORCHESTRA_DIR") or str(Path.home() / ".orchestra")
    return Path(base) / "state" / "terminal-relay-secret"


def read() -> str:
    try:
        return path().read_text().strip()
    except OSError:
        return ""


def ensure() -> str:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    value = read()
    if value:
        return value
    value = secrets.token_urlsafe(32)
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another local service won the creation race. Read its value instead of
        # minting a second credential that the API cannot verify.
        return read()
    with os.fdopen(fd, "w") as out:
        out.write(value + "\n")
    return value
