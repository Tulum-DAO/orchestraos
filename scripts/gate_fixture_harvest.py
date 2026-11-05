#!/usr/bin/env python3
"""Count the SHAPES of real tool calls and re-generate them synthetically for the gate's fixtures.

Nothing from a transcript is ever copied. The harvest reads Claude Code transcripts READ-ONLY, turns each
tool call into a shape with an allow-list serializer (command names, subcommands and flags from the
classifier's tables pass through; every other argument becomes a placeholder token such as
<path-in-folder>, <host-external> or <word>), and prints one JSON line per distinct shape with its count.
Heredoc bodies and inline code are replaced by synthetic lines, one per classifier pattern they hit.

    python3 scripts/gate_fixture_harvest.py --max 300 > /tmp/gate-shapes.jsonl
    python3 scripts/gate_fixture_harvest.py --check /tmp/gate-shapes.jsonl scripts/fixtures/gate/*.jsonl

--check prints findings (token-like strings, 32+ hex runs, home folders other than /home/orchestra, this
machine's user and host names, real-looking e-mail addresses and IP addresses) and exits 1 if any.
The test harness expands placeholders to fixed fake values with expand().
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import posixpath
import re
import shlex
import socket
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_classify as GC  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Placeholders (expanded by the tests)
# ---------------------------------------------------------------------------------------------

PLACEHOLDERS = {
    "<path-in-folder>": "/home/orchestra/proj/src/a.txt",
    "<path-outside>": "/etc/x",
    "<path-scratch>": "/tmp/work/a.txt",
    "<path-secret>": "/home/orchestra/proj/.env",
    "<host-loopback>": "127.0.0.1",
    "<host-external>": "example.com",
    "<host-paid>": "api.openai.com",
    "<email>": "someone@example.com",
    "<text>": "text",
    "<num>": "123",
    "<word>": "word",
}
FAKE_HOME = "/home/orchestra"
FAKE_CWD = "/home/orchestra/proj"
FAKE_OWN = [FAKE_CWD, FAKE_HOME + "/.orchestra/state", FAKE_HOME + "/.orchestra/.workspace"]
FAKE_BINS = ["eslint", "playwright", "prettier", "tsc", "tsx", "vite", "vitest"]
FAKE_SCRIPTS = {"build": "tsc -b && vite build", "build:check": "tsc -b && vite build --mode check",
                "dev": "vite", "lint": "eslint .", "test": "vitest run", "typecheck": "tsc --noEmit"}


def expand(obj):
    """Replace every placeholder token in every string of obj with its fixed fake value."""
    if isinstance(obj, str):
        for k, v in PLACEHOLDERS.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, list):
        return [expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: expand(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------------------------
# Privacy check
# ---------------------------------------------------------------------------------------------

_HEX = re.compile(r"[0-9a-fA-F]{32,}")
_HOME = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_FAKE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")


def default_deny() -> list:
    """This machine's own names: its user, home folder and host name parts."""
    words = set()
    home = os.path.expanduser("~")
    if home and home not in ("/", FAKE_HOME):
        words.add(home)
    for w in (os.environ.get("USER"), os.environ.get("LOGNAME"), posixpath.basename(home)):
        if w and w not in ("agent", "root", "runner") and len(w) >= 3:
            words.add(w)
    host = socket.gethostname()
    for part in re.split(r"[-_.]", host):
        if len(part) >= 5:
            words.add(part)
    extra = os.environ.get("GATE_CHECK_DENY", "")
    words.update(w for w in extra.split(",") if w.strip())
    return sorted(words)


def check_text(text: str, deny=None) -> list:
    """Findings as (line number, kind). Never echoes the matched text."""
    deny = default_deny() if deny is None else list(deny)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for k, rx in enumerate(GC.REDACT_RES):
            if rx.search(line):
                out.append((n, f"secret-pattern-{k}"))
        if _HEX.search(line):
            out.append((n, "hex-run"))
        for m in _HOME.finditer(line):
            if m.group(1) != posixpath.basename(FAKE_HOME):
                out.append((n, "home-folder"))
        for m in _EMAIL.finditer(line):
            if m.group(1).lower() not in _FAKE_EMAIL_DOMAINS:
                out.append((n, "email"))
        for m in _IPV4.finditer(line):
            a, b = int(m.group(1)), int(m.group(2))
            if not (a == 127 or a == 10 or (a, b) == (0, 0)):
                out.append((n, "ip-address"))
        low = line.lower()
        for w in deny:
            if w.lower() in low:
                out.append((n, "machine-name"))
    return out


