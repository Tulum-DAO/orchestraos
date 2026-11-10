#!/usr/bin/env python3
"""git_state_guard — PART 2 of the clobber-safe fix (congruence DEC-1788662573).

A PreToolUse Bash-matcher deny-hook. Git has no pre-checkout hook, so this blocks the destructive git
ops that revert or DELETE live state on the shared tree ~40 agents run git in:
  * checkout / restore of a protected path (state/, DOCS/, registry.json) or tree-wide (`-- .`)
  * reset --hard / --merge / --keep (reverts the worktree)
  * stash push / save / bare `git stash` (reverts the worktree)
  * clean (unless --dry-run) — the LETHAL TRAP: once the live dbs are UNTRACKED (PART 1), git clean
    treats them as garbage and PERMANENTLY DELETES the SQLite dbs + WAL sidecars.

★ TOKENIZE, never substring-match. The live delete-guard.sh false-positived on a command that merely
MENTIONED a pattern inside a string arg to another program. Here a shell SEGMENT is a git command
ONLY when its first token (after leading VAR=val assignments) is exactly `git`; a `git …` pattern
sitting in an `echo`/`agy`/`grep` argument is NOT a git invocation and is allowed.

Fail-safe: a git destructive op we cannot parse cleanly → DENY (fail-closed); a non-git or clearly
safe command → ALLOW. INERT until wired: settings.json PreToolUse wiring is a gm+the operator-armed step.
"""
import os
import re
import shlex
import sys

PROTECTED_PREFIXES = ("state/", "state\\", "DOCS/", "DOCS\\", "./state/", "./DOCS/")
PROTECTED_FILES = ("registry.json", "./registry.json")
# split a compound command into segments run as separate commands
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
# heredoc opener: << or <<- , optional quote around a bareword delimiter
_HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _strip_heredocs(command):
    """Remove heredoc BODIES so a git-destructive keyword sitting in heredoc data (which is stdin to a
    command, NOT an executed command) is not misparsed as a command segment — the gm-reported
    false-positive. SAFE-BY-CONSTRUCTION against the dangerous over-strip direction: a body is dropped
    ONLY when its closing delimiter line actually exists; an opener with no matching delimiter is left
    intact, so a real command line is never accidentally removed (which could let a threat escape)."""
    lines = command.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        out.append(line)                       # keep the opener line itself (its program != git-body)
        m = _HEREDOC_OPEN.search(line)
        if m:
            delim = m.group(2)
            # scan ahead for the closing delimiter (leading whitespace allowed for <<-)
            j = i + 1
            while j < n and lines[j].strip() != delim:
                j += 1
            if j < n:                          # delimiter FOUND -> this is a real heredoc; drop body+delim
                i = j + 1
                continue
            # no closing delimiter -> NOT a heredoc we can trust; do not strip (fail-safe)
        i += 1
    return "\n".join(out)
_RESET_WORKTREE_FLAGS = ("--hard", "--merge", "--keep")
_STASH_MUTATING = ("push", "save")            # bare `git stash` == push
_STASH_READONLY = ("list", "show", "apply", "pop", "drop", "branch", "clear")


def _is_protected(path):
    p = path.strip().strip("'\"")
    if p in PROTECTED_FILES:
        return True
    return any(p.startswith(pre) for pre in PROTECTED_PREFIXES) or p in ("state", "DOCS")


def _strip_env_assignments(tokens):
    """Drop leading VAR=value assignments (e.g. `GIT_DIR=x git ...`) so the program token is next."""
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    return tokens[i:]


