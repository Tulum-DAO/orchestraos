#!/usr/bin/env python3
"""PreToolUse(Bash) guard: refuse a `msg_store.py send` whose SHAPE is the stray-fragment shape.

WHY THIS IS A HOOK AND NOT A NOTE (gm ruling msg_999a3ecd, 2026-09-26). Six instances across
three seats in five days, every one a compound line, five of six with the send's own output
thrown away so nobody saw the refusal. The written rule failed its own author twice in ONE
session: devex-review g8 self-reported instance five, appended a section about it to its own
memory file, argued the guard's hole to gm with measurements, and committed instance six inside
the hour. A control the actor cannot reliably self-apply belongs in the tool, then the hook,
then the document — never in the document alone.

WHAT IT REFUSES, and both predicates are VISIBLE rather than matters of judgement:
  R1  MORE THAN ONE `msg_store.py send` on the line. That is the stray-fragment signature: a
      leftover or half-edited send sitting in front of a real one. It is what produced all six.
  R2  A send whose OWN stdout or stderr is redirected away (`>/dev/null`, `2>/dev/null`, `&>`,
      `>file`). The refusal is the only signal a send did not happen; suppressing it is how
      five of six went unnoticed by their own author.

DELIBERATE DEVIATION FROM THE ORDER AS WRITTEN, DISCLOSED (devex-review, same message). gm
specified "refuse when the send is not the SOLE command on the line". Implemented literally that
refuses `cd $ORCHESTRA_DIR && python3 msg_store.py send ...`, which is the DOMINANT
legitimate form on this fleet and the form of essentially every correct send in the ledger. That
is friction, not a control, and gm's own contract says so ("prove the false-positive cases pass,
or you have shipped friction"). R1+R2 catch 6 of 6 historical shapes WITHOUT refusing a leading
`cd`, because the hazard is a SECOND SEND or a HIDDEN one — not the mere presence of a preamble.
If a future instance arrives that is a single un-redirected send alongside an unrelated command,
this narrowing is what let it through, and the fix is to widen R1 to trailing commands.

WHAT IT DOES NOT DO — SCOPE, stated so the gap is enumerated rather than discovered:
  * SEND ONLY. `ack`, `get`, `inbox`, `reply` and every read stay legal in compound lines, in
    pipes and in for-loops. gm runs acks in loops; those must pass and are tested.
  * It is NOT an identity check. The g62 forgery (msg_bc8e8998, 3,082 bytes stored as
    fleet-resume-dev asserting what it never said) was an INVERTED --from/--to on what may well
    have been a single clean line. This guard cannot see that and must not be credited with it:
    that is orchestra-builder's pair-keyed impersonation arm at the tool layer. Two layers, two
    properties, no overlap — this one is SHAPE, that one is IDENTITY.
  * PreToolUse(Bash) binds THIS AGENT'S Bash tool calls and nothing else. Not cron, not
    @reboot, not a daemon, not a seat that reaches msg_store without a shell. A fence that binds
    only the interactive path has a door in it; the door is named here.

FAIL-OPEN on anything unexpected (exit 0). A guard that crashes must never stop the fleet from
sending mail.
"""
import hashlib
import json
import os
import re
import sys
import time

__all__ = ["refusal_for", "main", "record", "fail_open_fields", "SEND_RE", "LOG_PATH"]

LOG_PATH = os.environ.get("SSG_LOG") or os.path.expanduser(
    "$ORCHESTRA_DIR/logs/send-shape-guard.log")


