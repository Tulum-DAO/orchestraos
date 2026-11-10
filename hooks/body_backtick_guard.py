#!/usr/bin/env python3
"""PreToolUse(Bash) guard: refuse a msg_store.py send whose own arguments can be REWRITTEN BY
THE SHELL before the tool ever sees them.

ANY INLINE ARGUMENT CAN BE REWRITTEN BY THE SHELL BETWEEN YOU AND THE TOOL. That is the class,
and it is why this guard does not name the flags it protects. The rule is INVERTED: every
argument of the send invocation is checked, and the safe carriers (single quotes, quoted
heredocs, backslash escapes) are what is allowed out. An allow-list of flags re-breaks the day
someone adds a flag — the same lesson as asserting a contract by binding the call you make
rather than by counting its parameters.

Three instances from three seats in one day, 2026-09-20/22:
  * --body "...`git worktree remove ...`..." EXECUTED, removing two ~10 GB worktrees, running
    git prune and git gc --aggressive from gm's own shell, and blanking the commands out of the
    mail the recipient received (loud, and the reason this guard exists);
  * a stray fragment sending an 'x' row under another seat's name;
  * --subject "...`note`..." EXECUTED and SILENTLY ate a word from a delivered mail while the
    send reported success (QUIETER than the one the first version caught, and invisible to the
    sender — the old rule gated on --body|--text only, so it sailed through).

SAFE, and deliberately still allowed: a body in a QUOTED heredoc (cat > f <<'EOF' ... EOF) with
--body-file, and any SINGLE-QUOTED argument — bash expands neither. Backslash-escaped \\` and
\\$ inside double quotes are literal and are allowed too.

FAIL-OPEN on any parse error (exit 0): a guard that crashes must never be able to stop the
fleet from sending mail. Blocks with exit 2 + a reason on stderr that names the fix.

WHERE THIS GUARD STOPS (devex-review g6 condition C3, msg_206e934d). It is a PreToolUse(Bash)
hook, so IT BINDS THIS AGENT'S Bash TOOL CALLS AND NOTHING ELSE. Not cron, not @reboot, not a
watchdog, not a daemon, not another agent on another seat, and not any path that reaches
msg_store without going through the Bash tool. A fence that binds only the interactive path has
a door in it, so the door is enumerated here rather than left to be discovered.

MEASURED 2026-09-23, not asserted — and the ceiling is REAL but NARROWER than "117 call sites":
  * 117 files under scripts/ + services/ mention msg_store. Most are tests or reads.
  * 4 daemons import MessageStore DIRECTLY (dispatch.py, message-router.py, completion-watcher.py,
    inbox-bridge.py). THIS HAZARD CANNOT ARISE THERE: there is no shell in that path, so there is
    nothing to expand. Unguarded, but also unexposed — do not count them as holes.
  * 0 call sites use subprocess(..., shell=True) with msg_store.
  * 1 SHELL script sends with an INLINE argument, and it IS automated:
    scripts/gm-t0-minus-one-reminder.sh:31 passes --subject inline, driven by crontab `0 10 19 9 *`.
    Its :37 already runs $(TZ=America/Cancun date +%H:%M) inside a quoted inline argument — the
    exact construct this guard refuses for an agent, executing unguarded on the cron path. There
    it is deliberate and harmless; it is cited because it is the existence proof that the ceiling
    is not hypothetical.
So: the guard covers the path that produced all three incidents above, and a second path exists
where the same mistake would not be caught. Closing that is a different fix with a different
owner (a check inside msg_store itself would bind every caller); it is NOT done here.
"""
import json, re, sys

SEND_RE = re.compile(r"(^|[;&|]\s*|\n\s*)((?:python3?\s+)?\S*msg_store\.py)\s+send\b")


