#!/usr/bin/env python3
"""Rule 1 of the delete guard -- the NUCLEAR check -- decided on the RESOLVED TARGET.

THE DEFECT THIS REPLACES. The installed guard held a list of dangerous SPELLINGS
(`NUCLEAR_PATTERNS`) and matched them with `grep -qF`, a FIXED SUBSTRING. The bare prefix ending
in a root slash is a substring of EVERY absolute-path recursive delete, so the guard refused a
/tmp scratchpad, a repo .workspace and /var/log -- while every RELATIVE spelling contains no such
substring and sailed through. A relative recursive delete executed from the root IS the nuclear
case. It over-fired on safe and under-fired on nuclear.

THE FIX IS NOT A LONGER PATTERN LIST. A deny-list of spellings cannot work here, because the
dangerous property is not how the command is WRITTEN but WHERE IT LANDS -- and `rm -rf *` is
harmless in a build directory and catastrophic in `/`. So this module RESOLVES each delete target
against the invocation's cwd and refuses when the result IS a protected root. The safe set (every
project subdirectory anyone might ever delete) is unbounded and cannot be enumerated; the
PROTECTED set is small, closed, and writable by hand. That is the honest form of "enumerate the
safe cases and deny the rest" for this problem: enumerate the safe SHAPE (a path strictly below a
protected root), not the safe paths.

AND IT DECIDES ON INVOCATIONS, NOT ON TEXT. The old guard refused a document ABOUT itself three
times in one morning -- devex's commit message, the diagnostic that characterised it, and the
heredoc writing its test -- because it matched the pattern anywhere in the command string. A
guard that makes the record of its own defect expensive to write is a guard whose defect
survives. Here, only a segment that actually INVOKES the delete binary is considered; prose that
quotes the pattern is not an invocation, and a quoted heredoc body is text bash never executes.
AND A WRAPPED INVOCATION IS STILL AN INVOCATION: `sudo`, `env`, `nohup`, `time`, `xargs`,
`timeout`, `nice` and `sh -c` are stepped through to the command they exec, because reading only
the first word let every one of them bypass this check entirely (devex g7 condition 1) on the one
input class nobody disagrees about.

FAIL-OPEN on anything it cannot parse (returns None) -- a guard that crashes must never stop the
fleet from deleting a build directory. THE DEGRADED PATH IS NOT NOTHING: the shim that installs
this (scripts/hooks/delete_guard.installed.sh) keeps a small FAIL-CLOSED fence matching the
catastrophic root forms as EXACT whole commands, so a parse failure or a missing module degrades
to the old guarantee rather than to no protection at all. That fence is exact-match, never
substring, so it cannot re-create the inversion this replaces.
"""
from __future__ import annotations

import os
import re
import shlex

HOME = os.path.expanduser("~")

#: Deleting one of these IS the nuclear case. A path strictly BELOW one of them is ordinary work.
#: Kept small and closed on purpose -- this is the half that can be enumerated.
PROTECTED = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/media", "/mnt",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var",
    HOME,
}

_RECURSIVE_LONG = {"--recursive", "--dir"}
_SEPARATORS = (";", "&&", "||", "|", "\n")

#: Commands that EXEC another command. Reading only argv[0] made every one of these a bypass
#: (devex g7 condition 1): `sudo <recursive delete of the root>` hid the invocation completely,
#: while the guard being replaced refuses all of them today -- a D2 equality break in the LENIENT
#: direction, on the one input class nobody disagrees about. A wrapped invocation is still an
#: invocation, which is this module's own thesis applied one level out.
_WRAPPERS = {
    "sudo", "doas", "env", "nohup", "time", "exec", "command", "xargs",
    "nice", "ionice", "timeout", "stdbuf", "setsid", "chroot", "sh", "bash",
}

