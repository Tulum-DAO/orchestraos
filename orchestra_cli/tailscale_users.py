"""<data dir>/state/tailscale-users: the Tailscale logins the web terminal and the voice call let in.

Two readers decide who gets in, and both read this file (or ORCHESTRA_TAILSCALE_USERS, which wins):
dashboard-proxy.js via dashboard-tailscale-users.js (the web terminal) and api/src/lib/tailscale-identity.ts
(the voice call, and each login's role). No install issues an orchestra_session cookie, so with no file
nobody gets in: a fresh install refused its own owner ("[ws] refused /ws/terminal: authenticated session
required"). `orchestra init`, `orchestra tailscale-users --seed` and install-client.sh step 9 write this
machine's own Tailscale login as admin when the file is ABSENT. A file that is present, even an empty one,
is never touched: someone wrote it.

The format is the readers': `login [role]` per line, `#` starts a comment, a login is a word with an `@` in
it, the role is admin or member (none = admin).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

FILE_REL = Path("state") / "tailscale-users"
ENV = "ORCHESTRA_TAILSCALE_USERS"
ROLE_WORDS = ("admin", "member")
HEADER = ("# Tailscale logins the web terminal and the voice call let in: one per line, then its role\n"
          "# (admin or member; none = admin). Written once by the installer for this machine's own login.\n")
# One word with an @ and nothing the readers split on (whitespace, comma) or cut (#).
_LOGIN = re.compile(r"^[^\s,#@]+@[^\s,#]+$")


def users_path(data_dir) -> Path:
    return Path(data_dir) / FILE_REL


def allowed_logins(raw: str) -> list:
    """Which logins the readers let in. A MIRROR of allowedLogins() in dashboard-tailscale-users.js (the
    web terminal's reader), whose login rule parseTailscaleUsers() in api/src/lib/tailscale-identity.ts
    shares: cut `#` comments, split on whitespace and commas, a login is a word with an @ (lowercased),
    role words are never logins, each login once. test_tailscale_users.py runs the real JS reader on the
    file this module writes, so the two cannot drift apart unseen."""
    out: list = []
    for line in re.split(r"\r?\n", str(raw or "")):
        for tok in re.split(r"[\s,]+", re.sub(r"#.*$", "", line)):
            s = tok.strip().lower()
            if s and "@" in s and s not in ROLE_WORDS and s not in out:
                out.append(s)
    return out


def self_login(status_json: str) -> tuple:
    """(login, None) or (None, why) from `tailscale status --json`: Self.UserID -> User[id].LoginName."""
    try:
        doc = json.loads(status_json or "")
    except ValueError:
        return None, "Tailscale's status could not be read"
    if not isinstance(doc, dict):
        return None, "Tailscale's status could not be read"
    if doc.get("BackendState") in ("NeedsLogin", "NoState"):
        return None, "this server is not signed in to Tailscale"
    me, users = doc.get("Self"), doc.get("User")
    uid = me.get("UserID") if isinstance(me, dict) else None
    # Only THIS node's owner. Every peer's owner is in the User map too, so a Self with no UserID (or 0)
    # is "unknown", never "pick one": that would make some peer's owner admin of the web shell.
    if isinstance(uid, bool) or not isinstance(uid, (int, str)) or str(uid).strip() in ("", "0"):
        uid = None
    entry = users.get(str(uid)) if uid is not None and isinstance(users, dict) else None
    name = entry.get("LoginName") if isinstance(entry, dict) else None
    login = name.strip() if isinstance(name, str) else ""
    if not login:
        return None, "Tailscale did not say which login this server is signed in with"
    if not _LOGIN.match(login):
        # A tagged server (signed in with an auth key) reports "tagged-devices": no person to let in.
        return None, f"this server's Tailscale login ({login[:40]}) is not a person's login"
    return login, None


def read_status() -> str:
    """`tailscale status --json`, read only. Raises OSError / SubprocessError when it cannot answer."""
    exe = shutil.which("tailscale")
    if not exe:
        raise FileNotFoundError("tailscale is not installed")
    r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0 and not r.stdout.strip():
        raise subprocess.SubprocessError(f"tailscale status exited {r.returncode}")
    return r.stdout


def hand_fix(path: Path, login: Optional[str] = None) -> str:
    """A copy-paste shell line; both the line and the path are quoted (a login may hold a ', a path a space)."""
    return f"echo {shlex.quote((login or 'you@example.com') + ' admin')} >> {shlex.quote(str(path))}"


def _one_line(text: str, n: int = 80) -> str:
    return " ".join(str(text).split())[:n]


@dataclass
class SeedResult:
    did: bool
    detail: str
    login: Optional[str] = None


def seed(data_dir, status: Callable[[], str] = read_status) -> SeedResult:
    """Write this machine's own Tailscale login as admin when the file is absent. Never overwrites, never
    raises: `orchestra init` (and so `orchestra upgrade`) must not fail on this step."""
    path = users_path(data_dir)
    try:
        return _seed(path, status)
    except Exception as e:  # noqa: BLE001 — an unwritable state/ dir, an odd status shape: a hint, not a crash
        return SeedResult(False, _not_written(path, f"it could not be written ({type(e).__name__}: {_one_line(e)})"))


def _not_written(path: Path, why: str) -> str:
    return (f"not written: {why}. The web terminal and the voice call let nobody in "
            f"until you add your Tailscale login: {hand_fix(path)}")


def _seed(path: Path, status: Callable[[], str]) -> SeedResult:
    if os.path.lexists(path):
        return SeedResult(False, f"present, kept as is ({path})")
    try:
        login, why = self_login(status())
    except FileNotFoundError:
        login, why = None, "tailscale is not installed"
    except (OSError, subprocess.SubprocessError) as e:
        login, why = None, f"`tailscale status --json` did not answer ({_one_line(e, 60)})"
    if not login:
        return SeedResult(False, _not_written(path, why))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL: a file that appeared since the check above is still never overwritten.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SeedResult(False, f"present, kept as is ({path})")
    with os.fdopen(fd, "w") as f:
        f.write(f"{HEADER}{login} admin\n")
    return SeedResult(True, f"wrote {path}: {login} admin (the web terminal and the voice call let this login in)",
                      login)


def status_of(raw: Optional[str], env_value: Optional[str], path: Path) -> tuple:
    """(ok, detail, kind) for doctor. kind: env | env-empty | file | absent | empty. Like both readers, a
    non-empty ORCHESTRA_TAILSCALE_USERS is used INSTEAD of the file, even when it names no login."""
    if env_value:
        n = len(allowed_logins(env_value))
        if n:
            return True, f"{ENV} lets in {n} login(s)", "env"
        return False, f"{ENV} is set but lists no login (it overrides {path}): nobody is let in", "env-empty"
    if raw is None:
        return False, f"{path} is missing: the web terminal and the voice call let nobody in", "absent"
    logins = allowed_logins(raw)
    if not logins:
        return False, f"{path} lists no login: the web terminal and the voice call let nobody in", "empty"
    return True, f"{path} lets in {len(logins)} login(s)", "file"


def cmd_tailscale_users(ns) -> int:
    from . import settings as S
    st = S.load_settings()
    path = users_path(st.data_dir)
    if getattr(ns, "seed", False):
        res = seed(st.data_dir)
        print(f"tailscale-users: {res.detail}")
        return 0 if res.did or res.detail.startswith("present") else 1
    try:
        raw = path.read_text()
    except FileNotFoundError:
        raw = None
    ok, detail, _ = status_of(raw, os.environ.get(ENV), path)
    print(detail)
    for login in allowed_logins(os.environ.get(ENV) or raw or "") if ok else ():
        print(f"  {login}")
    return 0 if ok else 1
