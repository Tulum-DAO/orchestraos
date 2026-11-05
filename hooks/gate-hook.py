#!/usr/bin/env python3
"""gate-hook.py — the Claude Code PreToolUse safety gate (attention part 2a, plan §5.2 / §6): the entry script.

Installed by hooks/install.py as a PreToolUse row (timeout 600, the standard fail-open `[ -f ] && … || exit 0`
form, no onFailure). It reads stdin, checks the kill switch <data>/state/attention/GATE_OFF with nothing but os
and sys (present -> allow: no output, nothing read or written), then runs scripts/gate_hook.py, which holds the
logic and is imported (not run as __main__) so its bytecode is cached. Any failure to load it allows the call.
For allow the hook prints nothing; a deny is one JSON object on stdout; the exit status is always 0.
"""
import os
import sys


def _data_dir() -> str:
    d = os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR")
    if d:
        return d
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.append(root)
    from orchestra_cli.settings import data_dir
    return str(data_dir())


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        raw = ""
    try:
        data_dir = _data_dir()
    except Exception:  # noqa: BLE001
        return 0
    if os.path.exists(os.path.join(data_dir, "state", "attention", "GATE_OFF")):
        return 0                                           # the kill switch: nothing read, nothing written
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import gate_hook
    return gate_hook.run(raw, data_dir)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 — never a non-0 exit, never a traceback on stdout (fails open)
        pass
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(0)
