#!/usr/bin/env python3
"""Claude Code hook entry point for Jev (Levels 6 and 7).

  python3 scripts/jev_hook.py pre-tool-use        Level 6 A+B: bash gate, write gate
  python3 scripts/jev_hook.py post-tool-use       Level 6 C:   tool-result injection screen
  python3 scripts/jev_hook.py stop                Level 7:     evaluate the finished turn, persist verdict
  python3 scripts/jev_hook.py user-prompt-submit  Level 7:     inject a pending tier message once

Reads the hook event JSON on stdin, prints hook JSON on stdout only when it has something
to say. Exit 0 always on a decision; an unexpected bug exits 1, which Claude Code treats as
a NON-blocking error (exit 2 would block, and is never used here).

Rollout switchboard: $ORCHESTRA_DIR/state/jev/config.json (see load_config).

Off switches (checked first, before any import of the client):
  JEV_HOOKS_DISABLED=1, or the file $ORCHESTRA_DIR/state/jev/DISABLED exists.

The mock backend is refused here (exit 0, no decision) unless JEV_HOOK_ALLOW_MOCK=1.

Defaults set here (only if unset): JEV_LOG=$ORCHESTRA_DIR/logs/jev-decisions.jsonl,
hook client timeout JEV_HOOK_TIMEOUT_MS=5000.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.environ.get("ORCHESTRA_DIR") or os.path.dirname(HERE)
EVENTS = ("pre-tool-use", "post-tool-use", "stop", "user-prompt-submit")


def disabled() -> bool:
    return os.environ.get("JEV_HOOKS_DISABLED") == "1" or os.path.exists(os.path.join(ORCH, "state", "jev", "DISABLED"))


def load_config() -> dict:
    """$ORCHESTRA_DIR/state/jev/config.json — the rollout switchboard, re-read on every hook.

      {"gates": ["C"],                     Level 6 gates on for every seat
       "canary_gates": ["A", "B"],         extra gates only for canary seats
       "canary_seats": ["jev-builder"],    tmux session names
       "compact": true,                    Level 7 Stop/UserPromptSubmit on or off
       "write_roots": ["~/repos", ...]}    gate B roots besides the session cwd (~ expanded)

    Absent file = everything the env says (JEV_GUARD_GATES, default all) and compact on.
    A malformed file raises: a broken switchboard must be loud, not silently "all on".
    """
    path = os.path.join(ORCH, "state", "jev", "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return cfg


def seat_name() -> str:
    """The tmux session this hook's Claude runs in, or '' outside tmux."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return ""
    try:
        p = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#S"],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout.strip()


def resolve_gates(cfg: dict, seat_fn=seat_name) -> str:
    """Comma list for JEV_GUARD_GATES. Explicit env wins; then the config; then all."""
    if os.environ.get("JEV_GUARD_GATES") is not None:
        return os.environ["JEV_GUARD_GATES"]
    if "gates" not in cfg and "canary_gates" not in cfg:
        return "A,B,C"
    gates = {g.upper() for g in cfg.get("gates") or []}
    canary = {g.upper() for g in cfg.get("canary_gates") or []}
    if canary - gates:
        seat = seat_fn()
        if seat and seat in set(cfg.get("canary_seats") or []):
            gates |= canary
    return ",".join(sorted(gates))


def main(argv) -> int:
    if len(argv) != 2 or argv[1] not in EVENTS:
        print(f"usage: jev_hook.py {{{'|'.join(EVENTS)}}}", file=sys.stderr)
        return 1
    if disabled():
        return 0
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("jev_hook: stdin is not JSON; ignoring", file=sys.stderr)
        return 0

    sys.path.insert(0, HERE)
    os.environ.setdefault("JEV_LOG", os.path.join(ORCH, "logs", "jev-decisions.jsonl"))
    from jev import client as jev_client  # noqa: E402
    from jev import compact, guard  # noqa: E402

    # The mock is token overlap, not judgment: it blocks `ls -la`. Never gate a live seat on it.
    try:
        provider = jev_client.select_provider()
    except jev_client.JevConfigError:
        provider = None  # no key: decide() raises the same error and the hook policy handles it
    if provider == "mock" and os.environ.get("JEV_HOOK_ALLOW_MOCK") != "1":
        return 0

    timeout_ms = int(os.environ.get("JEV_HOOK_TIMEOUT_MS") or 5000)

    def decide(state, questions):
        if decide.client is None:
            decide.client = jev_client.JevClient(timeout_ms=timeout_ms, retry_delay_ms=200)
        return decide.client.system_one(state, questions)
    decide.client = None

    # A missing key raises JevConfigError inside decide(); the hooks treat it like any other
    # failed Jev call (logged, then fail-open or fail-closed per JEV_GUARD_FAIL).
    which = argv[1]
    cfg = load_config()
    if which in ("stop", "user-prompt-submit") and cfg.get("compact") is False:
        return 0
    if which in ("pre-tool-use", "post-tool-use"):
        os.environ["JEV_GUARD_GATES"] = resolve_gates(cfg)
        if os.environ.get("JEV_WRITE_ROOTS") is None and cfg.get("write_roots"):
            os.environ["JEV_WRITE_ROOTS"] = os.pathsep.join(os.path.expanduser(r) for r in cfg["write_roots"])
        if not os.environ["JEV_GUARD_GATES"]:
            return 0
    if which == "pre-tool-use":
        out = guard.pre_tool_use(event, decide)
    elif which == "post-tool-use":
        out = guard.post_tool_use(event, decide)
    elif which == "stop":
        out = compact.stop_hook(event, decide)
    else:
        out = compact.user_prompt_submit_hook(event)
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