# --- CONDITION 4 (gm msg_88ba4a40): REFUSE A MERGE THAT SILENTLY DRAGS ITS BASE -----------
# WHY THIS IS CODE AND NOT A HABIT. On 2026-09-25 merge 0f094857ea landed FOUR commits when one
# was authorized: the ref was based on d2f5263b63, which was not yet an ancestor of the target,
# so --no-ff brought the whole base chain. The damning part is not that it happened once — it is
# that THIS SEAT HAD ALREADY CAUGHT THE SAME HAZARD FOUR HOURS EARLIER and held a landing for it
# (g64, msg_2d63101b, subject beginning "HOLDING"), naming the exact instrument
# (`git log HEAD..<ref>`). The knowledge, the rule and the command were all in the seat's own
# outbox, and none of them survived a rotation plus a P0 marked LAND NOW. A rule that fails under
# mild urgency at the seat that authored it will fail everywhere, so it belongs in the tool.
# The trap is that building siblings on a common base — the right thing for INDEPENDENT GRADING —
# is exactly what makes a landing wide. Grading discipline and landing discipline pull opposite
# ways, and nothing in git says so at merge time.


def merge_chain_verdict(extra, named):
    """PURE core, so this is gradeable without a repo (the importable-hook-logic rule).

    ``extra``  = shas the merge would add BESIDES the ref tip, oldest-first, as
                 ``[(sha, subject), ...]``. Empty => a fast, clean landing.
    ``named``  = shas the operator explicitly acknowledged via LAND_CHAIN.
    Returns (deny: bool, unacknowledged: [sha, ...]).

    A sha counts as acknowledged when a named value is a PREFIX of it or it of a named value, so
    an operator may name short or full shas. Naming a sha that is not in the chain is NOT an
    error here: the point is informed consent to what IS riding, not an exact-set match that a
    stale note would break.
    """
    if not extra:
        return False, []
    un = []
    for sha, _subject in extra:
        if not any(sha.startswith(n) or n.startswith(sha) for n in named if n):
            un.append(sha)
    return bool(un), un


def _land_chain_named(tokens):
    """LAND_CHAIN=sha1,sha2 read ONLY from a TRUE ENV-PREFIX POSITION.

    THE MIRROR OF THE -m BUG, found by gm-rotator (msg_f03fb7c0) INSIDE the fix for it. The first
    version scanned EVERY token for anything starting with "LAND_CHAIN=", wherever it sat, so both
    of these silently turned a denial into a pass with no env assignment on the line at all:
        git merge sib -m "LAND_CHAIN=<rider sha> intended"
        git merge sib # LAND_CHAIN=<rider sha>
    Before, the message's words were parsed as REFS; then the message's words were parsed as
    ACKNOWLEDGEMENT. ONE ROOT BOTH TIMES: TEXT INSIDE THE COMMIT MESSAGE READ AS COMMAND
    STRUCTURE. And it needs nobody trying — landing messages routinely quote shas.
    It was also the THIRD instance in one shift of my own named pattern: this function's docstring
    already said "as an ENV PREFIX, not a --land-chain flag" and explained why an inline flag
    cannot exist, and then the code accepted one anywhere on the line. A stated invariant reads as
    a tested one.
    SO THE POSITION IS NOW THE AUTHORITY, not the spelling: only the leading VAR=value tokens that
    precede the program token can acknowledge — the same tokens _strip_env_assignments removes, and
    the only ones a shell would actually export.
    """
    out = []
    for t in tokens:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            break               # the env prefix has ended; everything after is argv or message
        if t.startswith("LAND_CHAIN="):
            out += [x.strip() for x in t.split("=", 1)[1].split(",") if x.strip()]
    return out


def _resolve_merge_chain(ref, cwd=None):
    """Ask git what the merge would add BESIDES the ref tip. Returns (extra, error).

    ``extra`` is oldest-first [(sha, subject)]. On any failure returns (None, reason) — and the
    CALLER fails CLOSED on that, deliberately: "cannot tell" is not "safe". That is affordable
    here and nowhere else in this guard, because a merge is a rare, deliberate act an operator
    can retry or acknowledge, whereas fail-closed on a common read would stop all work.
    """
    import subprocess
    try:
        r = subprocess.run(["git", "log", "--reverse", "--no-merges", "--format=%H%x00%s",
                            f"HEAD..{ref}"], capture_output=True, text=True, timeout=30, cwd=cwd)
    except Exception as e:                      # NAMED: the git call only
        return None, f"git-unavailable:{type(e).__name__}"
    if r.returncode != 0:
        return None, "git-log-failed"
    rows = [ln.split("\x00", 1) for ln in r.stdout.splitlines() if "\x00" in ln]
    if not rows:
        return [], "empty"
    # The ref TIP is the newest; everything older than it is what rides along.
    return [(h, sub) for h, sub in rows[:-1]], "ok"