# ---------------------------------------------------------------------------------------------
# The allow-list serializer
# ---------------------------------------------------------------------------------------------

def _vocab() -> frozenset:
    words = set()
    for name in ("SAFE", "WRITE_CMDS", "DELETE_CMDS", "KILL_CMDS", "SYSTEM_CMDS", "DB_CMDS", "GIT_SAFE",
                 "GH_READ_VERBS", "GH_READ_GROUPS", "DEPLOYERS", "DEPLOY_VERBS", "DEPLOY_READ_VERBS", "MAILERS",
                 "REMOTE_CMDS", "HTTP_CMDS", "NPM_FAMILY", "NPM_INSTALL_VERBS", "NPM_READ_VERBS", "NPX_LIKE",
                 "INSTALL_VERBS", "DEV_TOOLS", "UNINSPECTED", "FLEET_SCRIPTS", "TMUX_SAFE", "TMUX_KILL",
                 "SHELLS", "CURL_VALUE", "CURL_WRITE_OPTS", "WGET_VALUE", "GIT_VALUE_OPTS", "MCP_WRITE",
                 "MCP_DELETE", "MCP_READ"):
        words.update(getattr(GC, name))
    words.update(GC.INSTALLERS)
    words.update(GC.PUBLISHERS)
    words.update(GC.PUBLISHERS.values())
    for group, verbs in GC.GH_WRITE.items():
        words.add(group)
        words.update(verbs)
    words.update(FAKE_SCRIPTS)
    words.update(FAKE_BINS)
    words.update((
        "gh", "git", "api", "push", "reset", "clean", "origin", "main", "master", "HEAD", "HEAD~1", "run", "exec",
        "python", "python3", "node", "perl", "ruby", "deno", "uv", "pipx", "eval", "sudo", "env", "timeout", "nice",
        "nohup", "time", "xargs", "sed", "find", "tmux", "orchestra", "seat_supervisor.py", "send", "inbox", "ack",
        "status", "list", "read", "-delete", "-exec", "-execdir", "-name", "-type", "-maxdepth", "-mindepth",
        "-newer", "-mtime", "-print", "-path", "-not", "-o", "-iname", "f", "d", "{}", ";", "+", "--hard",
        "--soft", "--force", "--force-with-lease", "--prod", "--dir", "--yes", "--no-install", "--to", "--from",
        "--body", "--title", "--json", "--oneline", "--stat", "--cached", "--staged", "--name-only",
        "--porcelain", "--short", "--abbrev-ref", "--show-toplevel", "--no-pager", "-C", "-m", "-am", "-b",
        "-B", "--test", "--noEmit", "-b", "--build", "--fix", "--write", "--check", "--mode", "--max-warnings",
        "--prefix", "--workspace", "--", "-y", "-p", "--package", "-q", "-x", "-v", "-vv", "-k", "-s", "-sS",
        "-fsSL", "-sSL", "-fsS", "-X", "POST", "GET", "PUT", "PATCH", "DELETE", "-H", "-d", "--data",
        "--data-raw", "-o", "-O", "-w", "-t", "-l", "-n", "-c", "-e", "-r", "-R", "-rf", "-fr", "-f", "-i",
        "-a", "-la", "-lah", "-al", "-1", "-u", "-E", "-F", "-T", "-P", "-I", "-L", "-N", "-S", "-z", "-0",
        "Enter", "C-c", "C-m", "Escape", "-TERM", "-KILL", "-9", "-15", "-HUP", "-INT", "-pane", "-p",
        "capture-pane", "send-keys", "-J", "-S", "-E", "-lc", "-", "pytest", "unittest", "pip", "venv",
        "json.tool", "py_compile", "-m", "-u", "-B", "restart", "start", "stop", "reload", "enable", "disable",
        "daemon-reload", "--user", "--now", "TABLE", "DROP", "DELETE", "FROM", "SELECT", "-cmd", ".tables",
        ".schema", "--global", "--local", "user.name", "user.email", "config", "--get", "--list",
        "deploy", "publish", "--squash", "--merge", "--delete-branch", "--fill", "--draft", "--base",
        "--head", "--web", "--repo", "-R", "--limit", "--state", "--jq", "--paginate", "--method", "-f",
        "-F", "--field", "--raw-field", "--input", "--no-verify", "--amend", "--no-edit", "-A", "--all",
        "--tags", "--set-upstream", "-uall", "--prune", "-D", "-d", "--delete", "-fd", "-fdx", "-xdf",
        "--detach", "--no-ff", "--ff-only", "--continue", "--abort", "--onto", "--", "--test-name-pattern",
        "--reporter", "--run", "--watch", "--coverage", "--update", "-ra", "-q", "--tb=short", "-p",
        "no:cacheprovider", "--maxfail", "--lf", "-x", "-k", "--version", "-V", "--help", "-h", "-g",
        "--save-dev", "-D", "--save", "--no-save", "--legacy-peer-deps", "--frozen-lockfile", "install", "i",
        "-r", "requirements.txt", "--upgrade", "--user", "-e", ".", "..", "./", "/dev/null", "1", "2", "2>&1",
        "-lt", "-ltr", "-1t", "-A", "-type", "-print0", "-0", "--color=never", "-rn", "-rl", "-n", "-c",
        "-iE", "-E", "-o", "-h", "-H", "--include", "--exclude", "--exclude-dir", "-w", "-v", "-F", "-q",
        "-A3", "-B3", "-C3", "-5", "-20", "-50", "-100", "-200", "+1",
        "do", "done", "then", "fi", "if", "else", "elif", "while", "until", "case", "esac", "in", "for",
        "function", "select", "{", "}", "!", "[", "]", "[[", "]]", "-n", "-z", "-gt", "-lt", "-eq", "-ne",
        "-ge", "-le", "=", "==", "!=", "&&", "||",
    ))
    return frozenset(words)


