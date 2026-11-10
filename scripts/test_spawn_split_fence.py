"""REGISTRY/ORCHESTRA_DIR split-fence and under-test live-root refusal."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _source(script: str, tree: Path, extra_env: dict | None = None, registry: Path | None = None) -> subprocess.CompletedProcess:
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "state").mkdir(parents=True, exist_ok=True)
    reg = registry or (tree / "registry.json")
    if not reg.exists():
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"agents": {}}))
    env = dict(os.environ, ORCHESTRA_DIR=str(tree), REGISTRY=str(reg), NTFY_TOKEN_FILE="/nonexistent")
    if extra_env:
        env.update(extra_env)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    return subprocess.run(
        ["bash", "-c", 'source "$1"; ' + script, "spawn-test", str(ROOT / "spawn-agent.sh")],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=30,
    )


def test_split_registry_and_orchestra_dir_is_refused(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (b / "registry.json").write_text(json.dumps({"agents": {}}))
    r = _source("echo sourced", a, extra_env={"ORCHESTRA_DIR": str(a), "REGISTRY": str(b / "registry.json")})
    assert r.returncode == 2, r.stderr + r.stdout
    assert "different trees" in r.stderr


def test_matching_registry_and_orchestra_dir_sources(tmp_path):
    r = _source('echo ok', tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_protected_root_under_test_is_refused(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "state").mkdir()
    (live / "registry.json").write_text(json.dumps({"agents": {}}))
    r = _source(
        "_refuse_protected_roots_under_test; echo reached",
        live,
        extra_env={"SPAWN_PROTECTED_ROOT": str(live), "PYTEST_CURRENT_TEST": "test_protected"},
    )
    assert r.returncode == 2, r.stderr + r.stdout
    assert "protected root" in r.stderr
    assert "reached" not in r.stdout


def test_scratch_tree_under_test_is_allowed(tmp_path):
    scratch = tmp_path / "scratch"
    other = tmp_path / "live"
    other.mkdir()
    r = _source(
        "_refuse_protected_roots_under_test; echo reached",
        scratch,
        extra_env={"SPAWN_PROTECTED_ROOT": str(other), "PYTEST_CURRENT_TEST": "test_scratch"},
    )
    assert r.returncode == 0, r.stderr
    assert "reached" in r.stdout
