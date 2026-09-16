"""Effect-existence checks (RED-TEAM Finding 0.5-B + H6).

Rebases 'began the correct work' onto the unfoolable rung-4 substrate: the EFFECT
itself. A first-action effect declares what will exist (a file, a commit, a live
session, a msg_store row, or a command that returns rc 0); the gate checks that
mechanically — exit codes / existence, never a transcript. Effects don't lag once
created and can't be ghosted (unlike pane/jsonl/callback inference).

`effect = {kind: file|commit|session|msg|command, target, check?}` — authored into
`next_3_actions[0].effect` by the predecessor. For gm/ob-class agents whose focus-id
+ file-roots are vacuous, this task-level anchor IS the correctness check.
"""
import os
import shlex
import subprocess
from typing import Callable, Optional

_TIMEOUT_S = 10


def _default_runner(argv, cwd=None) -> int:
    """Return the exit code of argv (no output captured). Bounded; never raises to
    the caller of check_effect (wrapped there)."""
    return subprocess.run(
        argv, cwd=cwd, timeout=_TIMEOUT_S,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode


def check_effect(effect: dict, cwd: str = ".", runner: Optional[Callable] = None) -> dict:
    """Mechanically verify the declared effect exists. Returns
    {exists, kind, target, detail}. Fail-safe: any error/unknown-kind → exists=False
    (a missing effect must never be reported as present)."""
    runner = runner or _default_runner
    kind = (effect or {}).get("kind")
    target = (effect or {}).get("target", "")
    try:
        if kind == "file":
            exists = os.path.exists(os.path.join(cwd, target)) if not os.path.isabs(target) else os.path.exists(target)
            return {"exists": bool(exists), "kind": kind, "target": target, "detail": "filesystem"}
        if kind == "session":
            rc = runner(["tmux", "has-session", "-t", target], cwd=cwd)
            return {"exists": rc == 0, "kind": kind, "target": target, "detail": "tmux has-session"}
        if kind == "commit":
            rc = runner(["git", "cat-file", "-e", target], cwd=cwd)
            return {"exists": rc == 0, "kind": kind, "target": target, "detail": "git cat-file"}
        if kind == "msg":
            rc = runner(["python3", "msg_store.py", "get", "--message-id", target], cwd=cwd)
            return {"exists": rc == 0, "kind": kind, "target": target, "detail": "msg_store row"}
        if kind == "command":
            check = (effect or {}).get("check") or target
            rc = runner(shlex.split(check), cwd=cwd)
            return {"exists": rc == 0, "kind": kind, "target": target, "detail": f"rc0: {check}"}
        return {"exists": False, "kind": kind, "target": target, "detail": "unknown-kind"}
    except Exception as e:  # fail-safe: never report a missing effect as present
        return {"exists": False, "kind": kind, "target": target, "detail": f"error: {e}"}