def _eval_git(tokens):
    """tokens = a git invocation's argv (tokens[0]=='git'). Return (deny: bool, reason)."""
    args = [t for t in tokens[1:] if t]
    # skip pre-subcommand global flags (-C dir, -c k=v, --git-dir=…)
    sub = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        rest = args[i + 1:]
        break
    else:
        return False, "no-subcommand"
    if sub in ("switch",):
        return False, "branch-switch"              # not a state clobber
    if sub == "clean":
        if any(f in ("-n", "--dry-run") for f in rest):
            return False, "clean-dry-run"
        return True, "git-clean-deletes-untracked-live-state"   # lethal trap (deny always)
    if sub == "stash":
        head = next((r for r in rest if not r.startswith("-")), None)
        if head in _STASH_READONLY:
            return False, "stash-readonly"
        return True, "git-stash-reverts-worktree"   # bare stash / push / save
    if sub == "reset":
        if any(f in _RESET_WORKTREE_FLAGS for f in rest):
            return True, "git-reset-worktree-revert"
        return False, "reset-index-only"            # --soft/--mixed do not touch the worktree
    if sub in ("checkout", "restore"):
        pathspecs = _pathspecs(rest)
        if pathspecs is None:                        # branch switch, no pathspec
            return False, "branch-checkout"
        if not pathspecs:                            # `checkout --` with nothing / bare `.`
            return True, "checkout-tree-wide"
        for ps in pathspecs:
            if ps in (".", "./") or _is_protected(ps):
                return True, f"checkout-touches:{ps}"
        return False, "checkout-safe-pathspec"
    return False, f"nondestructive:{sub}"


def _pathspecs(rest):
    """Extract the pathspecs of a checkout/restore. Return None if it's a branch switch (no pathspec),
    [] if tree-wide (`.` alone or empty after `--`), else the list of path args."""
    if "--" in rest:
        after = rest[rest.index("--") + 1:]
        if not after or after == ["."]:
            return []
        return after
    # no `--`: restore always takes paths; checkout with a lone non-flag = branch switch
    paths = [r for r in rest if not r.startswith("-")]
    if not paths:
        return []
    if paths == ["."]:
        return []
    # a single ref-looking token with no slash and not a real path => branch switch
    if len(paths) == 1 and "/" not in paths[0] and "." not in paths[0].split("/")[-1] \
            and not _is_protected(paths[0]):
        return None
    return paths



# --- `git commit` reading its message from STDIN (gm AUTHORISED msg_8c0c5de9, 2026-09-22) --------
# WHAT HANGS: `git commit -F-` reads the message from stdin. Under the Bash tool stdin is not a
# terminal and may never EOF, so git blocks FOREVER while holding .git/index.lock — silently, with
# no error, on a tree ~40 agents share. It bit two seats in one day; both had the rule written down,
# one in their own baton IN CAPITALS. A recorded rule fails at the moment it is needed because the
# failure is TEMPLATING, not forgetting, so the check has to attach to the SHAPE of the command.
#
# THE DISCRIMINATOR IS NOT THE SPELLING, IT IS WHETHER STDIN IS ATTACHED. `echo msg | git commit -F-`
# terminates, because the pipe closes (gm's own observation). A heredoc and a `< file` redirect are
# the same case. So this blocks ONLY a git commit that reads stdin with NO stdin source on that
# command. Everything else — `-F <file>`, `--file=<path>`, any `-F-` outside a git commit, any other
# git subcommand — is untouched.
#
# BOTH SPELLINGS ARE BLOCKED, spaced and unspaced, and that IS the ruling (gm msg_83f1252d). His
# original condition listed the spaced form among those that must pass; he amended it after I raised
# it, because it is the same hazard — both read stdin, both hang when nothing feeds them — and
# allowing one while blocking the other is incoherent on the only axis that matters. His words:
# "I was pattern-matching on the FLAG SPELLING when the real property is STDIN ATTACHMENT."
# Recorded here rather than left as a divergence note, because a wrong reason sitting next to a
# right decision gets inherited.
#
# WHAT THIS DOES NOT COVER, stated because a guard that implies coverage it lacks is worse than none
# (gm condition 3): it does NOT catch a retry loop around a shared-index stage — that is not
# honestly pattern-matchable — and it cannot see a non-shell mistake such as acking a batch of
# messages without grading them. Those remain practice problems. This guard covers ONE fragment.
_STDIN_MSG_FLAGS = ("-F-", "--file=-")
_STDIN_MSG_PAIR = ("-F", "--file")


