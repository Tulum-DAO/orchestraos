"""Deterministic tool-call classifier for the PreToolUse safety gate.

classify(CallCtx) -> list[Hit] answers one question: which of the four supervised categories
(reaching the outside world, destructive changes, leaving the agent's folder, installing software)
does this tool call fall into, and which narrowing action inside it. It is a pure function of its
inputs (the hook snapshots the environment into CallCtx), table-driven, stdlib-only, and fast
(well under 2 ms a call). It never runs anything.

Anything it cannot read with confidence becomes one `destructive/unrecognised` hit with
certain=False: such a call is held only under "Watch closely", never silently passed.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from dataclasses import dataclass

# ---------------------------------------------------------------------------------------------
# Vocabulary (the UI reads it through GET /supervision)
# ---------------------------------------------------------------------------------------------

CATEGORIES = ("outside_world", "destructive", "outside_folder", "install")
ACTIONS = {
    "outside_world": {"deploy": "Deploys", "push": "Pushing code", "publish": "Publishing packages or pages",
                      "message": "Sending email or messages", "http_write": "Sending data to websites",
                      "remote_shell": "Running commands on other machines", "spend": "Paid APIs and payments",
                      "connector_write": "Changing things in connected apps"},
    "destructive": {"delete_files": "Deleting files", "force_push": "Force-pushing", "drop_data": "Deleting data",
                    "kill_process": "Stopping programs", "system_settings": "Changing system settings",
                    "unrecognised": "Commands the gate doesn't recognise"},
    "outside_folder": {"edit_outside": "Editing files outside its folder", "secrets": "Touching secrets or keys"},  # pragma: allowlist secret
    "install": {"npm": "npm / node packages", "pip": "Python packages", "system": "System packages",
                "script_pipe": "Install scripts from the internet", "other": "Other installers"},
}

DETAIL_MAX = 160
MAX_DEPTH = 2          # npm script -> its command -> one more level; inline code -> its subprocess literal


@dataclass(frozen=True)
class Hit:
    category: str              # one of CATEGORIES
    action: str                # a key of ACTIONS[category]
    detail: str                # short, redacted human text
    certain: bool = True       # False only for destructive/unrecognised
    folder: str | None = None
    recipient: str | None = None

    def to_dict(self) -> dict:
        return {"category": self.category, "action": self.action, "detail": self.detail,
                "certain": self.certain, "folder": self.folder, "recipient": self.recipient}

    @classmethod
    def from_dict(cls, d: dict) -> "Hit":
        return cls(category=d["category"], action=d["action"], detail=d.get("detail", ""),
                   certain=bool(d.get("certain", True)), folder=d.get("folder"), recipient=d.get("recipient"))


@dataclass(frozen=True)
class CallCtx:
    tool: str
    tool_input: dict
    cwd: str                   # the agent's project folder (registry cwd), not the hook's cwd
    own_dirs: tuple            # cwd + <data>/state + <data>/.workspace
    scratch_dirs: tuple        # /tmp, the session scratchpad: no outside_folder / delete hits there
    home: str
    local_bins: frozenset = frozenset()      # names in <cwd>/node_modules/.bin
    package_scripts: dict | None = None      # <cwd>/package.json "scripts", None if absent
    loopback_hosts: tuple = ("127.0.0.1", "localhost", "::1")
    realpath: Callable[[str], str] = os.path.realpath
    # Optional: package.json scripts for a folder other than cwd (`cd api && npm test`). None = unknown.
    scripts_loader: Callable[[str], dict | None] | None = None


# ---------------------------------------------------------------------------------------------
# Redaction, fingerprint, paths
# ---------------------------------------------------------------------------------------------

REDACT_RES = (
    re.compile(r"(?<![A-Za-z0-9])(?:sk|pk|rk|ghp|gho|ghs|xox[bpa]|AKIA|AIza)[-_A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"(?:token|key|secret|password|passwd)=\S+", re.I),
    re.compile(r"[0-9a-fA-F]{32,}"),
    # A 32+ base64url run with at least one digit (plain long words are left alone).
    re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{32,}"),
)


def redact(text: str) -> str:
    out = str(text)
    for rx in REDACT_RES:
        out = rx.sub("<redacted>", out)
    out = " ".join(out.split())
    return out if len(out) <= DETAIL_MAX else out[:DETAIL_MAX - 1] + "…"


def fingerprint(tool: str, tool_input: dict) -> str:
    body = {k: v for k, v in (tool_input or {}).items() if k != "description"}
    raw = tool + "\n" + json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    import hashlib  # lazy: only a call with hits needs a fingerprint (the hook's no-hit import budget)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def inside(p: str, d: str) -> bool:
    """Component-wise containment: /proj2 is not inside /proj. Both must be absolute."""
    if not p or not d:
        return False
    p, d = posixpath.normpath(p), posixpath.normpath(d)
    try:
        return posixpath.commonpath([p, d]) == d
    except ValueError:
        return False


SECRET_RES = tuple(re.compile(x) for x in (
    r"(^|/)\.env(\.|$)", r"\.pem$", r"\.key$", r"(^|/)id_(rsa|ed25519|ecdsa)", r"/\.ssh/", r"/\.aws/",
    r"/\.config/gcloud/",
    r"(^|/)\.(netrc|npmrc|pypirc)$", r"credentials", r"(^|/)[^/]*(token|secret)[^/]*$",
    r"\.claude/settings(\.local)?\.json$", r"\.claude\.json$",
    r"state/attention/(gate\.json|GATE_OFF)$", r"hooks/gate-hook\.py$",
))


def is_secret_path(path: str) -> bool:
    return bool(path) and any(rx.search(path) for rx in SECRET_RES)


def _abspath(p: str, cwd: str, home: str) -> str:
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    for var in ("${HOME}", "$HOME"):
        if p.startswith(var):
            p = home + p[len(var):]
    if not p.startswith("/"):
        p = posixpath.join(cwd, p)
    return posixpath.normpath(p)


def _real(ctx: CallCtx, p: str) -> str:
    try:
        return posixpath.normpath(ctx.realpath(p))
    except Exception:  # noqa: BLE001 - a broken realpath must not open a hole
        return p


def _in_any(ctx: CallCtx, p: str, dirs) -> bool:
    for d in dirs:
        if not d:
            continue
        if inside(p, d) or inside(p, _real(ctx, d)):
            return True
    return False


def _is_writable_here(ctx: CallCtx, p: str) -> bool:
    """True when a write to absolute p stays in own or scratch dirs, symlinks included."""
    dirs = tuple(ctx.own_dirs) + tuple(ctx.scratch_dirs)
    return _in_any(ctx, p, dirs) and _in_any(ctx, _real(ctx, p), dirs)


def _bin_exists(cwd: str, name: str) -> bool:
    return os.path.exists(posixpath.join(cwd, "node_modules", ".bin", name))


# ---------------------------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------------------------

READ_TOOLS = frozenset((
    "Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "Agent", "Task", "Skill", "ToolSearch",
    "AskUserQuestion", "SendMessage", "ScheduleWakeup", "Monitor", "TaskStop", "ExitPlanMode", "EnterWorktree",
    "ExitWorktree", "NotebookRead", "TaskOutput", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "BashOutput",
))
EDIT_TOOLS = frozenset(("Edit", "Write", "MultiEdit", "NotebookEdit"))

SAFE = frozenset((
    "ls", "cat", "head", "tail", "grep", "rg", "egrep", "fgrep", "awk", "cut", "wc", "sort", "uniq", "echo", "printf",
    "date", "sleep", "pwd", "cd", "export", "true", "false", "test", "[", "[[", "jq", "diff", "stat", "file", "which",
    "env", "basename", "dirname", "realpath", "readlink", "tr", "seq", "ps", "pgrep", "du", "df", "id", "whoami",
    "hostname", "uname", "tree", "less", "more", "column", "comm", "nl", "md5sum", "sha256sum", "base64",
    # read-only helpers and shell syntax words of the same kind
    "tac", "rev", "fold", "paste", "join", "shasum", "sha1sum", "cksum", "od", "xxd", "hexdump", "strings",
    "printenv", "nproc", "uptime", "free", "lsof", "ss", "netstat", "getent", "tty", "groups", "type", "set",
    "unset", "wait", "exit", "return", "shift", "read", "local", "declare", "typeset", "trap", ":", "for",
    "done", "fi", "esac", "case", "in", "]]", "popd", "pushd", "journalctl", "zcat", "zgrep", "fd", "nvidia-smi",
))
PEEL_PLAIN = frozenset(("nohup", "time", "exec", "setsid", "builtin", "if", "then", "do", "else", "elif",
                        "while", "until", "!", "{", "}", "(", "command"))
WRITE_CMDS = frozenset(("tee", "cp", "mv", "mkdir", "touch", "ln", "chmod", "install"))
DELETE_CMDS = frozenset(("rm", "rmdir", "shred", "truncate", "dd", "wipefs", "unlink"))
KILL_CMDS = frozenset(("kill", "pkill", "killall"))
SYSTEM_CMDS = frozenset(("systemctl", "service", "launchctl", "crontab", "sysctl", "ufw", "iptables", "chown",
                         "mount", "umount", "reboot", "shutdown", "usermod", "passwd", "chgrp", "useradd",
                         "userdel", "halt", "poweroff", "visudo", "update-alternatives"))
DB_CMDS = frozenset(("sqlite3", "psql", "mysql", "mongo", "mongosh", "redis-cli", "mariadb"))
DB_WRITE_RE = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER|FLUSHALL|FLUSHDB|dropDatabase|deleteMany|deleteOne)\b", re.I)

GIT_SAFE = frozenset((
    "status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "fetch", "add", "commit", "stash", "worktree",
    "remote", "config", "read-tree", "write-tree", "check-ignore", "merge-base", "tag", "blame", "shortlog",
    "describe", "grep", "ls-tree", "cat-file", "reflog", "rev-list", "show-ref", "for-each-ref", "name-rev", "init",
    "clone", "pull", "merge", "rebase", "cherry-pick", "switch", "checkout", "restore", "mv", "rm", "apply", "am",
    "format-patch", "notes", "bisect", "submodule", "gc", "fsck", "count-objects", "version", "help",
    "sparse-checkout", "cherry", "range-diff", "archive", "symbolic-ref", "update-index", "hash-object",
    "diff-tree", "diff-files", "diff-index", "ls-remote", "var", "whatchanged", "check-attr", "mailinfo",
))
GIT_VALUE_OPTS = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"))

GH_READ_VERBS = frozenset(("view", "list", "status", "diff", "checks", "checkout", "download", "watch", "ls"))
GH_WRITE = {
    "pr": {"create": "push", "comment": "push", "review": "push", "close": "push", "edit": "push",
           "ready": "push", "reopen": "push", "merge": "publish"},
    "issue": {"create": "push", "comment": "push", "close": "push", "edit": "push", "reopen": "push",
              "transfer": "push", "pin": "push", "lock": "push", "delete": "drop_data"},
    "release": {"create": "publish", "edit": "publish", "upload": "publish", "delete": "drop_data",
                "delete-asset": "drop_data"},
    "repo": {"create": "publish", "fork": "push", "edit": "push", "rename": "push", "archive": "push",
             "delete": "drop_data", "clone": None, "sync": None},
    "gist": {"create": "push", "edit": "push", "delete": "drop_data", "clone": None},
}
GH_READ_GROUPS = frozenset(("auth", "search", "browse", "help", "version", "config", "alias", "extension"))

DEPLOYERS = frozenset(("netlify", "vercel", "fly", "flyctl", "wrangler", "firebase", "heroku", "railway", "surge"))
DEPLOY_VERBS = frozenset(("deploy", "publish", "up", "release", "launch", "push", "promote", "rollback"))
DEPLOY_READ_VERBS = frozenset(("status", "list", "ls", "logs", "log", "whoami", "info", "open", "help", "version",
                               "--version", "-v", "-V", "--help", "-h", "sites:list", "env:list", "env:get",
                               "inspect", "dev", "link", "login", "logout", "functions:list", "serve", "watch"))
PUBLISHERS = {"npm": "publish", "pnpm": "publish", "yarn": "publish", "twine": "upload", "cargo": "publish",
              "gem": "push", "docker": "push", "podman": "push", "poetry": "publish", "bun": "publish"}
MAILERS = frozenset(("mail", "mailx", "sendmail", "mutt", "msmtp", "swaks"))
REMOTE_CMDS = frozenset(("ssh", "scp", "rsync", "sftp"))
HTTP_CMDS = frozenset(("curl", "wget", "http", "https", "httpie", "xh"))
PAID_HOSTS = ("api.openai.com", "api.anthropic.com", "api.stripe.com", "api.x.ai",
              "generativelanguage.googleapis.com", "api.elevenlabs.io", "api.twilio.com")

NPM_FAMILY = frozenset(("npm", "pnpm", "yarn", "bun"))
NPM_INSTALL_VERBS = frozenset(("install", "i", "add", "ci", "update", "upgrade", "up", "install-test", "it",
                               "link", "dedupe", "rebuild"))
NPM_READ_VERBS = frozenset(("ls", "list", "view", "info", "show", "outdated", "root", "bin", "prefix", "help",
                            "-v", "--version", "-V", "explain", "why", "doctor", "whoami", "ping", "search",
                            "config", "get", "pack", "fund", "query", "audit", "cache", "--help", "-h",
                            "licenses", "store", "bugs", "docs", "repo"))
NPX_LIKE = frozenset(("npx", "pnpx", "bunx"))
INSTALLERS = {
    "pip": "pip", "pip3": "pip", "pipx": "pip",
    "apt": "system", "apt-get": "system", "dnf": "system", "yum": "system", "brew": "system", "snap": "system",
    "apk": "system", "pacman": "system", "port": "system", "zypper": "system",
    "gem": "other", "cargo": "other", "go": "other", "composer": "other", "conda": "other", "mamba": "other",
}
INSTALL_VERBS = frozenset(("install", "add", "get", "upgrade", "reinstall", "-S", "-Syu", "dist-upgrade",
                           "full-upgrade", "uninstall", "remove", "purge"))
DEV_TOOLS = frozenset(("pytest", "py.test", "tsc", "eslint", "vite", "vitest", "prettier", "ruff", "mypy",
                       "black", "flake8", "pyright", "jest", "mocha", "biome", "stylelint", "playwright-test"))
UNINSPECTED = frozenset(("make", "just", "rake", "task", "gradle", "gradlew", "mvn", "ant", "ninja", "bazel"))
FLEET_SCRIPTS = frozenset(("msg_store.py", "approval.py", "agent-status.py"))
TMUX_SAFE = frozenset(("capture-pane", "capturep", "send-keys", "send", "ls", "list-sessions", "list-windows",
                       "list-panes", "list-clients", "list-buffers", "list-keys", "list-commands", "lsp", "lsw",
                       "has-session", "has", "display-message", "display", "show-options", "show",
                       "show-environment", "info", "server-info", "-V"))
TMUX_KILL = frozenset(("kill-session", "kill-server", "kill-pane", "kill-window", "killp", "killw"))

SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh"))
PY_RE = re.compile(r"^(python|pypy)(\d+(\.\d+)?)?$")
SCRIPT_PIPE_RES = (
    re.compile(r"\b(sh|bash|zsh|dash|ksh|source|\.)\s+<\(\s*(curl|wget)\b"),
    re.compile(r"\b(sh|bash|zsh|dash|ksh)\s+-c\s+[\"']?\$\(\s*(curl|wget)\b"),
    re.compile(r"\beval\s+[\"']?\$\(\s*(curl|wget)\b"),
)

INLINE_DELETE_RE = re.compile(r"shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|unlinkSync|rmSync|rmdirSync"
                              r"|fs\.rm\(|fs\.promises\.rm\b|\.unlink\(|\.rmdir\(")
INLINE_HTTP_RE = re.compile(r"requests\.(post|put|patch|delete)\b|httpx\.(post|put|patch|delete)\b"
                            r"|urlopen\(.*data=|Request\(.*(data=|method=\s*['\"](POST|PUT|PATCH|DELETE))"
                            r"|fetch\(.*method:\s*['\"](POST|PUT|PATCH|DELETE)"
                            r"|\.request\(\s*['\"](POST|PUT|PATCH|DELETE)", re.I | re.S)
INLINE_ANY_HTTP_RE = re.compile(r"urlopen\(|requests\.|httpx\.|fetch\(|Request\(|http\.client")
URL_RE = re.compile(r"https?://[^\s'\"`)<>]+")
INLINE_SUBPROC_RE = re.compile(
    r"(?:subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)|os\.system|os\.popen"
    r"|execSync|execFileSync|spawnSync|\bexec|\bspawn)\(\s*(\[[^\]]*\]|f?\"(?:[^\"\\\n]|\\.)*\"|f?'(?:[^'\\\n]|\\.)*'"
    r"|`[^`]*`)")
STR_LIT_RE = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")

HEREDOC_RE = re.compile(r"(?<!<)<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
OPS = ("&&", "||", ";;", "|&", ">>", "&>>", "&>", ">|", ">&", "<&", "<<<", "<<", "<>", ";", "&", "|", "(", ")",
       "<", ">", "\n")
_OPS_LONGEST_FIRST = tuple(sorted(OPS, key=len, reverse=True))
SEPARATORS = frozenset((";", "&&", "||", "&", "|", "|&", "(", ")", "\n", ";;"))
REDIRS = frozenset((">", ">>", ">|", "&>", "&>>", ">&", "<", "<<", "<<<", "<&", "<>"))
HEREDOC_TOKEN = re.compile(r"^__GATEHEREDOC(\d+)__$")


# ---------------------------------------------------------------------------------------------
# Shell lexing
# ---------------------------------------------------------------------------------------------

def _split_ops(tok: str) -> list:
    out, i = [], 0
    while i < len(tok):
        for op in _OPS_LONGEST_FIRST:
            if tok.startswith(op, i):
                out.append(op)
                i += len(op)
                break
        else:  # pragma: no cover - only punctuation chars reach here
            out.append(tok[i])
            i += 1
    return out


def _extract_heredocs(text: str):
    bodies: list = []
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        markers: list = []

        def sub(m):
            markers.append((m.group(3), m.group(1) == "-"))
            return "<< __GATEHEREDOC%d__" % (len(bodies) + len(markers) - 1)

        out.append(HEREDOC_RE.sub(sub, lines[i]))
        i += 1
        for marker, dash in markers:
            body = []
            while i < len(lines):
                ln = lines[i]
                i += 1
                if (ln.lstrip("\t") if dash else ln).rstrip() == marker:
                    break
                body.append(ln)
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _lex(text: str):
    """(tokens, heredoc bodies). Operators come back as separate tokens; raises ValueError when unparseable."""
    text, bodies = _extract_heredocs(text)
    text = text.replace("\\\n", " ")
    if "`" in text:
        parts = text.split("`")
        text = parts[0] + "".join(("$(" if k % 2 == 0 else ")") + p for k, p in enumerate(parts[1:]))
    lex = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    toks: list = []
    for t in lex:
        if t and all(c in "();<>|&\n" for c in t):
            toks.extend(_split_ops(t))
        else:
            toks.append(t)
    return toks, bodies


@dataclass
class _Cmd:
    argv: list
    redirs: list               # (op, target)
    heredoc: str | None = None
    piped_from: "_Cmd | None" = None
    piped_to: bool = False


def _split(command: str):
    toks, bodies = _lex(command)
    cmds: list = []
    cur = _Cmd([], [])
    pipe_prev = None
    i, n = 0, len(toks)

    def flush(next_piped):
        nonlocal cur, pipe_prev
        if cur.argv or cur.redirs:
            cur.piped_from = pipe_prev
            cmds.append(cur)
            if next_piped:
                cur.piped_to = True
            pipe_prev = cur if next_piped else None
        elif not next_piped:
            pipe_prev = None
        cur = _Cmd([], [])

    while i < n:
        t = toks[i]
        if t == "$" and i + 1 < n and toks[i + 1] == "(":
            flush(False)
            i += 2
            continue
        if t in SEPARATORS:
            flush(t in ("|", "|&"))
            i += 1
            continue
        if t in REDIRS:
            if cur.argv and cur.argv[-1].isdigit() and t not in ("<<", "<<<"):
                cur.argv.pop()
            nxt = toks[i + 1] if i + 1 < n else ""
            if nxt == "(":           # process substitution: the inner command is split normally
                flush(False)
                i += 1
                continue
            if t == "<<":
                m = HEREDOC_TOKEN.match(nxt)
                if m and int(m.group(1)) < len(bodies):
                    cur.heredoc = bodies[int(m.group(1))]
                    i += 2
                    continue
            if t == "<<<":
                cur.heredoc = nxt
                i += 2
                continue
            cur.redirs.append((t, nxt))
            i += 2
            continue
        if t.startswith("#") and not cur.argv:
            while i < n and toks[i] not in SEPARATORS:
                i += 1
            continue
        cur.argv.append(t)
        i += 1
    flush(False)
    return cmds


# ---------------------------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------------------------

class _Run:
    """Accumulates hits for one call (deduplicated by category/action/folder/recipient)."""

    def __init__(self, ctx: CallCtx):
        self.ctx = ctx
        self.hits: list = []
        self._seen: set = set()

    def add(self, category, action, detail, folder=None, recipient=None, folder_is_real=False):
        certain = action != "unrecognised"
        if action == "unrecognised":
            folder = recipient = None
        if folder and not folder_is_real:
            # The REAL folder: a folder-narrowed grant (gate_policy.grant_matches, compared by components) must not
            # cover a write that only lexically sits inside it (<granted>/link -> /etc). Grants store real paths too.
            folder = _real(self.ctx, folder)
        key = (category, action, folder, recipient)
        if key in self._seen:
            return
        self._seen.add(key)
        self.hits.append(Hit(category, action, redact(detail), certain, folder, recipient))

    def unknown(self, detail):
        self.add("destructive", "unrecognised", detail)


def _common_folder(paths) -> str | None:
    paths = [p for p in paths if p]
    if not paths:
        return None
    try:
        return posixpath.commonpath(paths)
    except ValueError:
        return paths[0]


def _looks_pathlike(a: str) -> bool:
    if not a or any(c.isspace() for c in a) or "://" in a or a.startswith("-"):
        return False
    return "/" in a or a.startswith((".", "~", "$HOME", "${HOME}")) or "." in a


def _secret_args(run: _Run, args, cwd, detail):
    home = run.ctx.home
    for a in args:
        if a.startswith("-") and "=" in a:
            a = a.split("=", 1)[1]
        elif a.startswith("-"):
            continue
        if "=" in a and not a.startswith(("/", ".", "~")) and "/" not in a.split("=", 1)[0]:
            a = a.split("=", 1)[1]          # dd of=..., key=path
        if _looks_pathlike(a) and is_secret_path(_abspath(a, cwd, home)):
            run.add("outside_folder", "secrets", detail, folder=posixpath.dirname(_abspath(a, cwd, home)))
            return


def _write_check(run: _Run, targets, cwd, detail):
    outside = []
    for t in targets:
        if not t or t == "-" or t.startswith("/dev/"):
            continue
        if t == "\x00stdin":
            outside.append(None)
            continue
        p = _abspath(t, cwd, run.ctx.home)
        if not _is_writable_here(run.ctx, p):
            outside.append(posixpath.dirname(_real(run.ctx, p)))     # each target real BEFORE the common folder
    if outside:
        # any unknown target means the folder is unknown: never narrow to the known ones (review r8)
        folder = None if None in outside else _common_folder(outside)
        run.add("outside_folder", "edit_outside", detail, folder=folder)


def _delete_check(run: _Run, targets, cwd, detail, recursive):
    hit_paths, unknown = [], False
    for t in targets:
        if t == "\x00stdin":
            unknown = True
            continue
        if t.startswith("/dev/"):
            continue
        p = _abspath(t, cwd, run.ctx.home)
        if _in_any(run.ctx, p, run.ctx.scratch_dirs):
            continue
        # each target's real parent BEFORE the common folder: a delete removes a link itself, not what it names
        # (a trailing slash, link/, follows the link, so that target is resolved whole)
        if p == "/" or t.rstrip().endswith(("/", "/.")):
            rp = _real(run.ctx, p)
        else:
            rp = posixpath.join(_real(run.ctx, posixpath.dirname(p)), posixpath.basename(p))
        hit_paths.append(rp if recursive else posixpath.dirname(rp))
    if hit_paths or unknown:
        # any unknown target (xargs, stdin) means the folder is unknown: never narrow to the known ones (review r4)
        folder = None if unknown else _common_folder(hit_paths)
        run.add("destructive", "delete_files", detail, folder=folder, folder_is_real=True)


def _positionals(args, value_opts=frozenset(), value_short=""):
    """Non-flag arguments, skipping the values of options that take one."""
    out, skip = [], False
    for k, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--":
            out.extend(args[k + 1:])
            break
        if a.startswith("--"):
            if a in value_opts:
                skip = True
            continue
        if a.startswith("-") and len(a) > 1:
            if a in value_opts:
                skip = True
            elif value_short and a[-1] in value_short and len(a) == 2:
                skip = True
            continue
        out.append(a)
    return out


def _host_of(url: str) -> str:
    m = re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/]*@)?(\[[^\]]+\]|[^:/?#]+)", url)
    h = m.group(1) if m else url
    return h.strip("[]").lower()


def _is_loopback(ctx: CallCtx, host: str) -> bool:
    return host in ctx.loopback_hosts or host.startswith("127.")


def _is_paid(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in PAID_HOSTS)


def _flag_set(args) -> set:
    s = set()
    for a in args:
        if a.startswith("--"):
            s.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            s.add(a)
            for c in a[1:]:
                s.add("-" + c)
    return s


# ---- HTTP clients (curl, wget, httpie/xh) --------------------------------------------------------------------
# Options are parsed against explicit tables. An option the tables don't know counts as "unseen": the request might
# go somewhere the URLs don't show, so a write is http_write even to a loopback URL (the safe side; review r6).

CURL_LONG_VALUE = frozenset((
    "--request", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "--json", "--form",
    "--form-string", "--upload-file", "--header", "--output", "--user", "--user-agent", "--referer", "--cookie",
    "--cookie-jar", "--write-out", "--max-time", "--connect-timeout", "--proxy", "--config", "--retry",
    "--retry-delay", "--retry-max-time", "--range", "--continue-at", "--cert", "--key", "--cacert", "--capath",
    "--resolve", "--url", "--unix-socket", "--abstract-unix-socket", "--limit-rate", "--output-dir", "--interface",
    "--dns-servers", "--time-cond", "--max-filesize", "--quote", "--oauth2-bearer", "--aws-sigv4", "--variable",
    "--expand-url", "--dump-header", "--trace", "--trace-ascii", "--stderr", "--etag-save", "--etag-compare",
    "--hsts", "--alt-svc", "--preproxy", "--proxy1.0", "--socks4", "--socks4a", "--socks5", "--socks5-hostname",
    "--connect-to", "--doh-url", "--proxy-user", "--proxy-header", "--max-redirs", "--url-query", "--ciphers",
    "--tls-max", "--keepalive-time", "--expect100-timeout", "--parallel-max", "--create-file-mode", "--rate",
    "--cert-type", "--key-type", "--pass", "--mail-from", "--mail-rcpt", "--mail-auth", "--ftp-port",
    "--speed-limit", "--speed-time", "--local-port", "--happy-eyeballs-timeout-ms", "--netrc-file", "--libcurl"))
CURL_SHORT_VALUE = "XdFTHouAebcwmxKrCEYyzQD"  # pragma: allowlist secret (curl short options that take a value)
CURL_LONG_FLAGS = frozenset((
    "--silent", "--show-error", "--fail", "--fail-with-body", "--fail-early", "--include", "--verbose", "--insecure",
    "--head", "--get", "--compressed", "--no-buffer", "--globoff", "--progress-bar", "--http1.0", "--http1.1",
    "--http2", "--http2-prior-knowledge", "--http3", "--ipv4", "--ipv6", "--remote-name", "--remote-name-all",
    "--remote-header-name", "--create-dirs", "--location", "--location-trusted", "--no-keepalive", "--tcp-nodelay",
    "--raw", "--no-progress-meter", "--disable", "--path-as-is", "--anyauth", "--basic", "--digest", "--ntlm",
    "--negotiate", "--netrc", "--netrc-optional", "--parallel", "--no-sessionid", "--ssl", "--ssl-reqd",
    "--tlsv1", "--tlsv1.2", "--tlsv1.3", "--junk-session-cookies", "--list-only", "--append", "--crlf",
    "--proxytunnel", "--suppress-connect-headers", "--trace-time", "--xattr", "--no-clobber", "--remove-on-error",
    "--help", "--version", "--manual", "--styled-output", "--no-styled-output", "--next", "--skip-existing"))
CURL_SHORT_FLAGS = "sSfivkIGNg#LOJq012346RlaBjnMVhpZ:"
CURL_WRITE_OPTS = frozenset(("--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "--json",
                             "--form", "--form-string", "--upload-file", "-d", "-F", "-T"))
# Options that send the request somewhere other than the URL's host (a proxy, a host remap, DNS over HTTPS, a
# redirect that may resend the body): with any of them a loopback URL is not proof that the data stays here.
CURL_REROUTE = frozenset(("-x", "--proxy", "--preproxy", "--proxy1.0", "--socks4", "--socks4a", "--socks5",
                          "--socks5-hostname", "--resolve", "--connect-to", "--doh-url", "-L", "--location",
                          "--location-trusted"))
# Options whose value is a file curl writes (besides -o).
CURL_OUT_OPTS = frozenset(("-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--trace", "--trace-ascii",
                           "--stderr", "--etag-save", "--hsts", "--alt-svc", "--libcurl"))
# -w/--write-out can name an output file: %output{file}, %output{>>file} (also written %{output{file}})
CURL_WRITE_OUT_FILE_RE = re.compile(r"%\{?output\{(?:>>)?([^}]*)\}")

WGET_LONG_VALUE = frozenset((
    "--output-document", "--directory-prefix", "--output-file", "--append-output", "--post-data", "--post-file",
    "--method", "--body-data", "--body-file", "--user-agent", "--header", "--tries", "--timeout", "--execute",
    "--user", "--password", "--input-file", "--wait", "--config", "--base", "--max-redirect", "--level", "--quota",
    "--limit-rate", "--referer", "--load-cookies", "--save-cookies", "--accept", "--reject", "--domains",
    "--exclude-domains", "--include-directories", "--exclude-directories", "--http-user", "--http-password",
    "--ca-certificate", "--certificate", "--private-key", "--dns-timeout", "--connect-timeout", "--read-timeout",
    "--waitretry", "--bind-address", "--local-encoding", "--remote-encoding", "--restrict-file-names",
    "--default-page", "--cut-dirs", "--progress", "--warc-file", "--rejected-log", "--proxy-user",
    "--proxy-password", "--random-wait", "--warc-tempdir"))
WGET_SHORT_VALUE = "OoaPeitTUBlQwDARIX"
WGET_LONG_FLAGS = frozenset((
    "--quiet", "--no-verbose", "--verbose", "--debug", "--server-response", "--spider", "--continue",
    "--timestamping", "--no-clobber", "--recursive", "--mirror", "--page-requisites", "--convert-links",
    "--no-parent", "--no-check-certificate", "--content-disposition", "--show-progress", "--no-cache",
    "--no-cookies", "--ignore-length", "--inet4-only", "--inet6-only", "--no-directories", "--force-directories",
    "--no-host-directories", "--adjust-extension", "--trust-server-names", "--no-proxy", "--retry-connrefused",
    "--https-only", "--auth-no-challenge", "--keep-session-cookies", "--save-headers", "--no-dns-cache", "--help",
    "--version", "--background", "--backups", "--no-netrc"))
WGET_SHORT_FLAGS = "qnvdScNrmkpKEFxHbhV46"
WGET_OUT_OPTS = frozenset(("-O", "--output-document", "-o", "--output-file", "-a", "--append-output",
                           "--save-cookies", "--warc-file", "--rejected-log"))

HTTPIE_LONG_VALUE = frozenset((
    "--output", "--auth", "--auth-type", "--session", "--session-read-only", "--timeout", "--verify", "--cert",
    "--cert-key", "--cert-key-pass", "--proxy", "--pretty", "--style", "--print", "--format-options",
    "--max-redirects", "--max-headers", "--raw", "--boundary", "--ssl", "--ciphers", "--default-scheme",
    "--unix-socket", "--history-print", "--response-charset", "--response-mime"))
HTTPIE_SHORT_VALUE = "oaApsP"
HTTPIE_LONG_FLAGS = frozenset((
    "--json", "--form", "--multipart", "--headers", "--body", "--verbose", "--all", "--follow", "--check-status",
    "--ignore-stdin", "--offline", "--download", "--continue", "--quiet", "--stream", "--chunked", "--compress",
    "--ignore-netrc", "--traceback", "--debug", "--help", "--version", "--meta", "--path-as-is", "--https"))
HTTPIE_SHORT_FLAGS = "jfhbvFIdcqSxm"
HTTPIE_REROUTE = frozenset(("--proxy", "--follow", "-F"))


# The option words gate_fixture_harvest keeps when it re-generates fixtures (its allow-list vocabulary; unchanged).
CURL_VALUE = frozenset(("-X", "--request", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                        "--data-ascii", "--json", "-F", "--form", "--form-string", "-T", "--upload-file", "-H",
                        "--header", "-o", "--output", "-u", "--user", "-A", "--user-agent", "-e", "--referer",
                        "-b", "--cookie", "-c", "--cookie-jar", "-w", "--write-out", "-m", "--max-time",
                        "--connect-timeout", "-x", "--proxy", "-K", "--config", "--retry", "--retry-delay",
                        "--retry-max-time", "-r", "--range", "-C", "--continue-at", "-E", "--cert", "--key",
                        "--cacert", "--resolve", "--url", "--unix-socket", "-Y", "-y", "--limit-rate",
                        "--output-dir", "--interface", "--dns-servers", "-z", "--max-filesize", "-Q", "--quote",
                        "--oauth2-bearer", "--aws-sigv4", "--variable", "--expand-url"))
WGET_VALUE = frozenset(("-O", "--output-document", "-P", "--directory-prefix", "-o", "--output-file", "-a",
                        "--append-output", "--post-data", "--post-file", "--method", "--body-data", "--body-file",
                        "-U", "--user-agent", "--header", "-t", "--tries", "-T", "--timeout", "-e", "--execute",
                        "--user", "--password", "-i", "--input-file", "-w", "--wait"))


def _parse_opts(args, long_value, short_value, long_flags, short_flags):
    """(opts [(name, value or None)], positionals, unknown). Short clusters stop at the first option that takes a
    value (the rest of the token, or the next argument, is its value). `--` ends the options. An option not in the
    tables sets unknown (its value, if any, is then read as a positional: the safe side)."""
    opts, pos, unknown, i = [], [], False, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1:])
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in long_value:
                if not eq:
                    i += 1
                    val = args[i] if i < len(args) else ""
                opts.append((name, val))
            else:
                if name not in long_flags:
                    unknown = True
                opts.append((name, val if eq else None))
        elif a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                c = a[j]
                if c in short_value:
                    val = a[j + 1:]
                    if not val:
                        i += 1
                        val = args[i] if i < len(args) else ""
                    opts.append(("-" + c, val))
                    break
                if c not in short_flags:
                    unknown = True
                opts.append(("-" + c, None))
                j += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos, unknown


def _curl_configs(args) -> list:
    """The values of curl's -K / --config (also clustered, e.g. -sK file)."""
    opts, _pos, _unknown = _parse_opts(args, CURL_LONG_VALUE, CURL_SHORT_VALUE, CURL_LONG_FLAGS, CURL_SHORT_FLAGS)
    return [v for k, v in opts if k in ("-K", "--config")]


