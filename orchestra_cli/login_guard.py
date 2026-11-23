"""The provider-login preservation guard.

Operator rule: an upgrade never logs anyone out of an AI provider. `orchestra upgrade`, `orchestra
init` and `orchestra install-unit` fingerprint every provider login file that exists for this user
BEFORE their first step and again after every step; a login that vanished, was emptied, lost its
login record, now names a different account, or had its mode opened up fails the command loudly,
naming the file and the step. The guard never repairs anything: it never writes, copies or moves a
login file (copying a Claude login is itself a logout: the refresh token rotates and the copy kills
one holder). `orchestra doctor` reads the stamp the guard leaves (hashes only, mode 0600, never a
token) and reports `logins:<provider>` intact since the last upgrade, or "no <provider> login found".

What a login file is, per provider (env-resolved, the same way each CLI resolves it):
  claude   $CLAUDE_CONFIG_DIR (else ~/.claude)/.credentials.json   the OAuth tokens (Linux)
           $CLAUDE_CONFIG_DIR/.claude.json (else ~/.claude.json)    oauthAccount: which account
           (macOS keeps the tokens in the Keychain: no file, nothing to fingerprint, never touched)
  codex    $CODEX_HOME (else ~/.codex)/auth.json                   tokens / OPENAI_API_KEY
  gemini   $GEMINI_CLI_HOME (else ~)/.gemini/oauth_creds.json, .gemini/antigravity-cli/antigravity-oauth-token
  grok     $GROK_HOME (else ~/.grok)/auth.json, credentials.json

A token the CLI refreshes on its own rewrites the file while the command runs (a live Claude seat
does this every hour), so a changed hash with the login record and the account intact is a NOTE,
not a failure. Everything here reads a login file only to hash it and to check which top-level key
it carries; no value is kept, logged or printed.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROVIDERS = ("claude", "codex", "gemini", "grok")
# The env vars that decide WHERE each CLI keeps its login. A seat, a unit or a recovery that runs with
# a different value than the shell the operator logged in from is effectively logged out.
LOGIN_ENV = ("HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME", "GEMINI_CLI_HOME")
STAMP_REL = Path("state") / "login-guard.json"


@dataclass(frozen=True)
class LoginFile:
    provider: str
    path: Path
    kind: str                      # "json" | "raw"
    login_keys: tuple = ()         # a JSON file holds a login when ANY of these top-level keys is present
    identity: tuple = ()           # dotted paths whose (hashed) value identifies the account


def login_files(env: Optional[dict] = None, home: Optional[str] = None) -> list:
    """Every provider login file this user's CLIs would read, resolved from `env` (os.environ by default).
    Existence is not checked here; `snapshot` records present/absent."""
    env = os.environ if env is None else env
    home = Path(home or env.get("HOME") or os.path.expanduser("~"))
    claude_dir = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    claude_json = claude_dir / ".claude.json" if env.get("CLAUDE_CONFIG_DIR") else home / ".claude.json"
    codex_home = Path(env.get("CODEX_HOME") or home / ".codex")
    gemini_home = Path(env.get("GEMINI_CLI_HOME") or home) / ".gemini"
    grok_home = Path(env.get("GROK_HOME") or home / ".grok")
    return [
        LoginFile("claude", claude_dir / ".credentials.json", "json", ("claudeAiOauth",)),
        LoginFile("claude", claude_json, "json", ("oauthAccount",),
                  ("oauthAccount.accountUuid", "oauthAccount.emailAddress")),
        LoginFile("codex", codex_home / "auth.json", "json", ("tokens", "OPENAI_API_KEY"), ("tokens.account_id",)),
        LoginFile("gemini", gemini_home / "oauth_creds.json", "json", ("refresh_token", "access_token")),
        LoginFile("gemini", gemini_home / "antigravity-cli" / "antigravity-oauth-token", "raw"),
        LoginFile("grok", grok_home / "auth.json", "json"),
        LoginFile("grok", grok_home / "credentials.json", "json"),
    ]


def _dig(obj, dotted: str):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _hash_value(v) -> Optional[str]:
    if v in (None, "", {}, []):
        return None
    return hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint(lf: LoginFile) -> dict:
    """present, kind (file/dir/other/missing), size, mtime_ns, mode, sha256, login (True: holds a login
    record; False: present but holds none; None: unreadable/not applicable), identity (hash of the
    account field, or None). Never keeps or returns file content."""
    fp = {"provider": lf.provider, "present": False, "kind": "missing", "size": None, "mtime_ns": None,
          "mode": None, "sha256": None, "login": None, "identity": None}
    try:
        st = os.lstat(lf.path)
    except OSError:
        return fp
    fp["present"] = True
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(lf.path)
        except OSError:
            fp["kind"] = "broken-symlink"
            return fp
    if stat.S_ISDIR(st.st_mode):
        fp["kind"] = "dir"
        return fp
    if not stat.S_ISREG(st.st_mode):
        fp["kind"] = "other"
        return fp
    fp["kind"] = "file"
    fp.update(size=st.st_size, mtime_ns=st.st_mtime_ns, mode=stat.S_IMODE(st.st_mode))
    try:
        data = Path(lf.path).read_bytes()
    except OSError:
        return fp
    fp["sha256"] = hashlib.sha256(data).hexdigest()
    if lf.kind == "raw":
        fp["login"] = bool(data.strip())
        return fp
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        fp["login"] = False
        return fp
    if not isinstance(obj, dict) or not obj:
        fp["login"] = False
        return fp
    fp["login"] = any(obj.get(k) for k in lf.login_keys) if lf.login_keys else True
    ident = next((_hash_value(_dig(obj, p)) for p in lf.identity if _hash_value(_dig(obj, p))), None)
    fp["identity"] = ident
    return fp


def snapshot(env: Optional[dict] = None, home: Optional[str] = None) -> dict:
    """{str(path): fingerprint} for every login file (present or not)."""
    return {str(lf.path): fingerprint(lf) for lf in login_files(env, home)}


@dataclass
class Finding:
    path: str
    provider: str
    reason: str
    step: str = ""
    fatal: bool = True

    def line(self) -> str:
        where = f" during step `{self.step}`" if self.step else ""
        head = "LOGIN LOST" if self.fatal else "login note"
        return f"{head}: {self.provider} login {self.path} {self.reason}{where}"


def compare(before: dict, after: dict, step: str = "") -> list:
    """Findings between two snapshots. Fatal: vanished, replaced by a non-file, login record gone, a
    different account, mode opened to group/other. A rewrite with the login intact is a non-fatal note."""
    out = []
    for path, b in before.items():
        a = after.get(path) or {"present": False, "kind": "missing"}
        prov = b.get("provider", "?")
        if not b.get("present"):
            if a.get("present"):
                out.append(Finding(path, prov, "appeared (new login file)", step, fatal=False))
            continue
        if not a.get("present"):
            out.append(Finding(path, prov, "VANISHED (it existed before this command started)", step))
            continue
        if b.get("kind") == "file" and a.get("kind") != "file":
            out.append(Finding(path, prov, f"was replaced by a {a.get('kind')}", step))
            continue
        if b.get("login") and a.get("login") is False:
            out.append(Finding(path, prov, "no longer holds a login record (emptied, unparseable or its login key is gone)", step))
            continue
        if b.get("identity") and a.get("identity") and a["identity"] != b["identity"]:
            out.append(Finding(path, prov, "now names a DIFFERENT account than before", step))
            continue
        if b.get("identity") and a.get("login") and not a.get("identity"):
            out.append(Finding(path, prov, "lost its account record", step))
            continue
        bm, am = b.get("mode"), a.get("mode")
        if bm is not None and am is not None and (am & 0o077) & ~(bm & 0o077):
            out.append(Finding(path, prov, f"mode opened from {bm:04o} to {am:04o}", step))
            continue
        if b.get("sha256") and a.get("sha256") and a["sha256"] != b["sha256"]:
            out.append(Finding(path, prov, "was rewritten with the login intact (its CLI refreshed a token)", step, fatal=False))
    return out


class LoginGuard:
    """begin() before the first step; after(step) after each one; findings accumulate. seal(command)
    writes the stamp (hashes only, 0600) when nothing fatal happened."""

    def __init__(self, env: Optional[dict] = None, home: Optional[str] = None, stamp_path: Optional[Path] = None):
        self.env = dict(os.environ if env is None else env)
        self.home = home
        self.stamp_path = Path(stamp_path) if stamp_path else None
        self.before: dict = {}
        self.last: dict = {}
        self.findings: list = []

    def begin(self) -> "LoginGuard":
        self.before = snapshot(self.env, self.home)
        self.last = dict(self.before)
        return self

    def present(self) -> list:
        return [p for p, f in self.before.items() if f.get("present")]

    def providers_present(self) -> list:
        return sorted({f["provider"] for f in self.before.values() if f.get("present")})

    def after(self, step: str) -> list:
        """Compare against the previous check (so each finding names the step it happened in).
        Returns the FATAL findings of this step; notes are kept in .findings only."""
        now = snapshot(self.env, self.home)
        found = compare(self.last, now, step)
        self.last = now
        self.findings.extend(found)
        return [f for f in found if f.fatal]

    @property
    def fatal(self) -> list:
        return [f for f in self.findings if f.fatal]

    def summary(self) -> str:
        n = len(self.present())
        provs = ", ".join(self.providers_present()) or "none"
        if self.fatal:
            return f"{len(self.fatal)} login file(s) harmed; see the LOGIN LOST line(s) above"
        notes = sum(1 for f in self.findings if not f.fatal)
        extra = f"; {notes} rewritten by their own CLI" if notes else ""
        return f"{n} provider login file(s) intact ({provs}){extra}"

    def seal(self, command: str) -> Optional[Path]:
        """Write the stamp doctor reads. Hashes, modes and the account-field hash only; 0600."""
        if self.stamp_path is None or self.fatal:
            return None
        write_stamp(self.stamp_path, command, self.last or self.before)
        return self.stamp_path


def write_stamp(path: Path, command: str, snap: dict) -> None:
    files = {p: {k: f.get(k) for k in ("provider", "present", "kind", "size", "mode", "sha256", "login", "identity")}
             for p, f in snap.items() if f.get("present")}
    doc = {"stamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "command": command, "files": files}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def read_stamp(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("files"), dict) else None


def stamp_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / STAMP_REL


def doctor_rows(data_dir: Path, env: Optional[dict] = None, home: Optional[str] = None,
                platform: Optional[str] = None) -> list:
    """One `logins:<provider>` row per provider, as (name, level, detail, remedy) tuples: OK intact since
    the stamp, WARN changed since it (naming the file and why), INFO no login found / not stamped yet."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    now = snapshot(env, home)
    stamp = read_stamp(stamp_path_for(data_dir))
    rows = []
    for prov in PROVIDERS:
        mine = {p: f for p, f in now.items() if f["provider"] == prov}
        present = {p: f for p, f in mine.items() if f.get("present")}
        if not present:
            if prov == "claude" and platform == "darwin":
                rows.append((f"logins:{prov}", "INFO", "no login file; macOS keeps Claude's login in the Keychain (never touched)", ""))
            else:
                rows.append((f"logins:{prov}", "INFO", f"no {prov} login found", ""))
            continue
        if not stamp:
            rows.append((f"logins:{prov}", "INFO", f"{len(present)} login file(s) found; not stamped yet",
                         "run `orchestra upgrade` (or `orchestra init --yes`): the guard records their fingerprints"))
            continue
        stamped = {p: dict(f, provider=prov) for p, f in stamp["files"].items() if f.get("provider") == prov}
        if not stamped:
            rows.append((f"logins:{prov}", "INFO", f"{len(present)} login file(s) found; none stamped at {stamp['stamp']} (logged in since)", ""))
            continue
        fatal = [f for f in compare(stamped, now) if f.fatal]
        if fatal:
            f0 = fatal[0]
            rows.append((f"logins:{prov}", "WARN", f"CHANGED since {stamp['stamp']}: {f0.path} {f0.reason}",
                         f"log in to {prov} again if its CLI asks; nothing in OrchestraOS writes that file"))
        else:
            rows.append((f"logins:{prov}", "OK", f"intact since {stamp['stamp']} ({stamp.get('command', '?')})", ""))
    return rows


def login_env_notes(env: Optional[dict] = None) -> list:
    """For `orchestra install-unit`: the login-dir variables set in the installing shell that a systemd
    unit will NOT carry (the unit's environment is rendered from the template; HOME comes from User=).
    Seats and recovery started by the unit would then read the DEFAULT dirs, which may hold no login."""
    env = os.environ if env is None else env
    names = [k for k in LOGIN_ENV if k != "HOME" and env.get(k)]
    if not names:
        return []
    return [("WARNING: " + ", ".join(names) + " is set in this shell, so your provider login lives outside "
             "the default dir, but a systemd unit does not carry it: seats and boot recovery started by the "
             "unit would read the default login dir (possibly signed out). Either log in under the default "
             "dir for the service user, or add Environment=\"<VAR>=<value>\" to a drop-in "
             "(`systemctl edit orchestra`) for " + ", ".join(names) + ".")]