def _segments_with_stdin_context(command):
    """[(segment_text, piped_into)] — the separator BEFORE each segment decides whether something
    upstream is feeding it stdin. The existing _SEGMENT_SPLIT discards that, so this keeps it."""
    parts = re.split(r"(&&|\|\||[;\n|&])", command)
    out, piped = [], False
    for i, part in enumerate(parts):
        if i % 2:                                   # a separator
            piped = (part == "|")
            continue
        out.append((part, piped))
    return out


# Flags git refuses to combine with -F. Measured by effect 2026-09-22: `git commit <stdin> -m x`
# exits 128 "options '-m' and '-F' cannot be used together" IMMEDIATELY — git rejects the argv
# before it ever reads stdin, so that shape CANNOT hang. devex named this case (msg_c7d2784d) and
# blocking it would be an over-match on gm condition 2 — and worse, the refusal would assert a hang
# that cannot happen, which breaks element 2 of the refusal convention (the WHY must be a true fact
# about THIS input). Nothing legitimate is lost either way, but a guard that says something false
# is how people learn to disbelieve guards.
# MEASURED AGAINST THE INSTALLED BINARY (git 2.43.0, 2026-09-22), not read from the docs — and
# `git rejects this combination` is a claim about a BINARY, not about a standard, so re-probe it if
# git is upgraded or this moves to another box (devex msg_e8bb45ca).
#   -m / --message= / -C / --reuse-message= / -c / --reedit-message= / --fixup  -> argv REJECTED
#   --squash / --amend                                                          -> git ACCEPTS
# --squash was in this list and is NOT rejected: `git commit -F- --squash=HEAD` reaches the stdin
# read and CAN hang, so listing it here made the guard ALLOW a real hang. That was the over-match
# fix over-reaching — I correctly stopped blocking shapes git rejects and took one with it that git
# permits. --amend is likewise accepted and is deliberately ABSENT, so it keeps blocking.
_MESSAGE_CONFLICT_FLAGS = ("-m", "--message", "--fixup", "-C", "--reuse-message",
                           "-c", "--reedit-message")


def _reads_message_from_stdin(tokens):
    try:
        start = tokens.index("commit") + 1
    except ValueError:
        start = 1
    after = tokens[start:]
    if any(t in _MESSAGE_CONFLICT_FLAGS or t.startswith(("--message=", "--fixup="))
           for t in after):
        return False                      # git errors on the argv; it never reaches stdin
    for i, t in enumerate(after):
        if t in _STDIN_MSG_FLAGS:
            return True
        if t in _STDIN_MSG_PAIR and i + 1 < len(after) and after[i + 1] == "-":
            return True
    return False


def _git_subcommand(tokens):
    """First non-flag token after `git`, skipping global option/value pairs. None when unclear."""
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-c", "-C", "--git-dir", "--work-tree", "--namespace"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return None