_CURL_HEADER_LINE = re.compile(r"^\s*(header\s*[=:]?|-H\b|--header\b)")


def _curl_header_only_stdin(ctx, configs, urls, piped_from) -> bool:
    """True only for `printf '<one header line>' | curl -K - <loopback scheme URLs>` (or plain `echo`): the piped
    text is exactly one static word with no flag, expansion, escape or format directive ($ ` \\ %), so what curl
    reads is that one line, and it is a header. Anything else in a curl config (url, data, request...) is unseen."""
    if configs != ["-"] or piped_from is None:
        return False
    argv = [x for x in piped_from.argv if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", x)]
    if len(argv) != 2 or posixpath.basename(argv[0]) not in ("printf", "echo"):
        return False
    text = argv[1]
    if text.startswith("-") or any(c in text for c in "$`\\%\n\r"):
        return False
    scheme = [u for u in urls if "://" in u]
    if not scheme or len(scheme) != len(urls) or any(not _is_loopback(ctx, _host_of(u) or "") for u in scheme):
        return False
    return bool(_CURL_HEADER_LINE.match(text))


def _http(run: _Run, name, args, cwd, detail, depth, piped_from=None):
    ctx = run.ctx
    outs, writes, rerouted, unseen = [], False, False, False
    if name == "curl":
        opts, pos, unknown = _parse_opts(args, CURL_LONG_VALUE, CURL_SHORT_VALUE, CURL_LONG_FLAGS, CURL_SHORT_FLAGS)
        keys = {k for k, _ in opts}
        urls = list(pos) + [v for k, v in opts if k in ("--url", "--expand-url") and v]
        # any write method named counts (curl uses the last; a repeat must not hide one)
        methods = {v.upper() for k, v in opts if k in ("-X", "--request") and v}
        writes = bool(keys & CURL_WRITE_OPTS) or bool(methods & {"POST", "PUT", "PATCH", "DELETE"})
        dirs = [v for k, v in opts if k == "--output-dir" and v]
        out_dir = dirs[-1] if dirs else None                     # curl uses the last one
        for d in dirs:
            outs.append(posixpath.join(d, "x"))                  # every one named is a place curl may write
        for k, v in opts:
            if k in CURL_OUT_OPTS and v:
                outs.append(posixpath.join(out_dir, v) if out_dir and k in ("-o", "--output") else v)
            elif k in ("-w", "--write-out") and v:
                if v.startswith("@"):
                    outs.append("\x00stdin")                    # the format is read from a file / stdin: unseen
                else:
                    outs.extend(m.group(1) for m in CURL_WRITE_OUT_FILE_RE.finditer(v) if m.group(1))
        if unknown:
            # an option the tables don't know: the call is not fully understood, so it is "unrecognised" (held at
            # Watch closely, like any command the gate doesn't know) and a write is treated as rerouted below
            run.unknown(detail)
        if keys & {"-O", "--remote-name", "--remote-name-all", "-J", "--remote-header-name"}:
            outs.append(posixpath.join(out_dir or ".", "x"))
        rerouted = unknown or bool(keys & CURL_REROUTE)
        configs = [v for k, v in opts if k in ("-K", "--config")]
        exempt = bool(configs) and not rerouted and _curl_header_only_stdin(ctx, configs, urls, piped_from)
        if configs and not exempt:
            # A config file can carry any URL, method and body (uninspected). The one exception is the
            # fleet's "printf 'header = ...' | curl -K - http://127.0.0.1:..." shape (one static header, loopback).
            unseen = True
    elif name == "wget":
        opts, pos, unknown = _parse_opts(args, WGET_LONG_VALUE, WGET_SHORT_VALUE, WGET_LONG_FLAGS, WGET_SHORT_FLAGS)
        keys = {k for k, _ in opts}
        urls = list(pos)
        writes = bool(keys & {"--post-data", "--post-file", "--body-data", "--body-file"}) or any(
            k == "--method" and (v or "").upper() in ("POST", "PUT", "PATCH", "DELETE") for k, v in opts)
        explicit_out = False
        for k, v in opts:
            if k in WGET_OUT_OPTS and v:
                if k in ("-O", "--output-document"):
                    explicit_out = True
                outs.append(v)
            elif k in ("-P", "--directory-prefix"):
                explicit_out = True
                outs.append(posixpath.join(v or ".", "x"))
            elif k == "--warc-tempdir" and v:
                outs.append(posixpath.join(v, "x"))
        if unknown:
            # an option the tables don't know: the call is not fully understood, so it is "unrecognised" (held at
            # Watch closely, like any command the gate doesn't know) and a write is treated as rerouted below
            run.unknown(detail)
        if not explicit_out and (urls or keys & {"-i", "--input-file"}):
            outs.append("./x")
        # wget follows redirects (resending a POST body) unless told not to
        no_redirect = any(k == "--max-redirect" and (v or "").strip() == "0" for k, v in opts)
        rerouted = unknown or not no_redirect
        if keys & {"-e", "--execute", "--config"}:
            unseen = True                          # wgetrc commands: any URL, body or proxy
        elif keys & {"-i", "--input-file"} and writes:
            unseen = True                          # the URLs are in a file
    else:  # httpie / xh
        opts, pos, unknown = _parse_opts(args, HTTPIE_LONG_VALUE, HTTPIE_SHORT_VALUE, HTTPIE_LONG_FLAGS,
                                         HTTPIE_SHORT_FLAGS)
        keys = {k for k, _ in opts}
        method = None
        if pos and pos[0].upper() in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            method = pos.pop(0).upper()
        urls = pos[:1]
        items = pos[1:]
        stdin_body = piped_from is not None and not keys & {"--ignore-stdin", "-I"}
        writes = method in ("POST", "PUT", "PATCH", "DELETE") or "--raw" in keys or stdin_body or any(
            ("=" in it and "==" not in it) or ":=" in it or "@" in it for it in items)
        named = [v for k, v in opts if k in ("-o", "--output") and v]
        outs.extend(named)                                       # every one named (the last applies)
        if not named and keys & {"-d", "--download"}:
            outs.append("./x")
        for k, v in opts:
            # a writable session named by path is updated after the request (a bare name lives in httpie's config)
            if k == "--session" and v and ("/" in v or v.endswith(".json")):
                outs.append(v)
        if unknown:
            # an option the tables don't know: the call is not fully understood, so it is "unrecognised" (held at
            # Watch closely, like any command the gate doesn't know) and a write is treated as rerouted below
            run.unknown(detail)
        rerouted = unknown or bool(keys & HTTPIE_REROUTE)
    _write_check(run, outs, cwd, detail)
    if rerouted and writes:
        unseen = True                              # the body may reach a host the URLs don't show
    for u in urls:
        host = _host_of(u)
        if not host or _is_loopback(ctx, host):
            continue
        if _is_paid(host):
            run.add("outside_world", "spend", detail, recipient=host)
        elif writes:
            run.add("outside_world", "http_write", detail, recipient=host)
    if unseen and not any(h.category == "outside_world" and h.action in ("http_write", "spend") for h in run.hits):
        run.add("outside_world", "http_write", detail)


def _remote(run: _Run, name, args, cwd, detail, depth):
    ctx = run.ctx
    if name == "ssh":
        i, dest = 0, None
        value_short = "bcDEeFIiJLlmOopQRSWw"
        while i < len(args):
            a = args[i]
            if a.startswith("-") and len(a) > 1 and dest is None:
                if a[-1] in value_short and len(a) == 2:
                    i += 1
                i += 1
                continue
            dest = a
            i += 1
            break
        if dest is None:
            run.unknown(detail)
            return
        host = _host_of(dest.replace("ssh://", ""))
        rest = args[i:]
        if not _is_loopback(ctx, host):
            run.add("outside_world", "remote_shell", detail, recipient=host)
            return
        if not rest:
            run.unknown(detail)
            return
        _bash_text(run, " ".join(rest), ctx.home, depth + 1, detail)
        return
    pos = _positionals(args, frozenset(("-e", "-i", "-P", "-p", "-o", "-F", "-J", "-l", "-c", "--rsh", "-S",
                                        "-B", "--exclude", "--include", "--filter", "-f", "--chmod")))
    remote_hosts = []
    local = []
    for a in pos:
        m = re.match(r"^(?:[^@/:\s]+@)?(\[[^\]]+\]|[A-Za-z0-9_.-]+):", a)
        if m and not a.startswith("/"):
            remote_hosts.append((m.group(1).strip("[]").lower(), a[m.end():]))
        else:
            local.append(a)
    if name == "sftp":
        remote_hosts = [(_host_of(pos[0]), "")] if pos else []
    external = [h for h, _ in remote_hosts if not _is_loopback(ctx, h)]
    for h in dict.fromkeys(external):
        run.add("outside_world", "remote_shell", detail, recipient=h)
    if not external and len(pos) > 1:
        # a local copy (or a copy to this box): the destination is a write
        dest = pos[-1]
        m = re.match(r"^(?:[^@/:\s]+@)?(\[[^\]]+\]|[A-Za-z0-9_.-]+):", dest)
        if m and not dest.startswith("/"):
            dest = dest[m.end():] or "~"
        _write_check(run, [dest], cwd, detail)


def _sql_hit(run: _Run, args, heredoc, detail):
    text = " ".join(args) + "\n" + (heredoc or "")
    if DB_WRITE_RE.search(text):
        run.add("destructive", "drop_data", detail)


def _git(run: _Run, args, cwd, detail):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTS else 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    flags = _flag_set(rest)
    pos = [a for a in rest if not a.startswith("-")]
    if sub == "push":
        remote = pos[0] if pos else None
        run.add("outside_world", "push", detail, recipient=remote)
        if flags & {"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "-d", "--delete",
                    "--prune"} or any(p.startswith(("+", ":")) for p in pos[1:]):
            run.add("destructive", "force_push", detail, recipient=remote)   # rewrites or deletes remote refs
        return
    if sub == "reset" and "--hard" in flags:
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "clean":
        if "-f" in flags or "--force" in flags:
            run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "branch" and ("-D" in flags or ({"--delete", "-d"} & flags and {"--force", "-f"} & flags)):
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "checkout" and ("--" in rest or "." in pos or "-f" in flags or "--force" in flags):
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "restore" and not ({"--staged", "-S"} & flags and not {"--worktree", "-W"} & flags):
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "stash" and pos[:1] and pos[0] in ("drop", "clear"):
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "rm" and {"-f", "--force"} & flags:
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "worktree" and pos[:1] == ["remove"] and {"-f", "--force"} & flags:
        run.add("destructive", "delete_files", detail, folder=cwd)
        return
    if sub == "config" and {"--global", "--system"} & flags and \
            not {"--get", "--list", "-l", "--get-all", "--get-regexp"} & flags and len(pos) >= 2:
        run.unknown(detail)
        return
    if sub not in GIT_SAFE:
        run.unknown(detail)


def _gh(run: _Run, args, detail):
    pos = [a for a in args if not a.startswith("-")]
    if not pos:
        return
    group = pos[0]
    verb = pos[1] if len(pos) > 1 else None
    if group == "api":
        flags = _flag_set(args)
        method = None
        for k, a in enumerate(args):
            if a in ("-X", "--method") and k + 1 < len(args):
                method = args[k + 1].upper()
            elif a.startswith("--method="):
                method = a.split("=", 1)[1].upper()
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].upper()
        if method == "DELETE":
            run.add("destructive", "drop_data", detail)
        elif method in ("POST", "PUT", "PATCH") or flags & {"-f", "-F", "--field", "--raw-field", "--input"}:
            run.add("outside_world", "push", detail)
        return
    if group in GH_READ_GROUPS or group in ("status",):
        return
    table = GH_WRITE.get(group)
    if verb in GH_READ_VERBS:
        return
    if table is not None and verb in table:
        action = table[verb]
        if action is None:
            return
        cat = "destructive" if action == "drop_data" else "outside_world"
        run.add(cat, action, detail)
        return
    if group == "release" and verb:
        run.add("outside_world", "publish", detail)
        return
    run.unknown(detail)