VOCAB = _vocab()
_SHORT_FLAG = re.compile(r"^-[A-Za-z]{1,5}$")
_NUM = re.compile(r"^[+-]?\d+(\.\d+)?[smhdkKMG%]?$")
_EMAIL_TOK = re.compile(r"^[^@\s/]+@[^@\s/]+\.[A-Za-z]{2,}$")
_HOSTPORT = re.compile(r"^(localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)"
                       r"(:\d+)?(/\S*)?$")
_SCP = re.compile(r"^(?:[^@/:\s]+@)?(\[[^\]]+\]|[A-Za-z0-9_.-]+):(.*)$")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class Shaper:
    """Turns one real call into a synthetic, placeholder-only call of the same shape."""

    def __init__(self, folder: str, home: str):
        self.folder = posixpath.normpath(folder or home)
        self.home = home
        self.own = [self.folder, posixpath.join(home, ".orchestra", "state"),
                    posixpath.join(home, ".orchestra", ".workspace")]

    # -- single values -------------------------------------------------------------------------
    def host(self, host: str) -> str:
        host = host.strip("[]").lower()
        if host in ("127.0.0.1", "localhost", "::1") or host.startswith("127."):
            return "<host-loopback>"
        if GC._is_paid(host):
            return "<host-paid>"
        return "<host-external>"

    def url(self, tok: str) -> str:
        h = self.host(GC._host_of(tok))
        if h == "<host-loopback>":
            return "http://<host-loopback>:8890/x"
        if h == "<host-paid>":
            return "https://<host-paid>/v1/x"
        return "https://<host-external>/x"

    def path(self, tok: str, cwd: str) -> str:
        base = posixpath.basename(tok.rstrip("/"))
        if base in GC.FLEET_SCRIPTS or base == "seat_supervisor.py":
            return "scripts/" + base
        p = GC._abspath(tok, cwd, self.home)
        if GC.is_secret_path(p):
            return "<path-secret>"
        if GC.inside(p, "/tmp"):
            return "<path-scratch>"
        if any(GC.inside(p, d) for d in self.own):
            return "<path-in-folder>"
        return "<path-outside>"

    def word(self, tok: str, cwd: str, first: bool = False) -> str:
        if tok in VOCAB:
            return tok
        if first:
            base = posixpath.basename(tok)
            if base in VOCAB or GC.PY_RE.match(base):
                return base if not GC.PY_RE.match(base) else "python3"
        if tok.startswith("--") and "=" in tok:
            name, val = tok.split("=", 1)
            return (name + "=" + self.word(val, cwd)) if name in VOCAB else "<word>"
        if tok.startswith("-") and len(tok) > 1:
            return tok if _SHORT_FLAG.match(tok) else "<word>"
        if any(c.isspace() for c in tok):
            return "<text>"
        if "://" in tok:
            return self.url(tok)
        if _EMAIL_TOK.match(tok):
            return "<email>"
        if _NUM.match(tok):
            return "<num>"
        m = _HOSTPORT.match(tok)
        if m and (m.group(4) or m.group(1) == "localhost" or re.match(r"^\d", m.group(1))) and "/" not in tok.split(":")[0]:
            return self.url(tok)
        m = _SCP.match(tok)
        if m and getattr(self, "cmd0", None) in GC.REMOTE_CMDS and not tok.startswith(("/", ".", "~")) and m.group(2) is not None and ":" in tok and not tok.endswith(":"):
            rest = m.group(2)
            return self.host(m.group(1)) + ":" + (self.path(rest, "/") if rest else "")
        if GC._looks_pathlike(tok) or tok.startswith("/"):
            return self.path(tok, cwd)
        return "<word>"

    # -- inline code and heredocs ----------------------------------------------------------------
    def code(self, code: str, cwd: str, depth: int) -> list:
        """Synthetic code lines, one per classifier pattern the real code hits."""
        lines = []
        if GC.INLINE_DELETE_RE.search(code):
            lines.append("import shutil; shutil.rmtree(p)")
        urls = GC.URL_RE.findall(code)
        for h in dict.fromkeys(self.host(GC._host_of(u)) for u in urls):
            lines.append('url = "%s"' % {"<host-loopback>": "http://<host-loopback>:8890/x",
                                         "<host-paid>": "https://<host-paid>/v1/x"}.get(h, "https://<host-external>/x"))
        if GC.INLINE_HTTP_RE.search(code):
            lines.append("requests.post(url, json=body)")
        elif GC.INLINE_ANY_HTTP_RE.search(code):
            lines.append("urlopen(url)")
        for m in GC.INLINE_SUBPROC_RE.finditer(code):
            lit = m.group(1)
            if lit.startswith("["):
                parts = [a or b for a, b in GC.STR_LIT_RE.findall(lit)]
                inner = " ".join(shlex.quote(p) for p in parts)
            else:
                inner = lit.lstrip("f")[1:-1]
            if depth < 2:
                shaped = self.bash(inner, cwd, depth + 1)
                lines.append("subprocess.run(%s, shell=True)" % json.dumps(shaped))
        return lines or ["pass"]

    def bash(self, command: str, cwd: str, depth: int = 0) -> str:
        try:
            toks, bodies = GC._lex(command)
        except ValueError:
            return "echo '<text>"          # keep the "unparseable" shape
        out, pending = [], []
        first, cmd0, prev = True, None, None
        cmd_start = 0
        marker = "EOF" if depth == 0 else "EOF%d" % depth
        eff = cwd
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in GC.SEPARATORS or t in GC.REDIRS or t == "$" and i + 1 < len(toks) and toks[i + 1] == "(":
                if t == "<<" and i + 1 < len(toks) and GC.HEREDOC_TOKEN.match(toks[i + 1]):
                    k = int(GC.HEREDOC_TOKEN.match(toks[i + 1]).group(1))
                    body = bodies[k] if k < len(bodies) else ""
                    out.append("<<'%s'" % marker)
                    pending.append(self.heredoc(cmd0, body, eff, depth))
                    i += 2
                    continue
                if t == "\n" and pending:
                    out.extend(["\n", "\n".join(b + "\n" + marker for b in pending)])
                    pending = []
                out.append("\n" if t == "\n" else t)
                if t in GC.SEPARATORS or t == "$":
                    first, cmd0 = True, None
                    cmd_start = len(out)
                if t == "$":
                    out[-1] = "$("
                    i += 2
                    continue
                prev = t
                i += 1
                continue
            if first and _ASSIGN.match(t):
                out.append("FOO=<word>")
                i += 1
                continue
            if first:
                w = self.word(t, eff, first=True)
                out.append(w)
                if w not in ("sudo", "env", "timeout", "nice", "nohup", "time", "exec", "if", "then", "do",
                             "while", "!", "{", "else"):
                    first, cmd0 = False, w
                elif w == "timeout" and i + 1 < len(toks) and _NUM.match(toks[i + 1]):
                    out.append("<num>")
                    i += 1
                prev = t
                i += 1
                continue
            self.cmd0 = cmd0
            if cmd0 == "cd" and prev == "cd":
                eff = GC._abspath(t, eff, self.home)
            if (cmd0 in GC.SHELLS and prev in ("-c", "-lc") or cmd0 == "eval") and depth < 2:
                out.append(shlex.quote(self.bash(t, eff, depth + 1)))
            elif (cmd0 == "python3" and prev == "-c" and "-m" not in out[cmd_start:] or
                  cmd0 in ("node", "perl", "ruby") and prev in ("-e", "-E", "--eval", "-p", "--print")):
                out.append(shlex.quote("; ".join(self.code(t, eff, depth))))
            elif cmd0 == "ssh" and any(c.isspace() for c in t) and depth < 2:
                out.append(shlex.quote(self.bash(t, eff, depth + 1)))
            else:
                w = self.word(t, eff)
                out.append(w if w.startswith("<") and w.endswith(">") or w in VOCAB else shlex.quote(w))
            prev = t
            i += 1
        text = ""
        for k, w in enumerate(out):
            if w == "\n":
                text = text.rstrip(" ") + "\n"
            elif w == "$(":
                text += "$("
            else:
                nxt = out[k + 1] if k + 1 < len(out) else ""
                glue = nxt == ")" or (w.isdigit() and nxt in GC.REDIRS) or (w in (">&", "<&") and nxt.isdigit())
                text += w + ("" if glue else " ")
        text = text.replace("( ", "(").strip()
        for body in pending:
            text += "\n" + body + "\n" + marker
        return text

    def heredoc(self, cmd0, body: str, cwd: str, depth: int) -> str:
        if cmd0 in ("python3", "node", "perl", "ruby"):
            return "\n".join(self.code(body, cwd, depth))
        if cmd0 in GC.SHELLS and depth < 2:
            return self.bash(body, cwd, depth + 1)
        if cmd0 in GC.DB_CMDS:
            return "DELETE FROM t;" if GC.DB_WRITE_RE.search(body) else "SELECT 1;"
        return "<text>"

    # -- whole calls -----------------------------------------------------------------------------
    def call(self, tool: str, inp: dict):
        if tool == "Bash":
            cmd = inp.get("command") if isinstance(inp, dict) else None
            if not isinstance(cmd, str):
                return None
            return {"command": self.bash(cmd, self.folder)}
        if tool in GC.EDIT_TOOLS or tool in ("Read", "Grep", "Glob", "LS"):
            shaped = {}
            for k, v in (inp or {}).items():
                if k in ("file_path", "notebook_path", "path") and isinstance(v, str):
                    shaped[k] = self.path(v, self.folder)
                elif k == "pattern" and tool == "Glob" and isinstance(v, str):
                    shaped[k] = "**/.env" if GC.is_secret_path("/" + v) else "**/*.ts"
                elif k == "edits":
                    shaped[k] = []
                elif isinstance(v, str):
                    shaped[k] = "<text>"
                elif isinstance(v, (bool, int)) or v is None:
                    shaped[k] = v
            return shaped
        if tool == "Artifact":
            action = (inp or {}).get("action")
            known = {"publish", "read", "list", "open", "quickstart", "pin", "unpin", "delete"}
            return {"action": action} if action in known else {}
        return {}

    def tool_name(self, tool: str) -> str:
        if not tool.startswith("mcp__"):
            return tool
        parts = tool.split("__")
        name = parts[2] if len(parts) > 2 else ""
        verb = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower().replace("-", "_").split("_")[0]
        known = GC.MCP_WRITE | GC.MCP_DELETE | GC.MCP_READ
        return "mcp__svc__" + (verb + "_item" if verb in known else "<word>")