def _has_stdin_redirect(tokens):
    """A `<` only counts as a stdin source when it is REDIRECT-SHAPED — i.e. its own token, or one
    beginning a filename/heredoc delimiter. Checking the raw segment text instead (`"<" in seg`)
    was disarmed by an angle bracket inside a QUOTED ARGUMENT, and the worst case was this repo's
    OWN commit convention: a `Co-Authored-By: ... <noreply@anthropic.com>` trailer made the guard
    allow the exact command it exists to catch (devex msg_3c5bdadf, driven). An `--author='a <b@c>'`
    did the same. House style, not an exotic edge.
    shlex.split has already removed the quotes, so a bracket that came from inside a quoted argument
    lives INSIDE a token and never starts one — which is precisely the distinction that was missing.
    """
    return any(t.startswith("<") for t in tokens)


def _bare_stdin_commit(command):
    """True when a `git commit` would read its message from stdin with NO stdin source attached.
    Unsure -> False (ALLOW): this rule is narrow by construction and gm condition 2 is that
    over-matching on the shared tree blocks legitimate work at the worst moment."""
    for seg, piped in _segments_with_stdin_context(command):
        seg = seg.strip()
        if not seg or "commit" not in seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            continue                                # unparseable -> not our narrow business
        tokens = _strip_env_assignments(tokens)
        if not tokens or os.path.basename(tokens[0]) != "git":
            continue
        if _git_subcommand(tokens) != "commit":
            continue
        if not _reads_message_from_stdin(tokens):
            continue
        if piped or _has_stdin_redirect(tokens):
            continue                                # stdin IS attached: it terminates
        return True
    return False


# Flags on `git merge` that CONSUME THE NEXT TOKEN. Their value does not start with "-", so a
# naive "every non-dash token is a ref" read counts it as a ref — and `-m "Merge item 32 (P0)"`
# then made len(refs) != 1 and DISABLED the gate. That is the exact command shape a landing uses:
# merge 0f094857ea, the incident this gate exists to prevent, carried a custom -m message and
# would have passed straight through the first version (gm-rotator measured it end to end).
_MERGE_VALUE_FLAGS = frozenset({
    "-m", "--message", "-F", "--file", "-s", "--strategy", "-X", "--strategy-option",
    "-S", "--gpg-sign", "--into-name", "--log", "--cleanup",
})


def _merge_refs(rest):
    """The REF operands of a `git merge` argv, with option VALUES and `#` comments removed.

    `--flag=value` needs no skip (the value is inside the token). A token that begins a shell
    comment ends the argv: shlex.split does not strip comments, so `git merge ref # approved`
    otherwise contributes 'approved' as a second "ref" — which was a second way to silence the
    gate, and worse, the comment carrying an operator's authorization was itself what disabled it.
    """
    refs = []
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("#"):
            break                     # rest of the line is a comment
        if t in _MERGE_VALUE_FLAGS:
            i += 2                    # skip the flag AND its value
            continue
        if t.startswith("-"):
            i += 1                    # a switch, or --flag=value
            continue
        refs.append(t)
        i += 1
    return refs