def _deployer(run: _Run, name, args, detail):
    pos = [a for a in args if not a.startswith("-")]
    verbs = set()
    for p in pos[:2]:
        verbs.add(p)
        verbs.update(p.split(":"))
    if verbs & DEPLOY_VERBS or (name in ("vercel", "surge") and (not pos or pos[0].startswith((".", "/")))):
        run.add("outside_world", "deploy", detail)
        return
    if not pos and _flag_set(args) & {"--version", "-v", "-V", "--help", "-h"}:
        return
    if pos and pos[0] in DEPLOY_READ_VERBS:
        return
    run.unknown(detail)


def _scripts_for(ctx: CallCtx, cwd: str):
    if posixpath.normpath(cwd) == posixpath.normpath(ctx.cwd):
        return ctx.package_scripts
    if ctx.scripts_loader is not None:
        try:
            return ctx.scripts_loader(cwd)
        except Exception:  # noqa: BLE001
            return None
    return None


def _npm_script(run: _Run, script, cwd, depth, detail):
    scripts = _scripts_for(run.ctx, cwd)
    if scripts is None or script not in scripts or depth >= MAX_DEPTH:
        run.unknown(detail)
        return
    for s in ("pre" + script, script, "post" + script):
        if s in scripts and isinstance(scripts[s], str):
            _bash_text(run, scripts[s], cwd, depth + 1, detail)


