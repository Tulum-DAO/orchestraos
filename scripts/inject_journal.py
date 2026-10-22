"""Typed-but-not-entered journal for the watch gateway's verified_inject (gm msg_40dcd58a, 2026-10-14).

Incident: 2026-10-14 06:46:59Z a `tmux respawn-pane -k` of the gateway (SIGHUP) killed a Quest
/agent-message between `send-keys -l <text>` and its Enter. the operator's text sat unsent in gm's composer;
the iPhone later forced "Hello" on top of it and both went as one prompt. MEASURED with a probe:
SIGHUP and respawn-pane -k kill an in-flight handler mid-way; SIGTERM lets aiohttp finish it.

So: (1) restarts use SIGTERM (scripts/restart_watch_gateway.sh); (2) for anything that still kills
the process mid-inject (crash, SIGKILL, OOM), verified_inject writes one row per session BEFORE it
types and deletes it after a verified submit; (3) at startup the gateway reconciles leftover rows:
our text still in the composer -> one Enter, verified; not verified -> left untouched and REPORTED.
Text we did not type is never touched (prefix match on what we typed).
"""
import json
import os
import re
import time
from pathlib import Path


def journal_dir():
    d = os.environ.get("WATCH_GATEWAY_INJECT_JOURNAL_DIR")
    if d:
        return Path(d)
    root = os.environ.get("ORCHESTRA_DIR") or str(Path(__file__).resolve().parents[1])
    return Path(root) / "state" / "gateway-inject-journal"


def _path(session):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session)) or "_"
    return journal_dir() / f"{safe}.json"


def record(session, text, marker):
    """Write the row BEFORE typing. Atomic replace; one row per session (the gateway's per-session
    send lock serializes injects to a pane)."""
    d = journal_dir()
    d.mkdir(parents=True, exist_ok=True)
    row = {"session": session, "marker": marker, "text": text or "", "text_head": (text or "")[:400],
           "typed_at": time.time(), "pid": os.getpid()}
    p = _path(session)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(row))
    os.replace(tmp, p)


def mark_typed(session):
    """Second phase, right after the keystrokes went out: only a row marked typed may ever get an
    Enter from the reconcile. A process killed between record() and its send leaves an UNMARKED
    row; a person could later type the identical text and park it, and the reconcile must never
    submit that. An unmarked row is reported, never pressed."""
    p = _path(session)
    try:
        row = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    row["typed"] = True
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(row))
    os.replace(tmp, p)
    return True


def clear(session):
    try:
        _path(session).unlink()
    except FileNotFoundError:
        pass


def rows():
    out = []
    d = journal_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError) as e:
            out.append({"session": None, "error": f"unreadable journal row {p.name}: {e}", "_path": str(p)})
    return out


def _norm(t):
    return " ".join((t or "").split())


def is_ours(composer, text):
    """EXACT ownership (codex r1): the composer holds exactly what we typed and nothing else.
    A prefix match would also match our text with a person's words appended, and Enter / C-u would
    then submit or erase their words. A long text that the composer shows wrapped or truncated does
    not match either, so it is left untouched and reported: never touching a person's words wins
    over recovering ours."""
    t = (text or "").strip()
    return bool(t) and (composer or "").strip() == t     # EXACT (codex r5): spacing edits are edits


