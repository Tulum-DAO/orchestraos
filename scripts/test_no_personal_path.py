"""Data-dir sweep S5 (gm ruling): no operator's personal path or machine marker ships.

88 non-test files once defaulted their data dir to one operator's private checkout; the default is
now orchestra_cli.settings.data_dir() (Python) and dataDir() in api/src/lib/config.ts (TS). This lint
fails on that path, or on any other personal marker, in any tracked file outside tests, naming
file:line. Fixed strings, not patterns: a marker either ships or it does not.
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Hex-encoded so this file does not ship the markers (or fragments of them) as literals either.
MARKERS = [bytes.fromhex(h).decode() for h in (
    "736372697074732f6167656e742d6f7263686573747261",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "2f686f6d652f73686177",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "73727631333937303136",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "7461696c386265353431",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "53686177436f6c65",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "3130302e3132342e3135312e3934",   # pragma: allowlist secret (hex-encoded marker, not a secret)
    "3130302e36382e3137312e3939",   # pragma: allowlist secret (hex-encoded marker, not a secret)
)]

# Exempt: tests and their fixtures (they may assert a marker is ABSENT, or fence this dev box), the
# recorded contract transcripts, and the operator-identifier scanner whose detection patterns ARE
# the markers.
EXEMPT_FILES = {"scripts/scan_operator_identifiers.py", "scripts/operator_identifiers_baseline.json"}
EXEMPT_PREFIXES = ("contract/transcript/fixtures/",)


def _exempt(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES)
            or name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.mjs"))
            or name == "conftest.py" or "/tests/" in f"/{rel}" or "/fixtures/" in f"/{rel}")


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def test_no_personal_marker_ships():
    files = _tracked()
    assert len(files) > 300, "git ls-files returned too little to be a real scan"
    hits = []
    for rel in files:
        if _exempt(rel):
            continue
        try:
            text = (ROOT / rel).read_text(errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in MARKERS:
                if m in line:
                    hits.append(f"{rel}:{i}: [{m}] {line.strip()[:120]}")
    assert not hits, "personal path / machine marker in shipped files:\n" + "\n".join(hits)


# ---- the operator's own Hume account: custom voice name + ids (gm: "so it can't come back") -------------
# Checked in EVERY tracked file, tests included: no fixture ever needs the real ones. Ids are fixed strings;
# the voice name is a whole word, so ordinary words that merely contain it do not trip the lint.
ACCOUNT_IDS = [bytes.fromhex(h).decode() for h in (
    "36313032653432332d633763632d343465662d623365612d396239393462376365386634",   # pragma: allowlist secret (hex-encoded marker: Hume config id)
    "34626332343536352d353234632d343639642d386235642d336366666433383430343361",   # pragma: allowlist secret (hex-encoded marker: Hume custom voice id)
)]
ACCOUNT_NAMES = [re.compile(r"\b" + re.escape(bytes.fromhex(h).decode()) + r"\b", re.I) for h in (
    "4672616e6b",   # pragma: allowlist secret (hex-encoded marker: Hume custom voice name)
)]
ACCOUNT_EXEMPT = {"scripts/test_no_personal_path.py"} | EXEMPT_FILES


def _account_hits(rel, text):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(x in line for x in ACCOUNT_IDS) or any(r.search(line) for r in ACCOUNT_NAMES):
            out.append(f"{rel}:{i}: {line.strip()[:120]}")
    return out


def test_no_hume_account_identifier_ships_anywhere():
    files = _tracked()
    assert len(files) > 300, "git ls-files returned too little to be a real scan"
    hits = []
    for rel in files:
        if rel in ACCOUNT_EXEMPT:
            continue
        try:
            hits += _account_hits(rel, (ROOT / rel).read_text(errors="ignore"))
        except (IsADirectoryError, FileNotFoundError):
            continue
    assert not hits, "the operator's Hume voice/config identifier in a tracked file:\n" + "\n".join(hits)


def test_the_account_lint_bites():
    name = bytes.fromhex("4672616e6b").decode()
    assert _account_hits("x.json", f'"voice": "{name}"') and _account_hits("x.py", ACCOUNT_IDS[0])
    assert _account_hits("x.py", name.lower() + " voice"), "case-insensitive"
    assert not _account_hits("x.md", name + "furt " + name + "ly"), "whole word only"


# ---- ANY user's home path (gm: "so the next unknown user is caught too") ------------------------------
# An absolute /home/<name>/ or /Users/<name>/ in a shipped file names somebody's machine. Allowed: placeholders
# (/home/<you>/, /home/$USER/, /home/.../), and "orchestra", the product's own install and dev-container user
# (docs/INSTALL.md, .devcontainer). Tests and fixtures are exempt, as for the markers above.
# No trailing slash needed (HOME=/home/<name>, a path at line end). A placeholder starts with a character
# outside the name class (<you>, $USER), so it never matches.
HOME_PATH = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")
ALLOWED_HOME_NAMES = {"orchestra", "..."}


def _home_hits(rel, text):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in HOME_PATH.finditer(line):
            if m.group(1) not in ALLOWED_HOME_NAMES:
                out.append(f"{rel}:{i}: {m.group(0)}  {line.strip()[:100]}")
    return out


def test_no_users_home_path_ships():
    files = _tracked()
    assert len(files) > 300, "git ls-files returned too little to be a real scan"
    hits = []
    for rel in files:
        if _exempt(rel) or rel == "scripts/test_no_personal_path.py":
            continue
        try:
            hits += _home_hits(rel, (ROOT / rel).read_text(errors="ignore"))
        except (IsADirectoryError, FileNotFoundError):
            continue
    assert not hits, ("a user's home path in shipped files (use <checkout>, <data dir> or /home/<you>/):\n"
                      + "\n".join(hits))


def test_the_home_path_lint_bites():
    assert _home_hits("x.md", "cd /home/alice/repo") and _home_hits("x.sh", "open /Users/bob/Downloads/")
    assert _home_hits("x.py", "'/home/" + "zed" + "/.orchestra/'"), "any unknown name, not a list"
    assert _home_hits("x.sh", "HOME=/home/alice") and _home_hits("x.md", "cd /Users/bob"), "no trailing slash"
    for ok in ("/home/<you>/repo", "/home/$USER/x", "/home/.../bin/claude", "/home/orchestra/.ssh", "/home/", "~/x"):
        assert not _home_hits("x.md", ok), ok


def test_the_lint_bites():
    # positive control: a marker in a non-exempt path is found (guards against a scan that sees nothing)
    assert not _exempt("scripts/example.py") and _exempt("scripts/test_example.py")
    assert any(m in "x = '~/" + MARKERS[0] + "'" for m in MARKERS)


# ---- the ONE data-dir default -------------------------------------------------------------------

sys.path.insert(0, str(ROOT))
from orchestra_cli import settings  # noqa: E402


def test_data_dir_order(monkeypatch, tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n' % (tmp_path / "configured"))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("ORCH_DIR", str(tmp_path / "old-alias"))
    assert settings.data_dir() == tmp_path / "env"
    monkeypatch.delenv("ORCHESTRA_DIR")
    assert settings.data_dir() == tmp_path / "old-alias"          # deprecated alias, still honoured
    monkeypatch.delenv("ORCH_DIR")
    assert settings.data_dir() == tmp_path / "configured"
    cfg.write_text("[data]\n")
    assert settings.data_dir() == pathlib.Path(os.path.expanduser(settings.DEFAULT_DATA_DIR))


def test_include_env_false_ignores_the_env(monkeypatch, tmp_path):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n' % (tmp_path / "configured"))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "env"))
    assert settings.data_dir(include_env=False) == tmp_path / "configured"
    assert os.environ["ORCHESTRA_DIR"] == str(tmp_path / "env")    # never mutates the env


@pytest.mark.parametrize("rel", ["message_bus.py", "unified_log.py", "scripts/park-idle.py",
                                 "scripts/session-index.py", "scripts/red_alert.py"])
def test_a_script_run_as_a_file_finds_the_one_default(rel, tmp_path):
    """Run the way an operator runs it by hand: fresh interpreter, no PYTHONPATH, no ORCHESTRA_DIR.
    The data dir must come from orchestra.toml, not a hard-coded path."""
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text('[data]\ndir = "%s"\n' % (tmp_path / "configured"))
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "ORCHESTRA_DIR", "ORCH_DIR", "ORCHESTRA_ROOT")}
    env["ORCHESTRA_CONFIG"] = str(cfg)
    code = ("import importlib.util as u, sys\n"
            f"s = u.spec_from_file_location('m', {str(ROOT / rel)!r}); m = u.module_from_spec(s)\n"
            "sys.argv = ['x']\n"
            "s.loader.exec_module(m)\n"
            "from orchestra_cli.settings import data_dir; print(data_dir())\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    assert r.stdout.strip().splitlines()[-1] == str(tmp_path / "configured")


# ---- every module that uses the one default must still IMPORT ------------------------------------
# The S5 edit inserted a sys.path block + import into ~55 files. Five of them (scripts/continuity/*) bound
# `sys` as `_sys`, so the block raised NameError at import, and no existing test imports them: the suite
# stayed green over modules that could not load. This imports every one, fresh, the way it is run.

_PACKAGES = ("orchestra_cli/", "services/", "scripts/lineage_daemon/", "scripts/identity_store/",
             "scripts/continuity/", "scripts/lineage_gate/", "scripts/focus_registry/")


def _uses_default():
    out = subprocess.run(["git", "grep", "-l", "-F", "_data_dir", "--", "*.py"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    return [p for p in out if not _exempt(p)]


@pytest.mark.parametrize("rel", _uses_default())
def test_module_using_the_default_imports(rel, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "ORCHESTRA_ROOT", "ORCH_DIR")}
    env["ORCHESTRA_DIR"] = str(tmp_path)              # an EMPTY data dir
    if rel.startswith(_PACKAGES) and "-" not in rel.rsplit("/", 1)[-1]:
        mod = rel[:-3].replace("/", ".")
        code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import {mod}"
    else:                                             # a standalone script, loaded by file
        code = ("import importlib.util as u, sys; sys.argv = ['x']\n"
                f"s = u.spec_from_file_location('m', {str(ROOT / rel)!r}); m = u.module_from_spec(s)\n"
                "sys.modules['m'] = m; s.loader.exec_module(m)")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{rel} does not import:\n{r.stderr[-1200:]}"