def _local_bin(run: _Run, cwd, name) -> bool:
    if posixpath.normpath(cwd) == posixpath.normpath(run.ctx.cwd) and name in run.ctx.local_bins:
        return True
    return _bin_exists(cwd, name)


def _npx(run: _Run, args, cwd, depth, detail):
    i, fetch = 0, False
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-y", "--yes") or a.startswith("--package") or a == "-p":
            fetch = True
            if a in ("-p", "--package"):
                i += 1
        i += 1
    if i >= len(args) or fetch:
        run.add("install", "npm", detail)
        return
    pkg = args[i]
    name = pkg.split("@")[0] if not pkg.startswith("@") else pkg
    if _local_bin(run, cwd, name):
        _command(run, [name] + args[i + 1:], [], None, None, cwd, depth, detail)
    else:
        run.add("install", "npm", detail)


def _npm(run: _Run, name, args, cwd, depth, detail):
    # directory switches
    i, rest = 0, []
    workspace = False
    while i < len(args):
        a = args[i]
        if a in ("--prefix", "-C", "--cwd", "--dir") and i + 1 < len(args):
            cwd = _abspath(args[i + 1], cwd, run.ctx.home)
            i += 2
            continue
        if a.startswith(("--prefix=", "--cwd=", "--dir=")):
            cwd = _abspath(a.split("=", 1)[1], cwd, run.ctx.home)
            i += 1
            continue
        if a in ("-w", "--workspace", "--workspaces", "-ws", "--filter", "-r", "--recursive") or \
                a.startswith(("--workspace=", "--filter=")):
            workspace = True
        rest.append(a)
        i += 1
    pos = [a for a in rest if not a.startswith("-")]
    flags = _flag_set(rest)
    if not pos:
        if name == "yarn" and not flags:
            run.add("install", "npm", detail)
        elif name == "bun" and not flags:
            run.unknown(detail)
        return
    verb = pos[0]
    if verb == "publish":
        run.add("outside_world", "publish", detail)
        return
    if verb == "unpublish":
        run.add("destructive", "drop_data", detail)
        return
    if verb == "create" or verb in ("dlx", "x") and name != "npm" or verb == "init" and len(pos) > 1:
        run.add("install", "npm", detail)
        return
    if verb == "init":
        return
    if verb == "audit" and "fix" in pos:
        run.add("install", "npm", detail)
        return
    if verb in NPM_INSTALL_VERBS:
        run.add("install", "npm", detail)
        return
    if verb in ("exec",):
        k = rest.index("exec")
        _npx(run, rest[k + 1:], cwd, depth, detail)
        return
    if verb in NPM_READ_VERBS:
        if verb == "cache" and "clean" in pos:
            run.add("destructive", "delete_files", detail)
        return
    if workspace:
        run.unknown(detail)
        return
    if verb in ("test", "t", "tst", "start", "stop", "restart"):
        _npm_script(run, "test" if verb in ("t", "tst") else verb, cwd, depth, detail)
        return
    if verb in ("run", "run-script", "rum", "urn"):
        if len(pos) < 2:
            return
        _npm_script(run, pos[1], cwd, depth, detail)
        return
    if name in ("yarn", "pnpm", "bun"):
        scripts = _scripts_for(run.ctx, cwd)
        if scripts is not None and verb in scripts:
            _npm_script(run, verb, cwd, depth, detail)
            return
        if name != "bun" and _local_bin(run, cwd, verb):
            k = rest.index(verb)
            _command(run, [verb] + rest[k + 1:], [], None, None, cwd, depth, detail)
            return
    run.unknown(detail)


