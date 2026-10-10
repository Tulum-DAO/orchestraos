"""No script falls back to ~/orchestra (gm msg_e62e4d1b, 2026-10-10).

Thirteen shipped files defaulted the data dir to ~/orchestra when neither ORCHESTRA_DIR nor ORCH_DIR was set:
not the product default (~/.orchestra), not the [data] dir in orchestra.toml, not the clone (orchestraos/).
Production launches set ORCHESTRA_DIR through orchestra-env.sh, so it showed only on a DIRECT run, which then
read and wrote a directory nothing else uses. They now fall back to orchestra_cli.settings.data_dir().
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (file, module-level name holding the data dir or a path under it, suffix under the data dir)
DIRECT_RUN = [
    ("scripts/message-router.py", "ORCHESTRA_DIR", ""),
    ("msg_store.py", "ORCHESTRA_DIR", ""),
    ("scripts/rotate_agent.py", "ORCHESTRA_DIR", ""),
    ("scripts/sid_invariants.py", "ORCH", ""),
    ("scripts/profile_switcher.py", "DEFAULT_ORCHESTRA_DIR", ""),
    ("scripts/lineage_daemon/auto_grade.py", "ORCHESTRA_DIR", ""),
    ("scripts/lineage_daemon/bus_feeder.py", "STREAM_DIR", "state/event-stream"),
    ("hooks/agent-queue-drain.py", "ORCH", ""),
    ("hooks/state-event-hook.py", "_DATA", ""),
]

_LOAD = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(getattr(m, sys.argv[2]))
"""


def _direct_env(tmp_path, home):
    """A direct run: no ORCHESTRA_DIR / ORCH_DIR / SID_ORCH, HOME a scratch dir, and an orchestra.toml whose
    [data] dir is a scratch dir. The process env is the test's own, minus those."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ORCHESTRA_DIR", "ORCH_DIR", "SID_ORCH", "ORCH_EVENT_STREAM_DIR", "PYTEST_CURRENT_TEST")}
    env["HOME"] = str(home)
    return env


@pytest.mark.parametrize("rel,name,suffix", DIRECT_RUN, ids=[r for r, _, _ in DIRECT_RUN])
def test_a_direct_run_resolves_the_configured_data_dir(rel, name, suffix, tmp_path):
    home = tmp_path / "home"; home.mkdir()
    data = tmp_path / "configured-data"; data.mkdir()
    toml = tmp_path / "orchestra.toml"
    toml.write_text(f'[data]\ndir = "{data}"\n')
    env = _direct_env(tmp_path, home)
    env["ORCHESTRA_CONFIG"] = str(toml)
    out = subprocess.run([sys.executable, "-c", _LOAD, str(ROOT / rel), name], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    got = out.stdout.strip().splitlines()[-1]
    assert got == str(data / suffix if suffix else data), got
    assert not (home / "orchestra").exists(), "a direct run created ~/orchestra"


@pytest.mark.parametrize("rel,name,suffix", DIRECT_RUN[:2], ids=[r for r, _, _ in DIRECT_RUN[:2]])
def test_with_no_config_it_is_the_product_default_not_tilde_orchestra(rel, name, suffix, tmp_path):
    home = tmp_path / "home"; home.mkdir()
    env = _direct_env(tmp_path, home)
    env["ORCHESTRA_CONFIG"] = str(tmp_path / "absent.toml")
    out = subprocess.run([sys.executable, "-c", _LOAD, str(ROOT / rel), name], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == str(home / ".orchestra")


# ---- lint: the stale default cannot come back -------------------------------------------------------

STALE = re.compile(r"~/orchestra(?![\w.-])|\(\s*HOME\s*,\s*['\"]orchestra['\"]\s*\)")


def _exempt(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.mjs"))
            or name == "conftest.py" or "/tests/" in f"/{rel}" or "/fixtures/" in f"/{rel}")


def test_no_shipped_file_names_tilde_orchestra():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                           check=True).stdout.decode().split("\0")
    files = [f for f in files if f]
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
            if STALE.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not hits, ("the data dir default is orchestra_cli.settings.data_dir() (~/.orchestra), never "
                      "~/orchestra:\n" + "\n".join(hits))