def reconcile(read_composer, press_enter, report, now=None, verify_wait_s=2.5, lock=None):
    """Resolve every leftover row. Returns [(session, outcome)].

    read_composer(session) -> None (session gone), 'ERROR:<why>' (could not read), or the
                              composer's TYPED text ('' = empty). It must return None/'' when no
                              composer is on screen (a menu replaces it: never press Enter there).
    press_enter(session)   -> sends ONE Enter; returns False when the key could not be sent.
    report(session, outcome, row) -> called for every outcome that a person should hear about.
    lock(session) -> context manager yielding True when the pane lock is held (False = another typer
    holds it: the row is kept for the next start). The read, the Enter and the verify all happen
    inside it, so no other typer can slip text in between (codex r1).
    Outcomes: 'submitted' (Enter verified), 'not-ours' (our text is no longer there: it was
    submitted, or someone changed it; untouched), 'session-gone', 'left-untouched' (Enter not
    verified; NOT retried, NOT cleared), 'unreadable' (row kept for the next start).
    """
    results = []
    for row in rows():
        session = row.get("session")
        if not session:
            report(None, "unreadable", row)
            results.append((None, "unreadable"))
            continue
        text = row.get("text") or row.get("text_head") or ""
        from contextlib import nullcontext
        with (lock(session) if lock else nullcontext(True)) as held:
            if not held:
                report(session, "unreadable", row)      # pane busy: keep the row, next start
                results.append((session, "unreadable"))
                continue
            results.append((session, _reconcile_one(row, session, text, read_composer, press_enter,
                                                    report, verify_wait_s)))
    return results


def _reconcile_one(row, session, text, read_composer, press_enter, report, verify_wait_s):
    seen = read_composer(session)
    if seen is None:
        clear(session)
        report(session, "session-gone", row)
        return "session-gone"
    if isinstance(seen, str) and seen.startswith("ERROR:"):
        report(session, "unreadable", row)              # keep the row: try again next start
        return "unreadable"
    if not is_ours(seen, text):
        clear(session)                                  # our text is gone: evidence no longer needed
        if (seen or "").strip():
            report(session, "not-ours", row)            # something else is there: never touched
        return "not-ours"
    if row.get("typed") is not True:
        # Recorded but never durably marked typed: the process may have died before its keystrokes,
        # so this text may be a person's own identical draft. Reported, never pressed.
        report(session, "left-untouched", row)
        return "left-untouched"
    if row.get("reconcile_attempted_at"):
        # One Enter per row, ever (never a retry loop across restarts). The row STAYS as evidence
        # until our text leaves the composer (codex r2).
        report(session, "left-untouched", row)
        return "left-untouched"
    # Mark the row attempted DURABLY before the Enter (codex r4): a crash between Enter and the mark
    # would otherwise let the next start press a second Enter. No durable mark, no Enter.
    try:
        _mark_attempted(session, row)
    except OSError:
        report(session, "unreadable", row)
        return "unreadable"
    if press_enter(session) is False:
        # tmux could not even send the key (codex r5): nothing was pressed, so un-mark the row and
        # let the next start try again instead of leaving it marked-attempted forever.
        try:
            p = _path(session)
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({k: v for k, v in row.items() if k != "reconcile_attempted_at"}))
            os.replace(tmp, p)
        except OSError:
            pass
        report(session, "unreadable", row)
        return "unreadable"
    time.sleep(verify_wait_s)
    after = read_composer(session)
    if isinstance(after, str) and not after.startswith("ERROR:") and not is_ours(after, text):
        clear(session)
        report(session, "submitted", row)
        return "submitted"
    report(session, "left-untouched", row)
    return "left-untouched"


def _mark_attempted(session, row):
    row = dict(row, reconcile_attempted_at=time.time())
    p = _path(session)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(row))
    os.replace(tmp, p)


def _cli(argv):
    """tmux-inject.sh entry: `inject_journal.py record <session> <text>` / `clear <session>`.
    Exit 0 = done, 1 = the record could not be written (the caller must not type)."""
    if len(argv) >= 3 and argv[0] == "record":
        try:
            record(argv[1], argv[2], argv[2].strip().splitlines()[0][:40] if argv[2].strip() else "")
            return 0
        except OSError as e:
            print(f"inject_journal: record failed: {e}", file=__import__("sys").stderr)
            return 1
    if len(argv) >= 2 and argv[0] == "clear":
        clear(argv[1])
        return 0
    print("usage: inject_journal.py record <session> <text> | clear <session>", file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli(_sys.argv[1:]))