def _installer(run: _Run, name, args, detail) -> bool:
    pos = [a for a in args if not a.startswith("-") or a in ("-S", "-Syu")]
    if name == "go":
        if pos[:1] in (["install"], ["get"]):
            run.add("install", "other", detail)
            return True
        if pos[:1] and pos[0] in ("build", "test", "vet", "fmt", "version", "env", "list", "mod", "doc"):
            return True
        if pos[:1] == ["run"]:
            run.unknown(detail)
            return True
        return False
    if name == "cargo":
        if pos[:1] == ["install"]:
            run.add("install", "other", detail)
            return True
        if pos[:1] and pos[0] in ("build", "test", "check", "clippy", "fmt", "doc", "tree", "metadata", "--version"):
            return True
        if pos[:1] == ["publish"]:
            run.add("outside_world", "publish", detail)
            return True
        if pos[:1] == ["run"]:
            run.unknown(detail)
            return True
        return False
    if name == "gem":
        if pos[:1] == ["push"]:
            run.add("outside_world", "publish", detail)
            return True
        if pos[:1] == ["install"]:
            run.add("install", "other", detail)
            return True
        return False
    kind = INSTALLERS[name]
    if name == "pipx" and pos[:1] == ["run"]:
        run.add("install", "pip", detail)
        return True
    if pos and pos[0] in INSTALL_VERBS:
        run.add("install", kind, detail)
        return True
    if pos and pos[0] in ("list", "show", "freeze", "search", "info", "check", "policy", "--version", "config",
                          "download", "inspect", "cache", "doctor", "outdated", "leaves", "deps", "help"):
        return True
    if not pos and _flag_set(args) & {"--version", "-V", "-v", "--help", "-h"}:
        return True
    return False


