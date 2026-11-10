"""install() must never leave the same OrchestraOS hook script twice on one event."""
from __future__ import annotations

import json
from pathlib import Path

import install as I


def test_install_twice_does_not_double_register(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    repo = Path(__file__).resolve().parents[2]  # checkout root
    data = tmp_path / "data"
    data.mkdir()
    r1 = I.install(settings_path=settings, repo_root=repo, data_dir=data)
    assert "error" not in r1, r1
    n1 = r1["installed"]
    r2 = I.install(settings_path=settings, repo_root=repo, data_dir=data)
    assert "error" not in r2, r2
    # Second install replaces (strip + write), so installed count matches once, not 2x.
    assert r2["installed"] == n1
    assert r2["removed"] == n1
    hooks = json.loads(settings.read_text())["hooks"]
    for ev, rules in hooks.items():
        cmds = []
        for rule in rules:
            for h in rule.get("hooks", []):
                cmd = h.get("command") or ""
                if I.MARKER in cmd:
                    cmds.append(cmd)
        assert len(cmds) == len(set(cmds)), f"event {ev} has duplicate hook commands"
        # each guard rel at most once on this event
        for rel in ("hooks/git_state_guard.py", "hooks/delete-guard.sh", "hooks/write-handoff.sh",
                    "hooks/send_shape_guard.py", "hooks/body_backtick_guard.py",
                    "hooks/worktree_path_guard.py"):
            hits = [c for c in cmds if rel in c]
            assert len(hits) <= 1, f"{ev} has {len(hits)} copies of {rel}"
