#!/usr/bin/env python3
"""bg_quarantine — BG Layer-4 prewarm quarantine (gm-authorized, congruence DEC-1788660122).

WHY: a PREWARMED green boots ALIVE and, until swap-wake, must NOT mutate shared state or reach the
human — the re-run showed a prewarmed green running as a parallel live writer alongside blue (it
mutated + fired a the operator AskUserQuestion). This holds it READ-ONLY until it is promoted, then it
SOLE-continues via the Layer-3 capsule.

MECHANISM M2 — env-scoped PreToolUse deny-hook (blast-radius contained by construction):
  * The guard is a LINE-1 NO-OP for any session WITHOUT this green's ``BG_QUARANTINE_ALIAS`` env, so
    it can NEVER block blue or any of the ~40 live agents. That containment is the load-bearing
    safety property.
  * For a green WITH that env AND an ACTIVE marker (``state/wal/quarantine/<alias>``): enforce a
    strict READ-ONLY ALLOW-LIST (AGY hardening — an allow-list, NOT a mutating blacklist, because a
    Bash heuristic is escape-prone: a read-only-looking command can write, ``curl -X POST``, or spawn
    an unquarantined subagent). ALLOW only {Read, Grep, Glob}; DENY everything else (Write/Edit/
    NotebookEdit, ALL Bash, Agent/Task subagent dispatch, AskUserQuestion, msg_store, any MCP tool).
  * Swap-wake removes the marker -> quarantine lifts WITHOUT a respawn (the promoted green acts).

FAIL-SAFE (asymmetric): env UNSET -> exit before any I/O -> ALLOW (a corrupt hook must never disrupt
the fleet). env SET + any error/ambiguity -> DENY (never let an unpromoted shadow mutate).

INERT until wired: this module + the marker seams ship inert. Wiring ``main`` into the global
~/.claude/settings.json PreToolUse chain touches ALL sessions -> a deliberate gm+the operator-armed step,
like the BG arm itself.
"""
import os
import sys

ENV_KEY = "BG_QUARANTINE_ALIAS"          # set ONLY in a prewarmed green's spawn env
WALDIR_ENV = "BG_QUARANTINE_WALDIR"      # absolute state/wal dir (green cwd != orchestra dir)
ALLOW_TOOLS = frozenset({"Read", "Grep", "Glob"})   # read-only allow-list (not a mutating blacklist)


def _safe_alias(alias):
    """Return the alias iff it is a single safe path segment (no traversal / separators), else None —
    a mangled BG_QUARANTINE_ALIAS must never escape the quarantine dir."""
    if not alias or not isinstance(alias, str):
        return None
    if alias != os.path.basename(alias) or alias in (".", "..") or "/" in alias or "\\" in alias:
        return None
    return alias


def _quarantine_dir(wal_dir):
    return os.path.join(wal_dir, "quarantine")


def marker_path(wal_dir, alias):
    safe = _safe_alias(alias)
    if safe is None:
        raise ValueError(f"unsafe quarantine alias: {alias!r}")
    return os.path.join(_quarantine_dir(wal_dir), safe)


def arm_quarantine(wal_dir, alias):
    """Write the ACTIVE quarantine marker for a prewarmed green (called at PREWARM spawn)."""
    path = marker_path(wal_dir, alias)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(f"quarantined green {alias}; read-only until swap-wake\n")
    return path


def lift_quarantine(wal_dir, alias):
    """Remove the marker (the swap-wake effect). Returns True if a marker was removed, False if none
    (idempotent)."""
    try:
        os.remove(marker_path(wal_dir, alias))
        return True
    except (OSError, ValueError):
        return False


def is_quarantined(wal_dir, alias):
    try:
        return os.path.exists(marker_path(wal_dir, alias))
    except ValueError:
        return False


def decide(tool_name, *, env, wal_dir):
    """The guard decision. Returns (allow: bool, reason: str). env UNSET -> allow before any I/O."""
    alias = (env or {}).get(ENV_KEY)
    if not alias:
        return True, "not-a-quarantined-green"      # line-1 fast path: zero I/O, zero blast radius
    try:
        safe = _safe_alias(alias)
        if safe is None:
            return False, "unsafe-alias-deny"        # env set + bad alias -> fail-closed
        # A known green MUST be able to positively confirm quarantine state. If the quarantine dir is
        # missing/unreadable (e.g. a wrong WALDIR), we CANNOT distinguish "lifted" from "misconfig" —
        # fail-CLOSED (else a mis-pathed green would false-lift and escape to mutate).
        if not os.path.isdir(_quarantine_dir(wal_dir)):
            return False, "no-quarantine-dir-fail-closed"
        if not is_quarantined(wal_dir, safe):
            return True, "quarantine-lifted"         # dir present + marker gone = promoted at swap-wake
        if tool_name in ALLOW_TOOLS:
            return True, "read-only-allowed"
        return False, f"quarantined-deny:{tool_name}"
    except Exception:  # noqa: BLE001 — env SET => fail-CLOSED (never let a shadow mutate)
        return False, "error-fail-closed"


def main():
    """PreToolUse hook entrypoint: stdin JSON {tool_name, tool_input}; exit 0 = allow, exit 2 = block
    (delete-guard protocol). wal_dir from BG_QUARANTINE_WALDIR (absolute)."""
    import json
    if not os.environ.get(ENV_KEY):
        return 0                                     # fleet fast-path: not a quarantined green
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    tool = data.get("tool_name", "") if isinstance(data, dict) else ""
    wal_dir = os.environ.get(WALDIR_ENV, "")         # empty -> decide() fail-closes (env is set)
    allow, reason = decide(tool, env=os.environ, wal_dir=wal_dir)
    if allow:
        return 0
    sys.stderr.write(
        f"BG-QUARANTINE: blocked {tool or '<?>'} — {reason}. This is a PREWARMED green: it is "
        f"read-only (Read/Grep/Glob) until swap-wake promotes it. Do NOT mutate state, commit, "
        f"message, or ask the human until you are the canonical agent.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