def _inline_code(run: _Run, code: str, cwd: str, depth: int, detail: str, shell: bool):
    """Pattern hits found in inline interpreter code (the caller adds the uncertain hit)."""
    if shell:
        if depth < MAX_DEPTH:
            _bash_text(run, code, cwd, depth + 1, detail)
        return
    for m in INLINE_DELETE_RE.finditer(code):
        line = code[max(0, code.rfind("\n", 0, m.start()) + 1):].split("\n", 1)[0]
        run.add("destructive", "delete_files", line.strip() or detail)
        break
    urls = [u for u in URL_RE.findall(code)]
    hosts = [_host_of(u) for u in urls]
    external = [h for h in hosts if h and not _is_loopback(run.ctx, h)]
    paid = [h for h in external if _is_paid(h)]
    if paid and INLINE_ANY_HTTP_RE.search(code):
        for h in dict.fromkeys(paid):
            run.add("outside_world", "spend", detail, recipient=h)
    m = INLINE_HTTP_RE.search(code)
    if m:
        others = [h for h in external if not _is_paid(h)]
        if others:
            for h in dict.fromkeys(others):
                run.add("outside_world", "http_write", detail, recipient=h)
        elif not urls:
            run.add("outside_world", "http_write", detail)
    for m in INLINE_SUBPROC_RE.finditer(code):
        lit = m.group(1)
        if lit.startswith("["):
            parts = [a or b for a, b in STR_LIT_RE.findall(lit)]
            if not parts:
                continue
            if re.search(r"[A-Za-z_]", STR_LIT_RE.sub("", lit)):
                parts.append("\x00stdin")      # a non-literal argument: unknown target
            sub_cmd = " ".join(shlex.quote(p) for p in parts)
        else:
            body = lit.lstrip("f")
            sub_cmd = body[1:-1]
        if depth >= MAX_DEPTH:
            run.unknown(detail)
        else:
            _bash_text(run, sub_cmd, cwd, depth + 1, sub_cmd)


def _peeled_name(argv) -> str:
    for a in argv:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a) or a in PEEL_PLAIN or a in ("sudo", "env", "nice", "timeout"):
            continue
        return posixpath.basename(a)
    return ""