#: Wrapper flags that CONSUME the next token, so the token after them is not the command word.
#: Without this, `sudo -u operator rm ...` stops at "operator" and the bypass survives the fix.
#:
#: PER WRAPPER, NOT ONE FLAT SET, and that distinction is a defect I shipped and caught: a flat
#: set had `-E` in it for xargs, so `sudo -E -u operator <delete>` consumed the WRONG token and the
#: bypass survived my own fix for it. The same short flag means different things to different
#: programs; a set that ignores which program it is reading is a set that is wrong somewhere.
_WRAPPER_FLAGS_WITH_ARG = {
    "sudo": {"-u", "-g", "-p", "-C", "-h", "-t", "-U", "-r", "--user", "--group"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S"},
    "xargs": {"-n", "-P", "-I", "-i", "-d", "-a", "-E", "-L", "-s", "--max-args"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e"},
    "chroot": {"--userspec", "--groups"},
}

#: Wrappers whose `-c` operand is a SCRIPT to be parsed as a command in its own right, not a
#: token to step over. Without this, `bash -c '<delete of the root>'` reads as one opaque word.
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
#: Shell keywords that can stand before a command word in a segment: `for ...; do rm -rf X; done`
#: put the delete behind `do`, where reading argv[0] never saw it.
_SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "{", "!", "(", "time"}

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def _command_word(argv: list[str]) -> list[str]:
    """Advance past shell assignments and exec-wrappers to the real command and its arguments.

    Returns the remaining argv. Conservative by design: if it cannot tell where the wrapper's
    own operands end, it stops, argv[0] is not `rm`, and the caller allows -- the same fail-open
    the rest of this module takes.
    """
    i, n = 0, len(argv)
    while i < n:
        while i < n and (_ASSIGNMENT.match(argv[i]) or argv[i] in _SHELL_KEYWORDS):
            i += 1                                    # VAR=VALUE, or `do`/`then`/`if`..., may precede it
        if i >= n:
            break
        name = os.path.basename(argv[i])
        if name not in _WRAPPERS:
            break
        takes_arg = _WRAPPER_FLAGS_WITH_ARG.get(name, set())
        is_shell = name in _SHELLS
        i += 1
        while i < n:
            tok = argv[i]
            if tok == "--":
                i += 1
                break
            if is_shell and tok in ("-c", "-o"):
                # `bash -c '<script>'`: the operand is a COMMAND, handed back for re-parsing.
                return ["\x00script", argv[i + 1]] if i + 1 < n else []
            if tok.startswith("-"):
                i += 2 if tok in takes_arg else 1
                continue
            if _ASSIGNMENT.match(tok) or _DURATION.match(tok):
                i += 1                                # `timeout 5 rm ...`, `env FOO=1 rm ...`
                continue
            break
    return argv[i:]


def _segments(command: str) -> list[str]:
    """Split on shell separators that are OUTSIDE quotes, so a separator inside a quoted
    argument does not split an invocation and a quoted blob is not mistaken for one."""
    out, buf, quote, i, n = [], [], None, 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(command[i:i + 2])
            i += 2
            continue
        matched = next((s for s in _SEPARATORS if command.startswith(s, i)), None)
        if matched:
            out.append("".join(buf))
            buf = []
            i += len(matched)
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def _is_recursive(flags: list[str]) -> bool:
    for f in flags:
        if f in _RECURSIVE_LONG:
            return True
        if f.startswith("--"):
            continue
        if f.startswith("-") and any(c in "rR" for c in f[1:]):
            return True
    return False


def _resolve(target: str, cwd: str | None) -> str | None:
    """Resolve one delete operand to the directory it would empty, or None if undecidable."""
    t = target.strip()
    if not t:
        return None
    if t.startswith("~"):
        t = HOME + t[1:]
    t = t.replace("$HOME", HOME).replace("${HOME}", HOME)
    # `X/*` and `X/.` empty X itself, so X is what is at risk.
    while t.endswith("/*") or t.endswith("/."):
        t = t[:-2] or "/"
    if t == "*" or t == ".":
        t = cwd if cwd else None
        if t is None:
            return None
    elif not t.startswith("/"):
        if not cwd:
            return None
        t = os.path.join(cwd, t)
    return os.path.normpath(t)


def nuclear_refusal(command: str, cwd: str | None = None, _depth: int = 0) -> str | None:
    """Return a refusal reason naming THIS input, or None to allow.

    The reason must be true of the command in hand: the old guard told an author deleting a /tmp
    scratchpad that the command "would delete critical system/user files", which was false and is
    exactly the kind of refusal people learn to work around.
    """
    try:
        # QUOTED heredocs carry bodies bash never expands and never executes. Replace each with a
        # marker so a documentation line that happens to look like an invocation is not read as
        # one. This is the SAME treatment body_backtick_guard.py already applies (devex g7's D4:
        # take it from there rather than inventing a second mechanism) -- two guards that answer
        # "is this text or is this a command?" differently is how one of them ends up wrong.
        command = re.sub(r"<<-?\s*'(\w+)'\n.*?\n\1(\n|$)", "<<'HEREDOC'\n", command, flags=re.S)
        for segment in _segments(command):
            try:
                argv = shlex.split(segment)
            except ValueError:
                continue                      # unbalanced quotes in THIS segment; not decidable
            argv = _command_word(argv)
            if argv and argv[0] == "\x00script":
                # `sh -c '<script>'` -- decide on the script itself. Depth-capped so a
                # pathological nesting cannot spin; anything deeper falls through and allows.
                if _depth < 4:
                    inner = nuclear_refusal(argv[1], cwd, _depth=_depth + 1)
                    if inner:
                        return inner
                continue
            if not argv or os.path.basename(argv[0]) != "rm":
                continue                      # prose quoting the pattern is not an invocation
            flags = [a for a in argv[1:] if a.startswith("-") and a != "--"]
            if not _is_recursive(flags):
                continue
            operands, seen_ddash = [], False
            for a in argv[1:]:
                if a == "--":
                    seen_ddash = True
                    continue
                if not seen_ddash and a.startswith("-"):
                    continue
                operands.append(a)
            for op in operands:
                resolved = _resolve(op, cwd)
                if resolved is None:
                    continue
                if resolved in PROTECTED:
                    where = f" (resolved from {op!r} in {cwd})" if resolved != op else ""
                    return (f"this is a recursive delete of {resolved}{where} — a protected root. "
                            f"Deleting it removes the system or the home directory itself, not a "
                            f"project under it. A path strictly BELOW a protected root is allowed; "
                            f"name the subdirectory you mean.")
    except Exception:
        return None                           # FAIL-OPEN: never stop the fleet on a parse error
    return None


# ── Rule 2: protected FILES, decided on each delete target (never on the command text) ──────────
#
# A delete of a secret or credential file, a key or media file, or anything inside a folder the
# operator listed, is refused; the seat moves it aside instead. Same parsing as rule 1: only a
# segment that INVOKES rm / unlink / shred / find -delete counts, wrappers are stepped through and a
# quoted heredoc is text. A command that only MENTIONS such a file (a commit message, an echo, a
# message body) is not a delete and is allowed: the text-matching guard this replaces refused those.

#: Folders whose contents are secrets wherever they sit.
SECRET_DIRS = tuple(os.path.join(HOME, d) for d in (".secrets", ".ssh", ".gnupg"))
#: Key and media extensions refused anywhere, /tmp included (a recording or a key is not a build artifact).
KEY_EXT = (".pem", ".key", ".p12", ".pfx")
MEDIA_EXT = (".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".flac", ".aac", ".m4a", ".webm")
_KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")
_ENV_TEMPLATES = (".example", ".sample", ".template", ".dist")
_DELETERS = {"rm", "unlink", "shred"}


def operator_paths() -> list[str]:
    """Absolute folders the operator protects, one per line in <data dir>/delete-guard-protected-paths
    ($DELETE_GUARD_PATHS_FILE overrides; # comments and blank lines skipped). Kept out of the repo:
    which folders matter is the operator's, not the project's."""
    f = os.environ.get("DELETE_GUARD_PATHS_FILE") or os.path.join(
        os.environ.get("ORCHESTRA_DIR") or os.path.join(HOME, ".orchestra"), "delete-guard-protected-paths")
    out = []
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    p = os.path.normpath(os.path.expanduser(line))
                    if os.path.isabs(p):
                        out.append(p)
    except OSError:
        pass
    return out


def _file_kind(name: str) -> str | None:
    """Why a file NAME (glob allowed) is protected, or None."""
    low = name.lower()
    if low == ".env" or low.endswith(".env") or (low.startswith(".env.") and not low.endswith(_ENV_TEMPLATES)):
        return "an environment/secrets file"
    if "credentials" in low or low.startswith(_KEY_PREFIXES) or low.endswith(KEY_EXT):
        return "a credential or key file"
    if low.endswith(MEDIA_EXT):
        return "a media file"
    return None


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
#: A target still holding one of these after expansion cannot be resolved: it is refused (fail closed).
OPAQUE = "opaque"


def _known_vars(cwd: str | None) -> dict[str, str]:
    """Variables whose value the guard can know: HOME, PWD (the call's cwd) and TMPDIR."""
    known = {"HOME": HOME, "TMPDIR": os.environ.get("TMPDIR") or "/tmp"}
    if cwd:
        known["PWD"] = cwd
    return known


def _expand(op: str, env: dict[str, str]) -> str:
    """Expand $NAME / ${NAME} from env (one pass, then once more for a value naming another);
    returns OPAQUE when anything unresolvable is left: an unknown variable, $(...), backticks."""
    t = op
    for _ in range(2):
        t = _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), t)
    if "$" in t or "`" in t:
        return OPAQUE
    return t


