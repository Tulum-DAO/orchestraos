"""The gateway's principal stamp on an Arturo turn, authenticated.

The gateway tells Arturo who is calling (X-Arturo-Principal: "fleet" for the gateway bearer, the
dashboard's path; "device:<id>" for a paired device). "fleet" is believed only when the request also
carries this install's stamp secret (X-Arturo-Stamp): a 0600 file in the data dir that the gateway
creates on its first start and that only processes of this install can read.

Fails closed: no file, an empty file, or a wrong secret = not fleet.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import stat
from pathlib import Path

log = logging.getLogger("arturo-stamp")
_warned = set()

HEADER = "X-Arturo-Stamp"
_NAME = "arturo-stamp.secret"


def path(data_dir) -> Path:
    return Path(data_dir) / "state" / _NAME


def read(data_dir) -> str:
    """The secret, or "" (= no fleet). A file anyone but its owner can read is not a secret: refused."""
    p = path(data_dir)
    try:
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & 0o077:
            if str(p) not in _warned:
                _warned.add(str(p))
                log.warning(f"{p} is mode {oct(mode)}, wider than 0600: ignored (no fleet turns until it is fixed)")
            return ""
        return p.read_text().strip()
    except OSError:
        return ""


def ensure(data_dir) -> str:
    """The secret, made on first use: 0600 before any content is written."""
    have = read(data_dir)
    if have:
        return have
    p = path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{_NAME}.{os.getpid()}.{secrets.token_hex(4)}")
    # O_EXCL: never reuse a stale temp file (it would keep its wider mode); fchmod in case of a umask.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32) + "\n")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return read(data_dir)


def verify(data_dir, presented) -> bool:
    want = read(data_dir)
    got = str(presented or "").strip()
    return bool(want) and bool(got) and hmac.compare_digest(want, got)