def _eval_merge_chain(tokens, env_tokens=None, cwd=None):
    """(deny, reason) for `git merge <ref>` — the condition-4 gate. Non-merges ALLOW.

    ``env_tokens`` are the PRE-STRIP tokens, because LAND_CHAIN rides as an env assignment and
    decide() strips those to locate the git argv. ``cwd`` is the directory the command runs in
    (decide() tracks it); a ``git -C <dir>`` on the merge itself is applied on top of it."""
    args = [t for t in tokens[1:] if t]
    sub = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            cwd = _join_dir(cwd, args[i + 1])
            i += 2
            continue
        if a in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        sub = a
        rest = args[i + 1:]
        break
    if sub != "merge":
        return False, "not-a-merge"
    # --abort/--continue/--quit take no ref and land nothing.
    if any(f in ("--abort", "--continue", "--quit") for f in rest):
        return False, "merge-control-flag"
    refs = _merge_refs(rest)
    if len(refs) != 1:
        # FAIL-CLOSED, and this line is the whole finding of gm-rotator's HOLD on the first
        # version (msg_2f44ca46). It used to `return False` — ALLOW — "reasoning about ONE named
        # ref and saying so rather than guessing". IT SAID SO TO ITSELF AND THEN ALLOWED THE
        # MERGE. Two cannot-tell branches sat ELEVEN LINES APART in this function pointing in
        # OPPOSITE directions: an unresolvable chain denied (correct) while an un-attributable
        # ref allowed. A GUARD IS A WRITER-SHAPED THING: when it cannot tell, it must refuse.
        return True, ("merge-refs-unattributable[%d]: %s — name exactly ONE ref so the chain can "
                      "be attributed" % (len(refs), refs or "none"))
    # The chain is resolved IN THE MERGE'S OWN REPO (gm msg_b50af786: with no cwd, every merge in
    # any other repo resolved against this tree and was refused merge-chain-undeterminable).
    extra, err = _resolve_merge_chain(refs[0], cwd=cwd)
    if extra is None:
        return True, f"merge-chain-undeterminable:{err}"
    named = _land_chain_named(env_tokens if env_tokens is not None else tokens)
    deny, un = merge_chain_verdict(extra, named)
    if not deny:
        return False, "merge-chain-clean-or-acknowledged"
    listing = "; ".join(f"{h[:10]} {sub[:60]}" for h, sub in extra if h in un)
    return True, f"merge-drags-unacknowledged-base[{len(un)}]: {listing}"


def _join_dir(base, d):
    """Resolve a `cd`/`-C` target against the directory it is relative to (None = our own cwd)."""
    return os.path.join(base or os.getcwd(), os.path.expanduser(d))   # join keeps an absolute d


def decide(command, cwd=None):
    """Return (allow: bool, reason: str) for a Bash command string. DENY (allow=False) a destructive
    git op touching protected state; ALLOW everything else.

    ``cwd`` is the session's working directory (the hook input's ``cwd``). A plain `cd <dir>`
    segment earlier in the same command moves it, so `cd ~/repos/x && git merge y` resolves y's
    chain in ~/repos/x, not in whatever directory the hook process happens to run in."""
    if not command or "git" not in command:
        return True, "no-git"
    command = _strip_heredocs(command)         # heredoc bodies are data, not commands
    if "git" not in command:
        return True, "no-git-after-heredoc-strip"
    if _bare_stdin_commit(command):
        return False, "git-commit-reads-stdin-with-no-stdin-source"
    for seg in _SEGMENT_SPLIT.split(command):
        seg = seg.strip()
        if not seg:
            continue
        try:
            tokens = shlex.split(seg)
        except ValueError:
            # Unparseable segment (e.g. an unbalanced quote in a string arg). Fail-CLOSED ONLY when
            # the segment is ACTUALLY a git invocation — its FIRST token (crude whitespace split,
            # env-assignments stripped) is `git` — AND a destructive keyword is present. A non-git
            # command that merely co-mentions the words in a string arg must ALLOW (same principle as
            # the heredoc fix: reason about the real git argv, not keyword co-occurrence anywhere).
            crude = _strip_env_assignments(seg.split())
            if crude and os.path.basename(crude[0]) == "git" and re.search(
                    r"\b(checkout|restore|reset|stash|clean)\b", seg):
                return False, "unparseable-git-destructive-fail-closed"
            continue
        # KEEP THE PRE-STRIP TOKENS: the env assignments are stripped to find the git argv, and
        # LAND_CHAIN rides as one of them. My first wiring read LAND_CHAIN off the STRIPPED
        # tokens, so an acknowledged stack was still refused — the acknowledgement could not be
        # expressed at all. The pure-core test passed throughout (it never goes through decide);
        # only the end-to-end one caught it, which is the argument for having both.
        if len(tokens) == 2 and tokens[0] == "cd":
            cwd = _join_dir(cwd, tokens[1])
            continue
        env_tokens = list(tokens)
        tokens = _strip_env_assignments(tokens)
        if not tokens or os.path.basename(tokens[0]) != "git":
            continue                                  # segment is not a git invocation (anti-FP core)
        deny, reason = _eval_git(tokens)
        if deny:
            return False, reason
        merge_deny, merge_reason = _eval_merge_chain(tokens, env_tokens, cwd=cwd)
        if merge_deny:
            return False, merge_reason
    return True, "allowed"