def _note_assignments(argv: list[str], env: dict[str, str]) -> None:
    """Record `NAME=value` segments and `export NAME=value` so a later `rm "$NAME/x"` in the same
    command resolves. A prefix assignment on a command (`X=1 rm $X`) does not count: the shell
    expands $X before that assignment applies."""
    words = argv[1:] if argv[:1] == ["export"] else argv
    if not words or not all(_ASSIGNMENT.match(w) for w in words):
        return
    for w in words:
        name, _, value = w.partition("=")
        value = _expand(value, env)
        if value is OPAQUE:
            env.pop(name, None)
        else:
            env[name] = value


def _target_path(op: str, cwd: str | None, env: dict[str, str] | None = None) -> str | None:
    t = op.strip()
    if not t:
        return None
    if t.startswith("~"):
        t = HOME + t[1:]
    t = _expand(t, env if env is not None else _known_vars(cwd))
    if t is OPAQUE:
        return OPAQUE
    if not t.startswith("/"):
        if not cwd:
            return t                                   # relative and no cwd: judge the name only
        t = os.path.join(cwd, t)
    return os.path.normpath(t)


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip("/") + "/")


def _judge_target(op: str, cwd: str | None, recursive: bool, guarded: list[str],
                  env: dict[str, str] | None = None) -> str | None:
    if recursive and op.strip().rstrip("/") in (".", ""):
        return "a recursive delete of the current directory itself"
    path = _target_path(op, cwd, env)
    if path is None:
        return None
    if path is OPAQUE:
        return ("a path the guard cannot resolve (an unset variable or a command substitution); "
                "name the path literally, or set the variable earlier in the same command")
    kind = _file_kind(os.path.basename(path.rstrip("/")) or path)
    if kind:
        return kind
    for root in SECRET_DIRS:
        if _under(path, root) or (recursive and os.path.isabs(path) and _under(root, path)):
            return f"inside the secrets folder {root}"
    for root in guarded:
        if _under(path, root) or (recursive and os.path.isabs(path) and _under(root, path)):
            return f"inside the protected folder {root}"
    return None


