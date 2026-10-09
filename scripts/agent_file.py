"""agent_file — read one file from an agent's working folder for a paired device (GET /agent-file).

A new read surface over the filesystem, so it is built as a series of fences, each of which must
hold on its own (design ruled by gm, 2026-10-09):

1. ROOTS. Off until the operator lists roots in orchestra.toml (`[code] roots = [...]`). A root (and
   the seat's cwd under it) is refused if it is `/`, $HOME, the data dir, inside the data dir, or
   CONTAINS $HOME or the data dir. The seat must be registered; its registry `cwd` is the root.
2. PATH. Relative, at most MAX_PATH_LEN characters and MAX_SEGMENTS segments, no NUL or backslash,
   no empty, `.` or `..` segment.
3. CONFINEMENT. realpath (symlinks resolved) must stay inside the root.
4. DENY at any depth, on the requested path AND on the resolved one: any segment starting with "."
   (dotfiles and dotdirs: .env, .git, .ssh, .claude, ...), `logs` and `backups` segments, and names
   that hold secrets or transcripts (DENY_NAMES). Over-deny is fine; widen only on a named need.
5. OPEN. O_NOFOLLOW, regular files only, and then the path the open descriptor ACTUALLY points to
   (/proc/self/fd on Linux, F_GETPATH on macOS) goes through 3 and 4 again: a parent directory
   swapped for a symlink between the check and the open lands outside and is refused. No way to
   ask the descriptor -> refused.
6. SIZE. MAX_BYTES, from fstat on the open descriptor.
7. CONTENT. The bytes are scanned before anything is served: private keys, provider keys, JWTs,
   service-account JSON, and the gateway's own bearer or any device token (compared by hash; no
   token is ever logged). A match is refused.

Every refusal before 7 is the same "not found" to the caller (it never reveals whether a file
exists); the real reason goes only to the audit log.
"""
import fnmatch
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

MAX_PATH_LEN = 1024
MAX_SEGMENTS = 32
MAX_BYTES = 2 * 1024 * 1024

DENY_SEGMENTS = {"logs", "backups"}
DENY_NAMES = (
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
    "*secret*", "*credential*", "*token*", "*password*", "*passwd*",
    "*.env", ".env*", "*.secrets", "*.tfstate", "*.tfstate.*", "*.kdbx", "*.ovpn",
    "*service-account*.json", "*-key.json", "*.keystore", "*.jks",
    "*.sqlite", "*.sqlite3", "*.db", "*.db-*", "*.log", "*.log.*", "*.bak", "*.bak*", "*.jsonl",
    "*.npmrc", "*.pypirc", "*.netrc", "*.git-credentials",
)