def _interpreter(run: _Run, name, args, heredoc, piped_from, cwd, depth, detail):
    """python / node / perl / ruby / shells / tsx / deno."""
    is_py = bool(PY_RE.match(name))
    shell = name in SHELLS
    code = None
    script = None
    module = None
    i = 0
    flags_done = False
    while i < len(args):
        a = args[i]
        if flags_done or not a.startswith("-") or a == "-":
            if a == "-":
                code = heredoc if heredoc is not None else ""
            else:
                script = a
            break
        if is_py and a == "-c" or name in ("node", "deno") and a in ("-e", "--eval", "-p", "--print") \
                or shell and a == "-c" or name in ("perl", "ruby") and a in ("-e", "-E"):
            code = args[i + 1] if i + 1 < len(args) else ""
            break
        if shell and a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
            code = args[i + 1] if i + 1 < len(args) else ""
            break
        if is_py and a == "-m":
            module = args[i + 1:] if i + 1 < len(args) else []
            break
        if is_py and a.startswith("-m") and len(a) > 2:
            module = [a[2:]] + args[i + 1:]
            break
        if a in ("-V", "--version", "-v", "--help", "-h") and not shell:
            return
        if name in ("node", "tsx", "deno", "bun") and a in ("--test", "--check", "-c"):
            return
        if shell and a == "-n":
            return
        if is_py and a in ("-W", "-X", "-O") or name == "node" and a in ("-r", "--require", "--import",
                                                                         "--loader"):
            i += 2
            continue
        i += 1
    if module is not None:
        mod = module[0] if module else ""
        margs = module[1:]
        if mod in ("pytest", "unittest", "py_compile", "json.tool", "compileall", "tabnanny", "doctest",
                   "mypy", "ruff", "black", "pyflakes", "timeit"):
            return
        if mod == "pip":
            if not _installer(run, "pip", margs, detail):
                run.unknown(detail)
            return
        if mod == "venv":
            return
        run.unknown(detail)
        return
    if code is None and script is None:
        if heredoc is not None:
            code = heredoc
        elif piped_from is not None:
            src = _peeled_name(piped_from.argv)
            if src in ("curl", "wget"):
                hosts = [_host_of(a) for a in piped_from.argv[1:] if not a.startswith("-") and
                         (("://" in a) or re.match(r"^[\w.-]+(:\d+)?(/|$)", a))]
                if not hosts or any(not _is_loopback(run.ctx, h) for h in hosts):
                    run.add("install", "script_pipe", detail)
                    return
            code = ""
        elif name == "eval":
            code = ""
        else:
            run.unknown(detail)   # interactive interpreter
            return
    if code is not None:
        run.unknown(detail)
        _inline_code(run, code, cwd, depth, detail, shell)
        return
    base = posixpath.basename(script)
    if base in FLEET_SCRIPTS:
        return
    if base == "seat_supervisor.py" and "status" in args[i + 1:]:
        return
    if name == "tsx" and "--test" in args:
        return
    run.unknown(detail)


def _command(run: _Run, argv, redirs, heredoc, piped_from, cwd, depth, detail=None):
    """Classify one simple command (argv already split; redirections separate)."""
    ctx = run.ctx
    argv = list(argv)
    # peel prefixes
    while argv:
        a = argv[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\+?=", a):
            argv.pop(0)
            continue
        if a == "command" and len(argv) > 1 and argv[1] in ("-v", "-V"):
            return
        if a in PEEL_PLAIN:
            argv.pop(0)
            if a == "time":
                while argv and argv[0].startswith("-"):
                    argv.pop(0)
            continue
        if a == "env":
            argv.pop(0)
            while argv and (argv[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0])):
                opt = argv.pop(0)
                if opt in ("-u", "--unset", "-C", "--chdir", "-S") and argv:
                    argv.pop(0)
            continue
        if a in ("timeout", "gtimeout"):
            argv.pop(0)
            while argv and argv[0].startswith("-"):
                opt = argv.pop(0)
                if opt in ("-s", "--signal", "-k", "--kill-after") and argv:
                    argv.pop(0)
            if argv:
                argv.pop(0)       # the duration
            continue
        if a in ("nice", "ionice", "stdbuf", "chrt", "taskset"):
            argv.pop(0)
            while argv and argv[0].startswith("-"):
                opt = argv.pop(0)
                if opt in ("-n", "-c", "-p", "--adjustment") and argv:
                    argv.pop(0)
            continue
        if a == "sudo" or a == "doas":
            argv.pop(0)
            run.add("destructive", "system_settings", detail or " ".join([a] + argv))
            while argv and argv[0].startswith("-"):
                opt = argv.pop(0)
                if opt in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U") and argv:
                    argv.pop(0)
            continue
        break
    if detail is None or depth == 0:
        detail = " ".join(argv + [f"{op} {t}" for op, t in redirs])
    # redirections
    for op, target in redirs:
        if op in (">", ">>", ">|", "&>", "&>>", "<>") or op == ">&" and not (target.isdigit() or target == "-"):
            _write_check(run, [target], cwd, detail)
            if _looks_pathlike(target) and is_secret_path(_abspath(target, cwd, ctx.home)):
                run.add("outside_folder", "secrets", detail)
        elif op == "<":
            if _looks_pathlike(target) and is_secret_path(_abspath(target, cwd, ctx.home)):
                run.add("outside_folder", "secrets", detail)
    if not argv:
        return
    name = posixpath.basename(argv[0])
    args = argv[1:]
    _secret_args(run, args, cwd, detail)

    if name == "cd" or name == "pushd":
        return
    if name in SAFE:
        return
    if name == "sed":
        inplace = any(a == "--in-place" or a.startswith("--in-place=") or
                      (a.startswith("-") and not a.startswith("--") and "i" in a[1:]) for a in args)
        if inplace:
            has_e = any(a in ("-e", "--expression", "-f", "--file") for a in args)
            pos = _positionals(args, frozenset(("-e", "--expression", "-f", "--file", "-l")))
            files = pos if has_e else pos[1:]
            _write_check(run, files, cwd, detail)
        return
    if name == "find":
        roots = []
        k = 0
        while k < len(args) and not args[k].startswith(("-", "(", "!")):
            roots.append(args[k])
            k += 1
        roots = roots or ["."]
        expr = args[k:]
        if "-delete" in expr:
            _delete_check(run, [posixpath.join(r, "x") for r in roots], cwd, detail, False)
        j = 0
        while j < len(expr):
            if expr[j] in ("-exec", "-execdir", "-ok", "-okdir"):
                sub = []
                j += 1
                while j < len(expr) and expr[j] not in (";", "+"):
                    sub.append(expr[j])
                    j += 1
                sub = [posixpath.join(roots[0], "x") if s == "{}" else s for s in sub]
                if sub:
                    if depth >= MAX_DEPTH + 1:
                        run.unknown(detail)
                    else:
                        _command(run, sub, [], None, None, cwd, depth + 1, detail)
            j += 1
        for k2, a in enumerate(expr):
            if a in ("-fprint", "-fprintf", "-fls") and k2 + 1 < len(expr):
                _write_check(run, [expr[k2 + 1]], cwd, detail)
        return
    if name == "xargs":
        k, repl = 0, None
        while k < len(args) and args[k].startswith("-"):
            a = args[k]
            if a in ("-I", "-i", "--replace") and a != "-i":
                repl = args[k + 1] if k + 1 < len(args) else "{}"
                k += 2
                continue
            if a.startswith("-I") and len(a) > 2:
                repl = a[2:]
            elif a in ("-n", "-P", "-d", "-L", "-a", "-E", "-s", "--max-args", "--max-procs", "--delimiter",
                       "--arg-file", "-e"):
                k += 1
            k += 1
        sub = args[k:] or ["echo"]
        if repl:
            sub = ["\x00stdin" if repl in s else s for s in sub]
        else:
            sub = sub + ["\x00stdin"]
        if depth >= MAX_DEPTH + 1:
            run.unknown(detail)
        else:
            _command(run, sub, [], None, None, cwd, depth + 1, detail)
        return
    if name in WRITE_CMDS:
        if name == "chmod" and ({"-R", "--recursive"} & _flag_set(args)):
            run.add("destructive", "system_settings", detail)
            return
        pos = _positionals(args, frozenset(("-t", "--target-directory", "-m", "--mode", "-S", "--suffix",
                                            "-o", "-g", "--owner", "--group")))
        tdir = None
        for k2, a in enumerate(args):
            if a in ("-t", "--target-directory") and k2 + 1 < len(args):
                tdir = args[k2 + 1]
            elif a.startswith("--target-directory="):
                tdir = a.split("=", 1)[1]
        if name == "chmod":
            targets = pos[1:]
        elif name in ("cp", "install"):
            targets = [posixpath.join(tdir, "x")] if tdir else pos[-1:] if len(pos) > 1 else []
        elif name == "mv":
            targets = (pos + ([posixpath.join(tdir, "x")] if tdir else [])) if len(pos) > 1 or tdir else []
        elif name == "ln":
            targets = pos[-1:] if len(pos) > 1 else ["./x"]
        else:  # tee mkdir touch
            targets = pos
        _write_check(run, targets, cwd, detail)
        return
    if name in DELETE_CMDS or name.startswith("mkfs"):
        if name == "dd":
            targets = [a[3:] for a in args if a.startswith("of=")]
        elif name == "truncate":
            targets = _positionals(args, frozenset(("-s", "--size", "-r", "--reference")))
        elif name.startswith("mkfs") or name == "wipefs":
            run.add("destructive", "delete_files", detail)
            return
        else:
            targets = _positionals(args)
        recursive = name == "rmdir" or bool({"-r", "-R", "--recursive"} & _flag_set(args))
        _delete_check(run, targets, cwd, detail, recursive)
        return
    if name in KILL_CMDS:
        run.add("destructive", "kill_process", detail)
        return
    if name in SYSTEM_CMDS:
        pos = [a for a in args if not a.startswith("-")]
        flags = _flag_set(args)
        reads = (
            name == "systemctl" and (not pos or pos[0] in ("status", "is-active", "is-enabled", "is-failed",
                                                           "list-units", "list-timers", "list-unit-files",
                                                           "show", "cat", "list-dependencies"))
            or name == "service" and (pos[-1:] == ["status"] or "--status-all" in flags)
            or name == "crontab" and flags == {"-l"}
            or name == "launchctl" and pos[:1] in (["list"], ["print"])
            or name == "sysctl" and not any("=" in a for a in args) and "-w" not in flags
            or name == "ufw" and pos[:1] == ["status"]
            or name == "iptables" and flags & {"-L", "-S", "--list"} and not flags & {"-A", "-D", "-I", "-F"}
            or name == "mount" and not args
        )
        if not reads:
            run.add("destructive", "system_settings", detail)
        return
    if name in DB_CMDS:
        _sql_hit(run, args, heredoc, detail)
        return
    if name == "git":
        _git(run, args, cwd, detail)
        return
    if name == "gh":
        _gh(run, args, detail)
        return
    if name in DEPLOYERS:
        _deployer(run, name, args, detail)
        return
    if name in PUBLISHERS and [a for a in args if not a.startswith("-")][:1] == [PUBLISHERS[name]]:
        run.add("outside_world", "publish", detail)
        return
    if name in ("docker", "podman"):
        pos = [a for a in args if not a.startswith("-")]
        if pos[:1] == ["push"]:
            run.add("outside_world", "publish", detail)
        elif pos[:1] and pos[0] in ("ps", "logs", "images", "inspect", "version", "info", "stats", "top"):
            return
        else:
            run.unknown(detail)
        return
    if name in MAILERS:
        run.add("outside_world", "message", detail)
        return
    if name in REMOTE_CMDS:
        _remote(run, name, args, cwd, detail, depth)
        return
    if name in HTTP_CMDS:
        _http(run, name, args, cwd, detail, depth, piped_from=piped_from)
        return
    if name in NPM_FAMILY:
        _npm(run, name, args, cwd, depth, detail)
        return
    if name in NPX_LIKE:
        _npx(run, args, cwd, depth, detail)
        return
    if name == "uv":
        pos = [a for a in args if not a.startswith("-")]
        if pos[:2] == ["pip", "install"] or pos[:1] in (["add"], ["sync"]) or pos[:2] == ["tool", "install"]:
            run.add("install", "pip", detail)
        elif pos[:1] == ["run"]:
            k = args.index("run")
            _command(run, args[k + 1:], [], heredoc, piped_from, cwd, depth, detail)
        elif pos[:2] in (["pip", "list"], ["pip", "show"], ["pip", "freeze"]):
            return
        else:
            run.unknown(detail)
        return
    if name in INSTALLERS:
        if not _installer(run, name, args, detail):
            run.unknown(detail)
        return
    if name in DEV_TOOLS:
        return
    if name == "tsx":
        if "--test" in args or not [a for a in args if not a.startswith("-")]:
            return
        run.unknown(detail)
        return
    if name in UNINSPECTED:
        run.unknown(detail)
        return
    if name == "tmux":
        k = 0
        while k < len(args) and args[k].startswith("-"):
            k += 2 if args[k] in ("-L", "-S", "-f") else 1
        sub = args[k] if k < len(args) else None
        if sub in TMUX_KILL:
            run.add("destructive", "kill_process", detail)
        elif sub is None or sub not in TMUX_SAFE:
            run.unknown(detail)
        return
    if name == "orchestra" and args[:1] == ["gate"] and len(args) > 1 and args[1] in ("mode", "retire-open"):
        run.add("destructive", "system_settings", detail)   # switches the safety gate itself (like gate.json)
        return
    if name in FLEET_SCRIPTS or name == "orchestra":
        return
    if name == "seat_supervisor.py" and "status" in args:
        return
    if name in ("source", ".") and args and args[0].endswith("/bin/activate"):
        return
    if name == "eval":
        run.unknown(detail)
        _inline_code(run, " ".join(args), cwd, depth, detail, True)
        return
    if PY_RE.match(name) or name in SHELLS or name in ("node", "perl", "ruby", "deno", "bun", "ts-node", "php"):
        _interpreter(run, name, args, heredoc, piped_from, cwd, depth, detail)
        return
    run.unknown(detail)