# ---------------------------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------------------------

KEEP_TOOLS = frozenset(("Bash", "Edit", "Write", "MultiEdit", "Read", "Artifact"))
QUOTAS = {"inline": 60, "curl": 40, "git": 40, "npm": 30, "rm_kill": 30, "fleet": 30, "paths": 70}


def bucket(tool: str, shaped: dict) -> str:
    if tool.startswith("mcp__") or tool == "Artifact":
        return "mcp"
    if tool != "Bash":
        return "paths"
    c = shaped.get("command", "")
    if re.search(r"\b(python3|node|perl|ruby)\s+(-c|-e|-)\b|\bpython3\s+<<|\b(bash|sh|zsh)\s+-l?c\b", c):
        return "inline"
    if re.search(r"\b(curl|wget)\b", c):
        return "curl"
    if re.search(r"(^|\s)(git|gh)\s", c):
        return "git"
    if re.search(r"\b(npm|npx|pnpm|yarn|bun|bunx|pnpx)\b", c):
        return "npm"
    if re.search(r"(^|[\s;&|(])(rm|rmdir|kill|pkill|killall)\s", c):
        return "rm_kill"
    if re.search(r"\btmux\b|msg_store\.py|approval\.py|agent-status\.py", c):
        return "fleet"
    return "other"


