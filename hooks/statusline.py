#!/usr/bin/env python3
"""statusline.py — the OrchestraOS Claude Code status line (opt-in, installed by `orchestra init`).

Claude Code runs the `statusLine` command on redraw and pipes it a JSON description of the session.
This command does two things with it:

1. Writes the session's context reading to <$TMPDIR or /tmp>/claude-ctx-<session_id>.json
   ({session_id, remaining_percentage, used_pct, timestamp}). The apps' context numbers
   (scripts/context_reading.py) and the rotation engine (lineage_daemon/wal/ctx_adapters.py) read
   it; without it a public install has no context reading at all. The path comes from
   ctx_adapters.ctx_bridge_path, the one resolver every reader uses.
2. Prints the status line. CHAINED (the user already had one and said yes): the user's own command
   gets the identical stdin bytes and its output is printed unchanged. Otherwise a minimal line:
   model | project | meter of the WINDOW, labeled "used".

hooks/statusline.sh runs this at most once per 30 s per session (it serves its cache otherwise),
so a redraw costs a shell, not a Python start.

Usage: statusline.py [CHAIN]   CHAIN = base64 of the user's original statusLine object (JSON).
Never fails the redraw: any error prints the fallback and exits 0.
"""
import base64
import json
import os
import subprocess
import sys
import time

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# used_pct is usage over the rotation BUDGET: 80% of the window, where Claude Code auto-compacts and
# where the rotation engine's ceiling sits (lineage_daemon/ctxstate.effective_ceiling). Same scale as
# the fleet's status line, so `context_pct_of_budget` means the same thing on every install.
BUDGET_FRACTION = 0.80
CHAIN_TIMEOUT_S = 10


def write_reading(d: dict, now: float | None = None) -> str | None:
    """Write the bridge file for this session; the path written, or None when there is nothing to
    write (no session id, or no numeric remaining_percentage yet)."""
    from lineage_daemon.wal.ctx_adapters import ctx_bridge_path
    sid = d.get("session_id")
    rem = (d.get("context_window") or {}).get("remaining_percentage")
    if not isinstance(sid, str) or not sid or "/" in sid or isinstance(rem, bool) \
            or not isinstance(rem, (int, float)):
        return None
    used = max(0.0, min(100.0, 100.0 - float(rem)))
    path = ctx_bridge_path(sid)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"session_id": sid, "remaining_percentage": rem,
                   "used_pct": min(100, round(used / (BUDGET_FRACTION * 100) * 100)),
                   "timestamp": int(time.time() if now is None else now)}, fh)
    os.replace(tmp, path)            # a reader never sees a half-written file
    return path


def render_minimal(d: dict) -> str:
    model = ((d.get("model") or {}).get("display_name") or "Claude").strip()
    cwd = (d.get("workspace") or {}).get("current_dir") or d.get("cwd") or ""
    parts = [model] + ([os.path.basename(cwd.rstrip("/"))] if cwd else [])
    rem = (d.get("context_window") or {}).get("remaining_percentage")
    if isinstance(rem, (int, float)) and not isinstance(rem, bool):
        used = max(0, min(100, round(100 - rem)))
        filled = used // 10
        parts[-1] += f" {'█' * filled}{'░' * (10 - filled)} {used}% used"
    return " │ ".join(parts)


def run_chain(chain_b64: str, raw: bytes) -> bytes:
    """The user's own status line command, fed the identical stdin; its stdout, unchanged."""
    obj = json.loads(base64.b64decode(chain_b64))
    cmd = obj.get("command") if isinstance(obj, dict) else None
    if not isinstance(cmd, str) or not cmd:
        return b""
    r = subprocess.run(["sh", "-c", cmd], input=raw, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, timeout=CHAIN_TIMEOUT_S)
    return r.stdout


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    raw = sys.stdin.buffer.read()
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        d = {}
    if not isinstance(d, dict):
        d = {}
    try:
        write_reading(d)
    except OSError:
        pass                         # the reading is best-effort; the line still prints
    out = None
    if argv:
        try:
            out = run_chain(argv[0], raw)
        except Exception:  # noqa: BLE001 — a broken chained command must not blank the line
            out = None
    if out is None:
        out = (render_minimal(d) + "\n").encode()
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        print("Claude")
        raise SystemExit(0)
