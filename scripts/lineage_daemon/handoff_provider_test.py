import json
import os
import pytest
import subprocess
from scripts.lineage_daemon.handoff_provider import (
    read_committed_handoff,
    build_live_handoff_provider,
    _extract_machine_block,
)


def test_extract_machine_block():
    text = """# Handoff
Some text
```json
{"current_goal": "finish rotation", "open_loops": []}
```
"""
    data = _extract_machine_block(text)
    assert data == {"current_goal": "finish rotation", "open_loops": []}


def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), capture_output=True, check=True)


def _commit_file(repo_path, file_path):
    subprocess.run(["git", "add", str(file_path)], cwd=str(repo_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Commit handoff"], cwd=str(repo_path), capture_output=True, check=True)


def test_read_committed_handoff_from_cwd(tmp_path):
    cwd = tmp_path / "worktree"
    cwd.mkdir()
    _init_git_repo(cwd)

    docs = cwd / "docs"
    docs.mkdir()
    hfile = docs / "HANDOFF_codex-dev-1-next.md"
    content = {
        "current_goal": "test goal",
        "phase_state": {"plan_ref": "p1", "phase_n": 1, "phase_m": 1},
        "next_3_actions": [{"action": "first", "first_effect": {"kind": "test", "target": "t"}}],
        "open_loops": [],
        "decisions": [],
    }
    hfile.write_text(f"```json\n{json.dumps(content)}\n```\n")
    _commit_file(cwd, hfile)

    data, mtime = read_committed_handoff("codex-dev-1", cwd=str(cwd))
    assert data is not None
    assert data["current_goal"] == "test goal"
    assert data["handoff_commit_sha"] is not None
    assert data["file_hash"] is not None
    assert data["authoring_timestamp"] is not None
    assert mtime is not None


def test_state_agent_handoffs_excluded_as_freshness_source(tmp_path):
    # DEC-1788165818 axis-4: state/agent-handoffs/<id>.* is daemon-clobbered
    # (re-snapshotted every beat) and is HARD-EXCLUDED as a freshness source. A
    # handoff present ONLY there must NOT be read -> None (using it would
    # reintroduce the mtime/clobber churn hazard).
    od = tmp_path / "orchestra"
    od.mkdir()
    _init_git_repo(od)

    state_dir = od / "state" / "agent-handoffs"
    state_dir.mkdir(parents=True)
    jfile = state_dir / "my-agent.json"
    content = {
        "current_goal": "from state json",
        "next_3_actions": ["act1", "act2", "act3"],
    }
    jfile.write_text(json.dumps(content))
    _commit_file(od, jfile)

    data, mtime = read_committed_handoff("my-agent", orchestra_dir=str(od))
    assert data is None, ("state/agent-handoffs/<id>.* must be excluded as a "
                          "freshness source (axis-4)")
    assert mtime is None


def test_read_committed_handoff_missing_or_uncommitted(tmp_path):
    od = tmp_path / "orchestra"
    od.mkdir()
    _init_git_repo(od)

    # Missing file
    data, mtime = read_committed_handoff("nonexistent", orchestra_dir=str(od))
    assert data is None
    assert mtime is None

    # Exists but untracked/uncommitted
    state_dir = od / "state" / "agent-handoffs"
    state_dir.mkdir(parents=True)
    jfile = state_dir / "uncommitted-agent.json"
    content = {
        "current_goal": "uncommitted",
        "next_3_actions": ["act1"],
    }
    jfile.write_text(json.dumps(content))

    data, mtime = read_committed_handoff("uncommitted-agent", orchestra_dir=str(od))
    assert data is None
    assert mtime is None


def test_read_committed_handoff_fixed_schema_validation(tmp_path):
    od = tmp_path / "orchestra"
    od.mkdir()
    _init_git_repo(od)

    state_dir = od / "state" / "agent-handoffs"
    state_dir.mkdir(parents=True)
    jfile = state_dir / "invalid-agent.json"
    
    # Missing current_goal or next_3_actions
    content = {
        "some_random_key": "some_value",
    }
    jfile.write_text(json.dumps(content))
    _commit_file(od, jfile)

    data, mtime = read_committed_handoff("invalid-agent", orchestra_dir=str(od))
    assert data is None
    assert mtime is None


def test_build_live_handoff_provider(tmp_path):
    od = tmp_path / "orchestra"
    od.mkdir()
    _init_git_repo(od)

    # canonical agent-authored artifact at docs/HANDOFF_<id>-next.md (axis-4);
    # markdown ##-section form (axis-1 accept-either).
    docs_dir = od / "docs"
    docs_dir.mkdir(parents=True)
    hfile = docs_dir / "HANDOFF_agent-1-next.md"
    hfile.write_text("## current_goal\nlive\n\n"
                     "## next_3_actions\n1. act1\n2. act2\n3. act3\n")
    _commit_file(od, hfile)

    reg = {"agents": {"agent-1": {"cwd": str(tmp_path)}}}
    prov = build_live_handoff_provider(orchestra_dir=str(od), registry=reg)
    data, mtime = prov("agent-1")
    assert data is not None
    assert data["current_goal"] == "live"
    assert data["handoff_commit_sha"] is not None
    assert mtime is not None
