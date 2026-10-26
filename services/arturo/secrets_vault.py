"""secrets_vault.py — keys Arturo can USE but never sees or says.

A user report: "Arturo should be able to use keys but never say them out loud." A deploy, an app
update or a campaign launch needs a key; Arturo's brain only needs to know the work happened. So:

  env()    every command Arturo runs gets this install's keys (<data dir>/.env.secrets) in its environment,
           by name: `curl -H "Authorization: Bearer $NETLIFY_TOKEN" ...` works, and the value never has to be
           in a prompt.
  scrub()  anything that comes back (a command's output, a file, an agent's pane) has every key VALUE blanked
           to its name before a model, the job drawer, the thread or the voice sees it. `cat .env.secrets`
           reads back as names. A key-shaped string that is not on file (sk-..., ghp_...) is blanked too.

The vault holds values only in this process's memory, re-read when the file changes.
"""
import os
import re
from pathlib import Path

MIN_LEN = 8                   # shorter values ("1", "true", a port) are not secrets worth matching on
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE)", re.I)
# Key shapes worth blanking even when not on file (a key printed by a CLI, pasted into a file).
_SHAPES = re.compile(
    r"\b(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")


def _parse(text):
    out = {}
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            out[name] = value
    return out


class Vault:
    def __init__(self, path, environ=None):
        self.path = Path(path)
        self._environ = os.environ if environ is None else environ
        self._mtime = None
        self._file = {}

    def _load(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._file = None, {}
            return self._file
        if mtime != self._mtime:
            try:
                self._file = _parse(self.path.read_text(errors="replace"))
            except OSError:
                self._file = {}
            self._mtime = mtime
        return self._file

    def names(self):
        """The keys a command can use, by name (what Arturo is told; never a value)."""
        return sorted(self._load())

    def values(self):
        """name -> value of everything to blank: the file's keys, and secret-named variables of this process."""
        vals = {k: v for k, v in self._environ.items() if _SECRET_NAME_RE.search(k) and len(v or "") >= MIN_LEN}
        vals.update({k: v for k, v in self._load().items() if len(v) >= MIN_LEN})
        return vals

    def env(self, base=None):
        """The environment a command runs with: this process's, plus the file's keys."""
        env = dict(self._environ if base is None else base)
        env.update(self._load())
        return env

    def scrub(self, text):
        """Every key value blanked to [NAME]; key-shaped strings to [key]. Never raises."""
        if not isinstance(text, str) or not text:
            return text
        try:
            for name, value in sorted(self.values().items(), key=lambda kv: -len(kv[1])):
                if value in text:
                    text = text.replace(value, f"[{name}]")
            return _SHAPES.sub("[key]", text)
        except Exception:  # noqa: BLE001 — blanking must never break what it guards
            return _SHAPES.sub("[key]", text)
