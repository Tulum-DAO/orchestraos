"""A guard refusal never sends a seat to a human.

A guard refuses one tool call in the seat's own pane and says why; the seat reroutes. If a refusal
says "ask the operator" or names a person, a seat may turn a refused command into a question for the
operator, which is the one outcome these guards must never cause. When a refusal names someone to
ask, it names gm.

Each guard is run as its hook runs (JSON on stdin) on a payload it refuses.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
P = "msg_store.py" + " send"

CASES = [
    ("send_shape_guard.py", f"python3 {P} --from a --to b --body-file f ; python3 {P} --from c --to d --body-file f", "/"),
    ("body_backtick_guard.py", f'python3 {P} --from a --to b --body "x `ls` y"', "/"),
    ("git_state_guard.py", "git " + "stash", "/"),
    ("git_state_guard.py", "git commit " + "-F-", "/"),
    ("worktree_path_guard.py", f"git worktree add {HOOKS.parent}/.workspace/x -b x", str(HOOKS.parent)),
    ("delete_guard.py", "rm " + "-rf /", "/"),
]

HUMAN = re.compile(r"\b(ask|tell|check with|confirm with) (the )?(operator|owner|user|human|admin)\b", re.I)


@pytest.mark.parametrize("script,command,cwd", CASES, ids=[c[0] + ":" + c[1][:12] for c in CASES])
def test_a_refusal_points_at_gm_never_at_a_human(script, command, cwd, tmp_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    env = {**os.environ, "SSG_LOG": str(tmp_path / "ssg.log"), "ORCHESTRA_ROOT": str(HOOKS.parent)}
    r = subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 2, f"{script} did not refuse {command!r}: rc {r.returncode} {r.stderr[:200]}"
    assert not HUMAN.search(r.stderr), f"{script} points the seat at a human: {r.stderr[:300]}"
    for m in re.finditer(r"\bask (\w+)", r.stderr, re.I):        # any "ask X" must be "ask gm"
        assert m.group(1) == "gm", f"{script} tells the seat to ask {m.group(1)!r}, not gm"