def iter_calls(pattern: str):
    for path in sorted(glob.glob(pattern)):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        yield rec.get("cwd") or "", block.get("name") or "", block.get("input") or {}


def harvest(pattern: str, max_lines: int, home: str):
    counts: Counter = Counter()
    by_tool: Counter = Counter()
    for cwd, tool, inp in iter_calls(pattern):
        by_tool[tool] += 1
        if tool not in KEEP_TOOLS and not tool.startswith("mcp__"):
            continue
        shaper = Shaper(cwd, home)
        try:
            shaped = shaper.call(tool, inp)
        except Exception:  # noqa: BLE001 - a shape we cannot make is skipped, never copied
            continue
        if shaped is None:
            continue
        key = json.dumps([shaper.tool_name(tool), shaped], sort_keys=True)
        counts[key] += 1
    buckets = defaultdict(list)
    for key, n in counts.items():
        tool, shaped = json.loads(key)
        buckets[bucket(tool, shaped)].append((n, key, tool, shaped))
    chosen = []
    for b in buckets.values():
        b.sort(key=lambda x: (-x[0], x[1]))
    for name, quota in QUOTAS.items():
        chosen += buckets.get(name, [])[:quota]
    chosen += buckets.get("mcp", [])
    rest = sorted([x for name in ("other",) for x in buckets.get(name, [])] +
                  [x for name, q in QUOTAS.items() for x in buckets.get(name, [])[q:]], key=lambda x: (-x[0], x[1]))
    chosen += rest[:max(0, max_lines - len(chosen))]
    chosen = chosen[:max_lines]
    rows = []
    for k, (n, _key, tool, shaped) in enumerate(chosen, 1):
        row = {"id": "h%03d" % k, "tool": tool, "input": shaped, "n": n, "bucket": bucket(tool, shaped)}
        cmd = shaped.get("command", "") if tool == "Bash" else ""
        if re.search(r"\b(npm|npx|pnpm|yarn|bun|bunx|pnpx)\b", cmd):
            row["local_bins"] = FAKE_BINS
            row["package_scripts"] = FAKE_SCRIPTS
        rows.append(row)
    return rows, by_tool


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    ap.add_argument("--check", nargs="+", metavar="FILE")
    ap.add_argument("--deny", action="append", default=[], help="extra words --check must not find")
    ap.add_argument("--summary", action="store_true", help="print tool-name counts to stderr")
    a = ap.parse_args(argv)
    if a.check:
        deny = default_deny() + a.deny
        total = 0
        for f in a.check:
            with open(f, encoding="utf-8") as fh:
                found = check_text(fh.read(), deny)
            for n, kind in found:
                print(f"{f}:{n}: {kind}")
            total += len(found)
        print(f"{total} findings")
        return 1 if total else 0
    rows, by_tool = harvest(a.projects, a.max, os.path.expanduser("~"))
    for row in rows:
        print(json.dumps(row, sort_keys=True))
    if a.summary:
        print(json.dumps({"tools": dict(by_tool.most_common(30)),
                          "buckets": dict(Counter(r["bucket"] for r in rows))}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