def record(verdict: str, cmd: str, detail: str = "") -> None:
    """Append ONE line per decision on a command that contains a send. Never raises.

    WHY THIS EXISTS (orchestra-improvements, msg_875f099f): the guard refuses at PreToolUse, so a
    blocked accident produces NO row in tasks.db and NO line in addressability-shadow.log — the
    only trace was stderr in the offending seat's own transcript. That makes the count of this
    accident class depend on VOLUNTARY DISCLOSURE, and the rate will appear to fall to zero
    exactly as the guards improve. Whoever next argues "this no longer happens, relax the arm"
    would be measuring the guards rather than the behaviour.
    AND THE SHARPER HALF: this guard is FAIL-OPEN by design, so without a record nobody can tell
    "it refused five things today" from "it has been fail-open since noon". ALLOW is logged too,
    which is what gives a refusal rate a DENOMINATOR.

    THE COMMAND TEXT IS NEVER WRITTEN — only a sha256 prefix. A send's arguments carry message
    bodies, and this fleet spent 2026-09-25 purging a live credential out of stores that had
    logged exactly that kind of text. A guard log that becomes a secret carrier would be a
    self-inflicted version of the incident it was built beside.
    """
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        digest = hashlib.sha256((cmd or "").encode("utf-8", "replace")).hexdigest()[:16]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {verdict} cmd_sha={digest} len={len(cmd or '')} {detail}\n")
    except Exception:       # noqa: BLE001 — a record must never change a verdict
        pass

# A `msg_store.py send` invocation. The subcommand must be the next word after the script so
# `... msg_store.py inbox` and a bare mention in prose do not match.
SEND_RE = re.compile(r"msg_store\.py\s+send\b")

# Output redirection applying to THIS invocation: >/dev/null, 2>/dev/null, &>/dev/null, >file,
# 1>&2 etc. Deliberately broad on the redirect side — a send whose output goes anywhere the
# author will not read is the defect, whatever the destination.
REDIR_RE = re.compile(r"(?:\d*>>?&?\s*\S+|&>\s*\S+)")

# Separators that end one command and begin another.
SEP_RE = re.compile(r"(?:\|\||&&|[;\n|])")


def _unquoted_spans(cmd: str):
    """Yield (start, end) of every region of `cmd` that is OUTSIDE single/double quotes.

    R1 counts INVOCATIONS, not occurrences of a substring. Without this, a send whose own body
    QUOTES the phrase counts itself into a refusal — found by gm grading this guard
    (msg_3b8daf1b), reproduced immediately: my first attempt to test the fix was blocked by the
    guard under test, which counted 8 "invocations" inside quoted example strings. A guard that
    cannot be discussed without a workaround is friction, and friction was the thing this guard
    was explicitly contracted not to ship.
    """
    i, n, start, quote = 0, len(cmd), 0, None
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
                start = i + 1
            i += 1
            continue
        if ch in "'\"":
            if i > start:
                yield (start, i)
            quote = ch
        i += 1
    if not quote and start < n:
        yield (start, n)
    elif quote:
        # Unbalanced quote: treat the remainder as unquoted rather than swallowing the line.
        yield (start, n)


def _invocation_span(cmd: str, start: int) -> str:
    """The text of the send invocation beginning at `start`, up to the next separator.

    Quote-aware so a `;` or `>` INSIDE a quoted argument does not end the invocation — a body
    containing a semicolon is not a compound line, and treating it as one would refuse ordinary
    prose.
    """
    i, n = start, len(cmd)
    quote = None
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if SEP_RE.match(cmd, i):
            break
        i += 1
    return cmd[start:i]


def fail_open_fields(payload, exc) -> tuple:
    """(command_to_hash, detail_label) for a FAIL_OPEN record. Extracted so it can be GRADED.

    It must hash THE SAME OBJECT the ALLOW/REFUSE paths hash, or the log cannot be joined across
    the fail-open boundary — orchestra-improvements, msg_a7f01070. When the command genuinely is
    not recoverable the row is LABELLED `no_command`, so a non-joining row is legible as
    non-joining instead of silently carrying a different id.
    """
    label = f"exc={type(exc).__name__}"
    try:
        cmd = (payload or {}).get("tool_input", {}).get("command") \
            if isinstance(payload, dict) else None
        if isinstance(cmd, str):
            return cmd, label
    except Exception:       # noqa: BLE001 — the fail-open path must not fail
        pass
    return "", label + " no_command"