def main():
    import json
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    allow, reason = decide(command, cwd=data.get("cwd") or None)
    if allow:
        return 0
    if reason == "git-commit-reads-stdin-with-no-stdin-source":
        # Written to docs/CONVENTION_a-refusal-that-teaches.md: what was refused, why, what did NOT
        # happen, the likely cause, and what is STILL legal.
        sys.stderr.write(
            "BLOCKED (git-state-guard): this `git commit` reads its message from STDIN and no "
            "stdin source is attached to it. NOTHING WAS RUN — no commit, no staging change, and "
            "the shared index.lock was not touched.\n"
            "WHY: under the Bash tool stdin is not a terminal and may never EOF, so git blocks "
            "FOREVER holding .git/index.lock — silently, no error — on a tree ~40 agents share. "
            "Two seats hit this in one day; both had the rule written down.\n"
            "CAUSE: almost always `-F-` typed from muscle memory where `-F <file>` was meant.\n"
            "STILL LEGAL: `git commit -F <file>` (use a scratch file), `git commit -m '...'`, and "
            "`-F-` WITH stdin attached — `echo msg | git commit -F-`, a heredoc, or `< file` — "
            "because those terminate.\n"
            "NOT COVERED by this guard, so do not read it as protection: a retry loop around a "
            "shared-index stage, and anything that is not shell-shaped.\n"
            "CEILING: this is a PreToolUse hook, so it sees an AGENT'S Bash calls ONLY. Cron lines, "
            "daemons, watchdogs and @reboot recovery never pass through it and are NOT protected. "
            "A fence that binds only the agent has a door in it — that shape hit this fleet three "
            "times today. The durable answer is a refusal in the git wrapper, where it would travel "
            "to every caller.\n")
        return 2
    if reason.startswith("merge-drags-unacknowledged-base") or reason.startswith(
            "merge-chain-undeterminable"):
        sys.stderr.write(
            f"BLOCKED (git-state-guard): {reason}\n"
            "WHAT THIS REFUSED: a merge that would land commits BESIDES the ref you named, "
            "because the ref's base is not yet an ancestor of the target. NOTHING was merged.\n"
            "WHY: on 2026-09-25 this landed FOUR commits when ONE was authorized (merge "
            "0f094857ea). Building siblings on a common base is correct for INDEPENDENT "
            "GRADING and is exactly what makes a landing wide — the two disciplines pull "
            "opposite ways and git says nothing at merge time.\n"
            "IF THE CHAIN IS INTENDED (a stack landing on purpose), acknowledge it by naming "
            "the extra shas: LAND_CHAIN=<sha>,<sha> git merge --no-ff <ref>\n"
            "IF IT IS NOT, land the ref's content alone (cherry-pick, verified BY CONTENT since "
            "a cherry-pick is never an ancestor) or land its base first under its own grade.\n"
            "A 'cannot tell' verdict is refused too, on purpose: a merge is rare and deliberate, "
            "so not-knowing must not read as safe here.\n"
            "CEILING, so this is not read as more than it is: PreToolUse sees an AGENT'S Bash "
            "only. A merge from cron, a daemon or a human shell never passes through it.\n")
        return 2
    sys.stderr.write(
        f"BLOCKED (git-state-guard): this git command would revert or DELETE live state on the "
        f"shared tree ({reason}). Protected: state/, DOCS/, registry.json. This class of op erased "
        f"live DECs + the registry 3x. If you truly must, ask the operator. Never stash/checkout/reset/clean "
        f"the shared live tree.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