def _bash_text(run: _Run, command: str, cwd: str, depth: int, detail: str | None = None):
    if depth > MAX_DEPTH:
        run.unknown(detail or command)
        return
    if depth > 0:
        try:
            _bash_body(run, command, cwd, depth, detail)
        except ValueError:            # nested text that is not valid shell: uncertain, keep the other hits
            run.unknown(detail or command)
        return
    _bash_body(run, command, cwd, depth, detail)


def _bash_body(run: _Run, command: str, cwd: str, depth: int, detail: str | None):
    for rx in SCRIPT_PIPE_RES:
        m = rx.search(command)
        if m:
            run.add("install", "script_pipe", detail if depth else command)
            break
    for cmd in _split(command):
        argv = cmd.argv
        # $(...) and backticks inside quoted words
        for w in argv:
            k = w.find("$(")
            while k != -1:
                level, j = 0, k + 1
                while j < len(w):
                    if w[j] == "(":
                        level += 1
                    elif w[j] == ")":
                        level -= 1
                        if level == 0:
                            break
                    j += 1
                inner = w[k + 2:j]
                if inner.strip():
                    _bash_text(run, inner, cwd, depth, None if depth == 0 else detail)
                k = w.find("$(", j)
        peeled = [a for a in argv if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a)]
        if peeled[:1] and peeled[0] in ("cd", "pushd"):
            if len(peeled) > 1 and peeled[1] != "-":
                cwd = _abspath(peeled[1], cwd, run.ctx.home)
            elif len(peeled) == 1:
                cwd = run.ctx.home
        _command(run, argv, cmd.redirs, cmd.heredoc, cmd.piped_from, cwd, depth, None if depth == 0 else detail)


def _path_hits(run: _Run, tool: str, path: str, write: bool):
    ctx = run.ctx
    if not path or not isinstance(path, str):
        if write:
            run.unknown(f"{tool} (no path)")
        return
    p = _abspath(path, ctx.cwd, ctx.home)
    detail = f"{tool} {p}"
    if write and not _is_writable_here(ctx, p):
        run.add("outside_folder", "edit_outside", detail, folder=posixpath.dirname(p))
    if is_secret_path(p) or is_secret_path(_real(ctx, p)):
        run.add("outside_folder", "secrets", detail, folder=posixpath.dirname(p))


MCP_WRITE = frozenset(("send", "post", "publish", "deploy", "create", "update", "reply", "comment", "share",
                       "invite", "upload", "write", "insert", "apply", "label", "move"))
MCP_DELETE = frozenset(("delete", "trash", "remove", "drop"))
MCP_READ = frozenset(("get", "list", "search", "read", "query", "fetch", "find", "describe", "guide", "export",
                      "authenticate"))


def _mcp(run: _Run, tool: str):
    parts = tool.split("__")
    server = parts[1] if len(parts) > 1 else ""
    name = parts[2] if len(parts) > 2 else ""
    words = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower().replace("-", "_").split("_")
    verb = words[0] if words else ""
    if verb in MCP_WRITE:
        run.add("outside_world", "connector_write", tool, recipient=server)
    elif verb in MCP_DELETE:
        run.add("destructive", "drop_data", tool, recipient=server)
    elif verb in MCP_READ:
        return
    else:
        run.unknown(tool)


def classify(call: CallCtx) -> list:
    run = _Run(call)
    tool = call.tool or ""
    inp = call.tool_input if isinstance(call.tool_input, dict) else {}
    try:
        if tool == "Bash":
            cmd = inp.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                run.unknown("Bash (no command)")
            else:
                _bash_text(run, cmd, call.cwd, 0)
        elif tool in EDIT_TOOLS:
            _path_hits(run, tool, inp.get("file_path") or inp.get("notebook_path"), True)
        elif tool in READ_TOOLS:
            if tool in ("Read", "Grep", "Glob", "LS", "NotebookRead"):
                for key in ("file_path", "path", "notebook_path", "pattern" if tool == "Glob" else ""):
                    if key and isinstance(inp.get(key), str):
                        _path_hits(run, tool, inp[key], False)
        elif tool == "Artifact":
            action = inp.get("action") or "publish"
            if action == "publish":
                run.add("outside_world", "publish", "Artifact publish")
            elif action == "delete":
                run.add("destructive", "drop_data", "Artifact delete")
            elif action not in ("read", "list", "open", "quickstart", "pin", "unpin"):
                run.unknown(f"Artifact {action}")
        elif tool.startswith("mcp__"):
            _mcp(run, tool)
        else:
            run.unknown(tool or "(no tool)")
    except ValueError:
        return [Hit("destructive", "unrecognised", redact(f"{tool}: could not parse"), False)]
    except RecursionError:
        return [Hit("destructive", "unrecognised", redact(f"{tool}: too deeply nested"), False)]
    except Exception as exc:  # noqa: BLE001 - never crash the hook; an unreadable call is uncertain
        return [Hit("destructive", "unrecognised", redact(f"{tool}: classifier error {type(exc).__name__}"),
                    False)]
    return run.hits