def danger_in_invocation(cmd: str, start: int) -> str | None:
    """Walk the send invocation's own argument span with a quote-state scan, from `start` to the
    end of that command. Returns the offending construct, or None.

    A quote-state scan rather than a regex because an apostrophe INSIDE a double-quoted string
    ("it's `whoami`") would make naive single-quote stripping swallow the rest of the line and
    ALLOW the very thing we are blocking. Inside single quotes bash expands nothing; inside
    double quotes it expands ` and $( unless backslash-escaped; unquoted, it expands both.

    THE SCAN IS DELIBERATELY NARROW AND STOPS AT THE END OF THE SEND INVOCATION (gm ruling
    msg_97e7ae1e, devex's boundary opinion made a ruling): this guard owns the send's OWN
    arguments. A backtick in an unrelated later command on the same line is a different hazard
    with a different owner, and a guard that grows into a general shell linter gets worked
    around — which costs more than the cases it would catch.
    """
    i, n, quote = start, len(cmd), None
    while i < n:
        ch = cmd[i]
        if quote is None and ch in ";\n" :
            return None                       # end of this command; later commands are not ours
        if quote is None and cmd.startswith(("&&", "||", "|"), i):
            return None
        if ch == "\\" and quote != "'":
            i += 2                            # \` and \$ are literal in double quotes / unquoted
            continue
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == '"':
                quote = None
            elif ch == "`":
                return "a backtick"
            elif cmd.startswith("$(", i):
                return "a $( ) command substitution"
        else:
            if ch == "'":
                quote = "'"
            elif ch == '"':
                quote = '"'
            elif ch == "`":
                return "a backtick"
            elif cmd.startswith("$(", i):
                return "a $( ) command substitution"
        i += 1
    return None


def main() -> int:
    d = json.load(sys.stdin)
    if d.get("tool_name") != "Bash":
        return 0
    cmd = (d.get("tool_input") or {}).get("command", "") or ""
    if "msg_store.py" not in cmd:
        return 0
    # QUOTED heredocs carry bodies bash never expands: replace each with a marker so their
    # contents cannot trip the scan and cannot hide a send invocation either.
    cmd = re.sub(r"<<-?\s*'(\w+)'\n.*?\n\1(\n|$)", "<<'HEREDOC'\n", cmd, flags=re.S)
    # EVERY send, not just the first (orchestra-builder g61; hit by g60 2026-09-23,
    # characterised by devex g6 msg_3c5ecfb9, routed by gm msg_29fa8096). This was `.search`,
    # so only the FIRST send in a command was ever scanned: a backtick in the arguments of a
    # SECOND send executed and silently ate a word from a DELIVERED mail while the send
    # reported {"sent": true}. Three separators reach it -- `;`, `&&` and a NEWLINE -- and the
    # newline is the one that matters, because that is how every multi-line Bash block on this
    # fleet is written, so the hole was reachable without the author thinking the command
    # compound.
    # THE PER-INVOCATION SCAN STAYS NARROW (gm ruling msg_97e7ae1e). Widening the SCAN is what
    # that ruling forbids; scanning every SEND is what it always assumed -- each match below is
    # still walked only across its own arguments, and a backtick in an unrelated non-send
    # command remains someone else's hazard.
    found = None
    for m in SEND_RE.finditer(cmd):
        found = danger_in_invocation(cmd, m.end())
        if found:
            break
    if not found:
        return 0                              # no send at all, or every send is safe
    sys.stderr.write(
        f"BLOCKED (body-backtick-guard): an inline argument of this msg_store send contains "
        f"{found} — bash EXECUTES it in your shell before msg_store is reached. On 2026-09-20 "
        f"this removed two worktrees and ran git gc --aggressive; on 2026-09-22 it silently ate "
        f"a word from a --subject while the send reported success. ANY inline argument can be "
        f"rewritten by the shell between you and the tool, which is why no flag is exempt.\n"
        f"FIX: write the body to a file with a QUOTED heredoc (cat > f <<'EOF' ... EOF) and pass "
        f"--body-file f; single-quote any other argument that must contain a backtick; and for a "
        f"subject, simply do not use one. Then verify the STORED subject after sending.\n")
    return 2


if __name__ == "__main__":
    # IMPORTABLE ON PURPOSE (devex msg_dc269b33): the first version ran main() at import, so a
    # reviewer trying to drive danger_in_invocation() got no output and exit 0 three ways, and
    # the two boundaries they were asked to attack went UNGRADED. A guard whose logic cannot be
    # imported can only be tested through a subprocess, which is exactly the coverage that hides
    # a boundary bug.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
