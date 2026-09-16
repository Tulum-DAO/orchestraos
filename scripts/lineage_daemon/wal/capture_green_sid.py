#!/usr/bin/env python3
"""Green-sid capture (BG leg-(ii) P2.7, Design 2) — deterministic SessionStart hook.

The green's real claude ``session_id`` was never captured at spawn
(``spawn_green.py:120-127`` records the pane pid, not the sid), so after a
provisional->canonical swap the canonical row ended ``session_id=null`` (the M3
gap that made the identity plane unresolvable). This hook closes that at the
SOURCE: on the green's own SessionStart it reads the sid straight from the hook's
stdin JSON payload (the same field ``state-event-hook.py`` reads) and writes it to
``state/wal/<alias>.sid`` — DETERMINISTIC, no ``--resume``/log parsing.

Marker-gated on ``BG_GREEN_ALIAS`` (spawn-agent.sh exports it ONLY for a BG green;
a normal session never has it → INERT no-op — same discipline as
``green_boot_probe.main``). M3 reads ``state/wal/<alias>.sid`` to thread the green's
real sid into ``obs["green"]["session_id"]`` at promote.

HARD CONTRACT (Claude Code hook): ALWAYS exit 0, NEVER write stdout (a SessionStart
hook's stdout is injected as context; a non-zero exit could disrupt boot). Fail
silent to stderr. It must NEVER abort the green's spawn.
"""
import json
import os
import sys
import tempfile

# scripts/lineage_daemon/wal/capture_green_sid.py → repo root is four parents up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _orchestra_dir():
    return os.environ.get("ORCHESTRA_DIR") or _REPO


def write_green_sid(orchestra_dir, alias, session_id):
    """Atomically write ``state/wal/<alias>.sid`` = session_id. Returns the path.
    Atomic (tmp + os.replace) so a concurrent M3 reader never sees a half-written
    sid. Creates state/wal/ if absent."""
    wal_dir = os.path.join(orchestra_dir, "state", "wal")
    os.makedirs(wal_dir, exist_ok=True)
    path = os.path.join(wal_dir, f"{alias}.sid")
    fd, tmp = tempfile.mkstemp(dir=wal_dir, prefix=f".{alias}.sid.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(str(session_id).strip() + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def read_green_sid(wal_dir, alias):
    """Read the sid the green wrote to ``<wal_dir>/<alias>.sid`` (the write side is
    ``write_green_sid``; co-located so the path convention has ONE source of truth).
    Returns the stripped sid, or None if absent/unreadable (green not yet booted) —
    M3's build_obs never fabricates a sid, so a missing file is a clean None."""
    try:
        with open(os.path.join(wal_dir, f"{alias}.sid")) as fh:
            sid = fh.read().strip()
        return sid or None
    except (OSError, ValueError):
        return None


def register_green_session(orchestra_dir, green_alias, *, resolve_cid_fn,
                           update_session_fn=None, project_fn=None,
                           full_record=None, poll_attempts=8, sleep_fn=None):
    """(a) ACTIVE green-side #1 (Blocker-1 applied to the green). A runtime WITHOUT a
    claude SessionStart-stdin hook (gemini) never runs ``main()`` above, so the green's live
    cid is never captured and its registry row / .sid stay null — verify/(b) then watch the
    WRONG (blue) sid and false-stall. This closes it provider-agnostically: BOUNDED-poll the
    INJECTED ``resolve_cid_fn(green_alias)`` (resolves the green's live cid by effect once it
    boots), then write it DB-FIRST (``update_session_fn`` -> agent-sessions take-over),
    reproject the flat file (``project_fn``), and write ``state/wal/<alias>.sid`` — the SAME
    sid the swap/M3/verify read. Returns the cid, or None if the green never became
    resolvable within the budget (bounded, never hangs; spawn_green records the breadcrumb
    and the verify seam fail-closes + (d) prunes). ZERO runtime-name literals — the resolver
    and store writers are injected."""
    sleep_fn = sleep_fn or (lambda: None)
    cid = None
    for attempt in range(max(1, poll_attempts)):
        try:
            cid = resolve_cid_fn(green_alias)
        except Exception:  # noqa: BLE001 — a resolver hiccup is a not-yet-booted miss
            cid = None
        if cid:
            break
        if attempt < poll_attempts - 1:
            sleep_fn()
    if not cid:
        return None
    # DB-FIRST under lock: the same-txn session take-over (update_session) writes the green's
    # session document with the live cid; project_now refreshes the flat agent-sessions.json
    # the beat's build_obs reads. Both injected so this is unit-testable without a live DB.
    if update_session_fn is not None:
        record = full_record or {
            "session_id": cid, "name": green_alias, "tmux_session": green_alias,
            "lineage_root": green_alias.rsplit("-g", 1)[0], "status": "online",
        }
        update_session_fn(green_alias, {"session_id": cid}, full_record=record)
        if project_fn is not None:
            try:
                project_fn()
            except Exception:  # noqa: BLE001 — a projection hiccup is non-fatal here; the
                pass          # .sid below is still the load-bearing path for M3/verify
    # .sid: the load-bearing path build_obs/M3/verify read (overwrites any STALE prior-era
    # .sid — the reblind-on-rotation landmine).
    write_green_sid(orchestra_dir, green_alias, cid)
    return cid


def main():
    alias = os.environ.get("BG_GREEN_ALIAS")
    if not alias:
        return 0  # marker absent → INERT no-op (a normal session is unaffected)
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — FAIL-SAFE: garbage/empty stdin never aborts boot
        data = {}
    sid = data.get("session_id") if isinstance(data, dict) else None
    if not sid:
        # nothing to capture yet — the verify/promote seam fail-closes safely on
        # an absent .sid (no worse than today's null); never fabricate a sid.
        return 0
    try:
        path = write_green_sid(_orchestra_dir(), alias, sid)
        sys.stderr.write(f"[bg-green-sid] {alias}: captured session_id -> {path}\n")
    except Exception as e:  # noqa: BLE001 — FAIL-SAFE: never abort the green spawn.
        sys.stderr.write(
            f"[bg-green-sid] {alias}: FAILED to write .sid ({e!r}). The green spawn "
            f"CONTINUES; M3/verify fail-closes on the absent sid (no worse than null).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
