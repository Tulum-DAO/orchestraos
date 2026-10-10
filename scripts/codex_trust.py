#!/usr/bin/env python3
"""codex_trust.py <dir> — mark ONE seat directory trusted in Codex's config.toml, so an
interactive `codex` spawned there does not stop at "Do you trust the contents of this directory?".

ROOT CAUSE (gate container, codex-cli 0.153.4, 2026-10-10): `--yolo` does NOT skip Codex's
directory-trust prompt. A seat spawned in an untrusted directory sat at that prompt; the boot
instruction typed into it answered the menu, Codex quit, and the seat was a bare shell. Codex
reads the answer from `[projects."<dir>"] trust_level = "trusted"` in $CODEX_HOME/config.toml
(~/.codex/config.toml by default), so writing that key before launch is what a person clicking
"Yes, continue" would have done.

Rules (gm conditions on PR 0):
  * Only the exact seat directory, after resolving symlinks (the path Codex will check).
    REFUSED: /, $HOME, any directory that contains $HOME, anything with glob characters, and
    anything that is not an existing directory.
  * Merge, never clobber: every other key and comment in config.toml is kept byte for byte.
    The new text must parse and must say trusted, or nothing is written.
  * Atomic (temp file + rename, same mode as before), serialised with a lock, idempotent: an
    already-trusted directory writes nothing.
  * The first time this changes an existing config.toml, the original is copied to
    config.toml.orchestra-backup (never overwritten after that).

Exit 0 = trusted (written or already). Exit 3 = refused or failed; one plain line on stderr says
which and why, and spawn-agent.sh refuses the spawn instead of launching a seat that will die.
"""
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile

try:
    import tomllib
except ImportError:  # pragma: no cover — doctor already requires 3.11+
    tomllib = None

PREFIX = "codex_trust"
BACKUP_SUFFIX = ".orchestra-backup"
_GLOB = set("*?[]{}")
_HEADER = re.compile(r"""^\s*\[\s*projects\s*\.\s*("(?:[^"\\]|\\.)*"|'[^']*')\s*\]\s*(?:#.*)?$""")
_ANY_HEADER = re.compile(r"^\s*\[")
_TRUST_LINE = re.compile(r"^(\s*trust_level\s*=\s*)(\"[^\"]*\"|'[^']*')(.*)$")


class Refused(Exception):
    pass


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def resolve_target(arg: str) -> str:
    """The directory Codex will check, or Refused."""
    if not arg or not arg.strip():
        raise Refused("no directory given")
    if _GLOB & set(arg):
        raise Refused(f"{arg!r} contains glob characters; only one exact directory can be trusted")
    target = os.path.realpath(os.path.expanduser(arg))
    if not os.path.isdir(target):
        raise Refused(f"{target} is not an existing directory")
    home = os.path.realpath(os.path.expanduser("~"))
    if target == "/":
        raise Refused("refusing to trust / (the whole filesystem)")
    if target == home:
        raise Refused(f"refusing to trust your home directory {home}; spawn the seat in a project directory")
    if home.startswith(target.rstrip("/") + "/"):
        raise Refused(f"refusing to trust {target}: it contains your home directory")
    return target


def _key_of(quoted: str):
    try:
        return tomllib.loads(f"k = {quoted}\n")["k"]
    except Exception:  # noqa: BLE001 — an unparseable header is simply not ours
        return None


def is_trusted(text: str, target: str) -> bool:
    data = tomllib.loads(text) if text.strip() else {}
    proj = data.get("projects")
    return isinstance(proj, dict) and isinstance(proj.get(target), dict) \
        and proj[target].get("trust_level") == "trusted"


def merged(text: str, target: str) -> str:
    """config.toml text with target trusted; every other line unchanged."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _HEADER.match(line.rstrip("\n"))
        if not m or _key_of(m.group(1)) != target:
            continue
        j = i + 1
        while j < len(lines) and not _ANY_HEADER.match(lines[j]):
            t = _TRUST_LINE.match(lines[j].rstrip("\n"))
            if t:
                nl = "\n" if lines[j].endswith("\n") else ""
                lines[j] = f'{t.group(1)}"trusted"{t.group(3)}{nl}'
                return "".join(lines)
            j += 1
        lines.insert(i + 1, 'trust_level = "trusted"\n')
        return "".join(lines)
    out = text
    if out and not out.endswith("\n"):
        out += "\n"
    if out.strip():
        out += "\n"
    return out + f"[projects.{json.dumps(target)}]\ntrust_level = \"trusted\"\n"


def ensure_trusted(arg: str) -> str:
    """Returns "already" or "written"; raises Refused."""
    if tomllib is None:
        raise Refused("Python 3.11+ (tomllib) is needed to edit Codex's config.toml")
    target = resolve_target(arg)
    home = codex_home()
    os.makedirs(home, mode=0o700, exist_ok=True)
    cfg = os.path.join(home, "config.toml")
    lock_fd = os.open(os.path.join(home, ".orchestra-trust.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(cfg, encoding="utf-8") as f:
                text = f.read()
            mode = os.stat(cfg).st_mode & 0o777
            exists = True
        except FileNotFoundError:
            text, mode, exists = "", 0o600, False
        try:
            if is_trusted(text, target):
                return "already"
        except tomllib.TOMLDecodeError as e:
            raise Refused(f"{cfg} is not valid TOML ({e}); fix it, then spawn again") from None
        new = merged(text, target)
        try:
            ok = is_trusted(new, target)
        except tomllib.TOMLDecodeError as e:
            ok, why = False, str(e)
        else:
            why = "the merged file did not read back as trusted"
        if not ok:
            raise Refused(f"could not add the trust entry to {cfg} without changing other settings "
                          f"({why}); open Codex once in {target} and answer its trust question")
        if exists and not os.path.exists(cfg + BACKUP_SUFFIX):
            shutil.copy2(cfg, cfg + BACKUP_SUFFIX)
        fd, tmp = tempfile.mkstemp(dir=home, prefix=".config.toml.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(new)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, cfg)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return "written"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(argv) -> int:
    arg = argv[1] if len(argv) > 1 else ""
    try:
        result = ensure_trusted(arg)
    except Refused as e:
        sys.stderr.write(f"{PREFIX}: refused: {e}\n")
        return 3
    except OSError as e:
        sys.stderr.write(f"{PREFIX}: failed: could not update Codex's config.toml in {codex_home()}: {e}\n")
        return 3
    if result == "written":
        sys.stderr.write(f"{PREFIX}: trusted {os.path.realpath(os.path.expanduser(arg))} for codex\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
