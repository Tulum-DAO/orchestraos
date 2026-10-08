"""The gateway's principal stamp on an Arturo turn, authenticated.

The gateway tells Arturo who is calling (X-Arturo-Principal: "fleet" for the gateway bearer, the
dashboard's path; "device:<id>" for a paired device). "fleet" is believed only when the request also
carries this install's stamp secret (X-Arturo-Stamp): a 0600 file in the data dir that the gateway
creates on its first start and that only processes of this install can read.

Fails closed: no file, an empty file, or a wrong secret = not fleet.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

HEADER = "X-Arturo-Stamp"
_NAME = "arturo-stamp.secret"


def path(data_dir) -> Path:
    return Path(data_dir) / "state" / _NAME


def read(data_dir) -> str:
    try:
        return path(data_dir).read_text().strip()
    except OSError:
        return ""


def ensure(data_dir) -> str:
    """The secret, made on first use: 0600 before any content is written."""
    have = read(data_dir)
    if have:
        return have
    p = path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{_NAME}.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_urlsafe(32) + "\n")
    os.replace(tmp, p)
    return read(data_dir)


def verify(data_dir, presented) -> bool:
    want = read(data_dir)
    got = str(presented or "").strip()
    return bool(want) and bool(got) and hmac.compare_digest(want, got)