# Content that must never leave the machine, whatever the file is called.
SECRET_PATTERNS = (
    ("private-key", re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("anthropic-key", re.compile(rb"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai-style-key", re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-|live-)?[A-Za-z0-9_\-]{20,}")),
    ("stripe-key", re.compile(rb"(?<![A-Za-z0-9])(?:rk|sk|pk)_live_[A-Za-z0-9]{10,}")),
    ("slack-token", re.compile(rb"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("github-token", re.compile(rb"(?<![A-Za-z0-9])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("aws-access-key", re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("gcp-service-account", re.compile(rb'"private_key"\s*:\s*"')),
    ("jwt", re.compile(rb"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
)
# Device tokens are secrets.token_urlsafe(32): 43 URL-safe characters.
_DEVICE_TOKEN_SHAPE = re.compile(rb"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{43}(?![A-Za-z0-9_\-])")
_MAX_TOKEN_CANDIDATES = 20000


class Refused(Exception):
    """A request that is not served. `reason` goes to the audit log only."""

    def __init__(self, reason: str, *, secret: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.secret = secret


def _real(p) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def check_root(root, *, home, data_dir) -> Path:
    """The real path of an acceptable root, or Refused."""
    r, h, d = _real(root), _real(home), _real(data_dir)
    if not r.is_dir():
        raise Refused("root-not-a-dir")
    if r == Path("/") or r == h:
        raise Refused("root-too-wide")
    if _inside(h, r):
        raise Refused("root-contains-home")
    if _inside(d, r) or _inside(r, d):
        raise Refused("root-overlaps-data-dir")
    return r


def seat_root(seat_cwd, allowlist, *, home, data_dir) -> Path:
    """The seat's cwd as a root, if it is (under) an allowlisted root and both pass check_root."""
    if not allowlist:
        raise Refused("no-roots-configured")
    cwd = check_root(seat_cwd, home=home, data_dir=data_dir)
    for allowed in allowlist:
        try:
            a = check_root(allowed, home=home, data_dir=data_dir)
        except Refused:
            continue
        if _inside(cwd, a):
            return cwd
    raise Refused("root-not-allowlisted")


def check_relpath(path: str) -> list:
    """The segments of an acceptable request path, or Refused."""
    if not isinstance(path, str) or not path:
        raise Refused("path-empty")
    if len(path) > MAX_PATH_LEN:
        raise Refused("path-too-long")
    if "\x00" in path or "\\" in path:
        raise Refused("path-bad-char")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise Refused("path-absolute")
    segs = path.split("/")
    if len(segs) > MAX_SEGMENTS:
        raise Refused("path-too-deep")
    if any(s in ("", ".", "..") for s in segs):
        raise Refused("path-bad-segment")
    return segs


def denied(segments) -> str | None:
    """Why these path segments are refused, or None."""
    for s in segments:
        low = s.lower()
        if low.startswith("."):
            return "deny-dot"
        if low in DENY_SEGMENTS:
            return "deny-segment"
        for pat in DENY_NAMES:
            if fnmatch.fnmatchcase(low, pat):
                return "deny-name"
    return None


def _confined_rel(p: Path, root: Path) -> list:
    if not _inside(p, root) or p == root:
        raise Refused("outside-root")
    return list(p.relative_to(root).parts)


def _fd_path(fd: int) -> Path:
    """The path an open descriptor points to NOW, or Refused when the OS cannot say."""
    if os.path.isdir("/proc/self/fd"):
        try:
            return Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            raise Refused("fd-path-unavailable")
    if sys.platform == "darwin":
        import fcntl
        try:
            raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
            return Path(raw.split(b"\0", 1)[0].decode())
        except OSError:
            raise Refused("fd-path-unavailable")
    raise Refused("fd-path-unsupported")


def scan_secrets(data: bytes, *, fleet_token: str = "", device_hashes=frozenset()) -> str | None:
    """The kind of secret found in `data`, or None. Never returns or logs the secret itself."""
    for kind, rx in SECRET_PATTERNS:
        if rx.search(data):
            return kind
    if fleet_token and len(fleet_token) >= 16 and fleet_token.encode() in data:
        return "gateway-bearer"
    if device_hashes:
        for i, m in enumerate(_DEVICE_TOKEN_SHAPE.finditer(data)):
            if i >= _MAX_TOKEN_CANDIDATES:
                break
            if hashlib.sha256(m.group(0)).hexdigest() in device_hashes:
                return "device-token"
    return None


def read_file(root: Path, path: str, *, fleet_token: str = "", device_hashes=frozenset(),
              max_bytes: int = MAX_BYTES, _after_check=None) -> tuple[Path, bytes]:
    """(path relative to root, bytes) or Refused. `_after_check` is a test hook that runs between
    the checks and the open (to prove a swap there is caught)."""
    segs = check_relpath(path)
    why = denied(segs)
    if why:
        raise Refused(why)
    resolved = _real(root / path)
    rel = _confined_rel(resolved, root)
    why = denied(rel)
    if why:
        raise Refused(why)
    if _after_check:
        _after_check()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(str(resolved), flags)
    except OSError:
        raise Refused("open-failed")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("not-a-regular-file")
        actual = _fd_path(fd)
        rel = _confined_rel(actual, root)
        why = denied(rel)
        if why:
            raise Refused(why + "-after-open")
        if st.st_size > max_bytes:
            raise Refused("too-large")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise Refused("too-large")
    finally:
        os.close(fd)
    kind = scan_secrets(data, fleet_token=fleet_token, device_hashes=device_hashes)
    if kind:
        raise Refused(f"secret-shaped:{kind}", secret=True)
    return Path(*rel), data


def content_type(data: bytes) -> tuple[str, bool]:
    """(Content-Type, inline). Images the bytes prove, and UTF-8 text with no NUL, are shown inline;
    everything else is an attachment. Served under a sandbox CSP + nosniff either way."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", True
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", True
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
            return "text/plain; charset=utf-8", True
        except UnicodeDecodeError:
            pass
    return "application/octet-stream", False