def _find_targets(args: list[str]) -> tuple[list[str], list[str]]:
    """(start paths, -name/-path patterns) of a `find ... -delete`."""
    starts, i = [], 0
    while i < len(args) and not (args[i].startswith("-") or args[i] in ("(", "!", ")")):
        starts.append(args[i])
        i += 1
    names = [args[j + 1] for j in range(i, len(args) - 1)
             if args[j] in ("-name", "-iname", "-path", "-ipath", "-wholename")]
    return starts or ["."], names


def protected_file_refusal(command: str, cwd: str | None = None, _depth: int = 0,
                           _env: dict[str, str] | None = None) -> str | None:
    """Return a refusal naming the target and why, or None to allow. FAIL-OPEN on a parse error
    like rule 1, but a delete target it cannot RESOLVE (a variable it does not know) is refused."""
    env = dict(_env) if _env is not None else _known_vars(cwd)
    try:
        guarded = operator_paths()
        command = re.sub(r"<<-?\s*'(\w+)'\n.*?\n\1(\n|$)", "<<'HEREDOC'\n", command, flags=re.S)
        for segment in _segments(command):
            try:
                argv = shlex.split(segment)
            except ValueError:
                continue
            _note_assignments(argv, env)
            argv = _command_word(argv)
            if argv and argv[0] == "\x00script":
                if _depth < 4:
                    inner = protected_file_refusal(argv[1], cwd, _depth=_depth + 1, _env=env)
                    if inner:
                        return inner
                continue
            if not argv:
                continue
            name = os.path.basename(argv[0])
            if name == "find" and "-delete" in argv[1:]:
                starts, patterns = _find_targets(argv[1:])
                for pat in patterns:
                    kind = _file_kind(os.path.basename(pat))
                    if kind:
                        return f"`find -delete` matching {pat!r} would delete {kind}"
                for st in starts:
                    why = _judge_target(st, cwd, True, guarded, env)
                    if why and not why.startswith("a recursive delete of the current"):
                        return f"`find {st} -delete` reaches {why}"
                continue
            if name not in _DELETERS:
                continue
            flags = [a for a in argv[1:] if a.startswith("-") and a != "--"]
            recursive = name == "rm" and _is_recursive(flags)
            seen_ddash = False
            for a in argv[1:]:
                if a == "--":
                    seen_ddash = True
                    continue
                if not seen_ddash and a.startswith("-"):
                    continue
                why = _judge_target(a, cwd, recursive, guarded, env)
                if why:
                    verb = "targets" if why.startswith("a path the guard cannot") else "would delete"
                    return f"`{name} {a}` {verb} {why}"
    except Exception:
        return None
    return None


def main() -> int:
    import json
    import sys
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "") or ""
    cwd = payload.get("cwd") or None
    reason = nuclear_refusal(command, cwd)
    if reason:
        sys.stderr.write(f"BLOCKED (delete-guard/nuclear): {reason}\n")
        return 2
    reason = protected_file_refusal(command, cwd)
    if reason:
        sys.stderr.write(
            f"BLOCKED (delete-guard/protected-file): {reason}. NOTHING WAS DELETED.\n"
            f"Move it aside instead: mv <path> ~/.trash-safety/ (it can be restored from there). "
            f"If it really has to be deleted, ask gm.\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