def refusal_for(payload) -> str | None:
    """The importable decision. Returns the refusal reason, or None to allow.

    Importable (and `main` guarded) on purpose: hook logic that cannot be imported cannot be
    graded by a reviewer, which is how an unexercised guard gets cited as protection.
    """
    try:
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return None
        cmd = (payload.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or "msg_store.py" not in cmd:
            return None

        # A QUOTED heredoc body is not shell-interpreted and cannot contain a real invocation;
        # blank it so a `send` or a `>` mentioned inside prose cannot trip either rule.
        scan = re.sub(r"<<-?\s*'(\w+)'\n.*?\n\1(?=\n|$)", "<<'HEREDOC'", cmd, flags=re.S)

        # Only matches at real command positions count. A match inside a quoted argument is
        # someone TALKING about a send, not making one.
        live = list(_unquoted_spans(scan))
        sends = [m for m in SEND_RE.finditer(scan)
                 if any(a <= m.start() < b for a, b in live)]
        if not sends:
            return None

        if len(sends) > 1:
            record("REFUSE", cmd, f"rule=R1 sends={len(sends)}")
            return (f"{len(sends)} `msg_store.py send` invocations on ONE line. That is the "
                    f"stray-fragment shape: six instances across three seats came from exactly "
                    f"this, one of which put a 3,082-byte forged message in the store under a "
                    f"seat that never said it.")

        # R2 MUST BE QUOTE-AWARE TOO. I applied _unquoted_spans to R1 and left R2 reading raw
        # text, so a '>' inside a QUOTED --body read as a redirect and refused a legitimate send
        # — found live by orchestra-improvements (msg_875f099f), whose body quoted a shadow-log
        # line of the form `gm > b-fire-watcher`. The guard therefore refused precisely the
        # messages ANALYSING the behaviour it guards. Same root cause as the git-commit-stdin
        # guard (2cdeefed87), and the mirror direction: that one failed OPEN because a quoted
        # bracket looked like real syntax, this one failed CLOSED for the same reason.
        start = sends[0].end()
        span = _invocation_span(scan, start)
        end = start + len(span)
        redir = None
        for a, b in _unquoted_spans(scan):
            lo, hi = max(a, start), min(b, end)
            if lo >= hi:
                continue
            m = REDIR_RE.search(scan, lo, hi)
            if m:
                redir = m
                break
        if redir:
            record("REFUSE", cmd, "rule=R2 redirect")
            return (f"this send's own output is redirected ({redir.group(0).strip()!r}). The "
                    f"refusal is the ONLY signal that a send did not happen — five of the six "
                    f"historical instances hid it exactly this way and their authors had to go "
                    f"looking afterwards.")
        record("ALLOW", cmd, f"sends={len(sends)}")
        return None
    except Exception as exc:   # noqa: BLE001 — fail-open: never block mail because guard broke
        # FAIL-OPEN IS LOGGED. An unlogged fail-open is indistinguishable from a guard that ran
        # and found nothing, which is exactly the ambiguity finding 2 is about.
        #
        # AND IT MUST HASH THE SAME OBJECT THE OTHER PATHS HASH (orchestra-improvements,
        # msg_a7f01070). This first stringified the tool_input DICT, so the same command produced
        # a DIFFERENT cmd_sha on the fail-open path than on ALLOW/REFUSE — which broke the one
        # property offered in place of logging the text: "if you need to correlate two events,
        # the sha does it". It did, except across the fail-open boundary, which is exactly the
        # join somebody wants most — was the command that FAIL_OPENed at 12:40 the same one that
        # REFUSED at 12:45, i.e. did the guard start working again on the same input? A row that
        # cannot join is LABELLED `no_command` rather than silently carrying a different id.
        _cmd, _label = fail_open_fields(payload, exc)
        record("FAIL_OPEN", _cmd, _label)
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:       # noqa: BLE001
        return 0
    why = refusal_for(payload)
    if not why:
        return 0
    sys.stderr.write(
        "BLOCKED by send-shape-guard: " + why + "\n"
        "FIX: put the `msg_store.py send` on its OWN Bash call, with nothing else on the line "
        "and no redirect. Reads (ack/get/inbox) stay legal in compound lines and loops.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
