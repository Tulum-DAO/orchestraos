#!/usr/bin/env python3
"""Fail the build when an OPERATOR IDENTIFIER would be published to a public repo.

This is not a credential scanner. `secret-scan` already hunts credentials, and it is
blind to this class by construction: `.secrets.baseline` enables
`detect_secrets.filters.heuristic.is_potential_uuid`, whose whole job is to discard
uuid-shaped findings, so a session id cannot be reported by it even in principle.

The class this gate exists for, from the three instances found while landing #156:
a real incident id, a test fixture carrying a real session id that resolved to a live
private transcript, and a docstring narrating an incident to the millisecond.

THE STATIC-DECISION CONSTRAINT. CI has no access to the operator's transcript store, so
this scanner cannot ask "is this session id real". It asks the only question available
from the string alone: "is this DISTINGUISHABLE from a real one?" If it is not, it must
not be published. A test that wants a session id should use a visibly synthetic one --
which costs nothing, and is what the sibling tests in #156 already did.

FAIL-SAFE BY CONSTRUCTION. A file this scanner cannot read or decode is REPORTED, never
skipped. Silently skipping what you cannot parse is a gate that an encoding defeats, and
it is the same fail-open class #156 forecloses elsewhere. Coverage (scanned / skipped /
baselined) is always printed, so "found nothing" can never be a misconfigured invocation
wearing a green tick.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

# --------------------------------------------------------------------------- rules

# A uuid is "indistinguishable from real" at this many distinct hex digits or more.
#
# The justification is the FALSE-NEGATIVE RATE, not the shape of this tree. A reviewer
# ran 2M random uuids: P(distinct <= 9) = 7.7e-5, and the same for v7's timestamp-prefixed
# form. So a real session id slipping past this rule is a 1-in-13,000 event.
#
# It is NOT justified by the gap this tree happens to show (synthetic <=8, random >=13,
# nothing in 9..12). That gap is a small-sample artifact: 9.43% of genuinely random uuids
# land in 9..12, so with 19 of them an empty band is only ~15% likely. An earlier draft of
# this comment argued from the gap and was wrong; the number survived for a better reason.
UUID_DISTINCT_HEX_THRESHOLD = 10

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    # Dashless 32-hex is the same identifier with the hyphens removed. `detect-secrets`
    # already reports this form today (it clears the entropy limit), so leaving it out
    # would make this gate weaker than the one it supplements.
    r"|\b[0-9a-fA-F]{32}\b"
)

# P2 is CONTEXT-GATED on purpose. A bare 8-hex run matches every abbreviated git sha in
# the repo; ungated this rule is unusable, and a gate nobody trusts gets switched off,
# which is strictly worse than no gate. It fires only next to a session-id keyword.
# Compound names count: `green_sid`, `blue_sid`, `NEW_SID`, `operator_sid` and
# `sessionID` are all the same context. An earlier `\bsid\b` missed every one of them.
_SID_PREFIX = re.compile(
    r"(?:--resume|--settings|--session-id|\bclaude -r\b|resume=|rollout-"
    r"|[A-Za-z]*_?(?:session_?id|sid))"
    r"[\s=:\"'/]+"
    r"(?P<val>[0-9a-fA-F]{8,})",
    re.IGNORECASE,
)

_SID_CONTEXT_WORD = re.compile(
    r"--resume|--settings|--session-id|\bclaude -r\b|resume=|rollout-"
    r"|[A-Za-z]*_?session_?id|[A-Za-z]*_?sid\b",
    re.IGNORECASE,
)
_BARE_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")

_INCIDENT_ID = re.compile(r"\b\d{8}T\d{6}Z\b")
_SNAPSHOT = re.compile(r"\b(?:logs|state)/red-alert/(?P<name>[^\s`'\")\]]+)")
# A capture path leaks only when its FILENAME bears an identifier: an incident
# timestamp-id, a uuid, or a long hex run. Constants and placeholders do not.
_NAME_BEARS_IDENTIFIER = re.compile(r"\d{8}T\d{6}Z|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}|[0-9a-fA-F]{12,}")

_HOME_PATH = re.compile(r"/home/shaw\b|/Users/ShawCole\b")  # operator-id-ok: the home-path detector's own pattern, not an occurrence
_CITY_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:Z|UTC)?\s+(?:Tulum|Cancun|Berlin|London|Tomorrowland)\b")
_HOST_EMAIL = re.compile(r"\bsrv1397016\b|\bmacbook-pro-6\b|[A-Za-z0-9._%%+-]+@(?:gmail|arkdata)\.[A-Za-z]{2,}")

# A verbatim transcript timestamp: a NON-ROUND millisecond field is the tell. An incident
# narrated to the millisecond correlates back to one private transcript; the same reasoning
# told at minute precision does not. This was the third #156 leak (`...T17:33:00.563Z`,
# copied verbatim out of a live transcript) and no other rule catches it.
#
# Why `.000` is exempt: hand-written fixtures use round milliseconds, and flagging them
# turned 2 real findings into 44 alarms when this rule was first measured. A gate at 44:2
# signal-to-noise gets switched off, and a switched-off gate catches nothing. `.000` is
# itself evidence a human typed the value rather than copying it.
_MS_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.(?!000Z)\d{3}Z\b"
    r"|\b\d{2}:\d{2}:\d{2}\.(?!000Z)\d{3}Z\b"
)

# Placeholders are the FORM #156 landed in. They must keep passing, or this gate fails the
# very fix it exists to enforce.
# Provider-side correlation ids. Found live on public main in a fixture whose `session_id`
# and `uuid` HAD been placeholder-scrubbed: the scrubber replaced the fields it recognised
# and left `msg_…`/`req_…` behind, and that msg id resolves to a real private transcript.
# Mixed-case base58-ish, so neither the uuid rule, nor the hex rule, nor detect-secrets
# sees it.
_PROVIDER_ID = re.compile(r"\b(?:msg|req|toolu|srvtoolu|resp|chatcmpl|acct|cus|sub|evt)_[A-Za-z0-9]{20,}\b")

_PLACEHOLDER = re.compile(
    r"<[A-Za-z][A-Za-z0-9 _-]*>"     # <sid>, <incident id>, <snapshot>, <ts>, <UTC ts>
    r"|\{[a-z_]+\}"                  # {sid} format slots
    r"|%\([a-z_]+\)s"
    r"|\$\{?[A-Za-z_]+\}?",          # $SID / ${SID}
)



def _distinct_hex(uuid_value: str) -> int:
    return len(set(uuid_value.replace("-", "").lower()))


def is_synthetic_uuid(value: str) -> bool:
    """True when a reader can SEE this uuid is a placeholder.

    Deliberately conservative: when in doubt this returns False and the value is
    reported. A false alarm costs one synthetic-uuid edit; a miss publishes a real
    session id to a public repo, permanently.
    """
    return _distinct_hex(value) < UUID_DISTINCT_HEX_THRESHOLD


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    rule: str
    value: str
    why: str

    def key(self) -> str:
        """Baseline identity: file + rule + the matched VALUE, never the line number.

        Line number is excluded so an ordinary edit does not churn the baseline, while
        CHANGING a value DOES invalidate it: a changed value is a different identifier and
        has not been accepted.

        The value is stored IN PLAINTEXT, deliberately. A reviewer briefly talked me into
        hashing it on the grounds that an accept-list quoting what it accepts republishes
        it. That argument does not hold here, for two reasons:

        1. Nothing opaque is ever baselined. Session ids, sid prefixes, provider ids and
           verbatim transcript timestamps are FIXED, not accepted. What remains is the
           readable classes -- a hostname in a CORS test, a home path in a checklist, a
           city-plus-clock line in a deck -- and those are baselined PRECISELY BECAUSE a
           human can read them and judge them. A digest destroys the one property that
           made accepting them defensible.
        2. Every baselined value is already in this repo at the path the entry cites. The
           baseline adds no exposure; it is an index, not a second publication.

        So the entry stays legible, and `_baseline_self_accepts` below keeps the file from
        alarming on its own index without creating a place to hide a NEW value.
        """
        return f"{self.path}::{self.rule}::{self.value}"

    def render(self) -> str:
        return f"{self.path}:{self.line_no}: [{self.rule}] {self.value} -- {self.why}"


#: An inline exemption, for the one legitimate case: a line whose job IS to contain a
#: leak-shaped string -- this gate's own test inputs, and fixtures that must carry a
#: literal shape. It takes a reason, and exempted lines are COUNTED AND PRINTED, never
#: silent, so a pragma cannot quietly become a permission. A pragma on a value that
#: resolves to real operator data is still a leak: `--resolve-against` ignores pragmas.
_PRAGMA = re.compile(r"operator-id-ok:\s*\S")
_PRAGMA_HITS: list[str] = []


def _scan_text(path: str, text: str) -> list[Finding]:
    lines = text.splitlines()
    out: list[Finding] = []

    # Which lines sit in a session-id CONTEXT. A +-2 line window, because the keyword and
    # the value are routinely on different lines -- a bare 8-hex in a trailing comment
    # (`# green, not blue 0ed9c5d7`) is how one real sid hid from a line-scoped gate.  operator-id-ok: illustrative hex, resolves to nothing
    ctx: set[int] = set()
    for i, line in enumerate(lines):
        if _SID_CONTEXT_WORD.search(line):
            ctx.update(range(max(0, i - 2), min(len(lines), i + 3)))

    for i0, line in enumerate(lines):
        i = i0 + 1
        if _PRAGMA.search(line):
            _PRAGMA_HITS.append(f"{path}:{i}")
            continue
        placeholders = set(_PLACEHOLDER.findall(line))

        uuid_spans = [(u.start(), u.end()) for u in _UUID.finditer(line)]
        uuids_on_line = [u.group(0) for u in _UUID.finditer(line)]

        def _inside_a_uuid(start: int, end: int) -> bool:
            """A hex run that is PART of a uuid on this line is not a separate finding.

            Without this, the inner groups of a visibly synthetic uuid
            (`...cccc-333344445555`) get re-reported as bare hex runs, and the gate
            contradicts its own synthetic-uuid exemption.
            """
            return any(a <= start and end <= b for a, b in uuid_spans)

        for val in uuids_on_line:
            if is_synthetic_uuid(val):
                continue
            out.append(Finding(path, i, "session-id", val,
                               f"uuid with {_distinct_hex(val)} distinct hex digits is "
                               f"indistinguishable from a real session id (threshold "
                               f"{UUID_DISTINCT_HEX_THRESHOLD}); use a visibly synthetic one"))

        for m in _SID_PREFIX.finditer(line):
            val = m.group("val")
            if _inside_a_uuid(m.start("val"), m.end("val")):
                continue
            if val in placeholders or len(set(val.lower())) <= 2:
                continue
            out.append(Finding(path, i, "session-id-prefix", val,
                               "hex run in a session-id context is a session id by "
                               "construction; publish a <placeholder> instead"))

        # A bare hex run with no keyword ON this line, but inside the +-2 window.
        if i0 in ctx:
            for m in _BARE_HEX.finditer(line):
                val = m.group(0)
                if _inside_a_uuid(m.start(), m.end()):
                    continue
                if val in placeholders or len(set(val.lower())) <= 2:
                    continue
                # The window rule is WEAKER evidence than an explicit keyword, so it
                # demands a hex letter. An all-decimal run near the word "sid" is almost
                # always an epoch or a row id (`1789507884583046`), and flagging those is
                # the noise that gets a gate switched off. A keyword-adjacent value still
                # fires whatever its digits, where the context carries the proof.
                if not re.search(r"[a-f]", val, re.IGNORECASE) or len(val) > 40:
                    continue
                if any(f.line_no == i and f.value == val for f in out):
                    continue
                out.append(Finding(path, i, "session-id-prefix", val,
                                   "bare hex run within 2 lines of a session-id keyword"))

        for m in _PROVIDER_ID.finditer(line):
            # Same synthetic exemption the uuid rule gets: a visibly placeholder suffix
            # is the form a fixture SHOULD use, and a gate that rejects its own prescribed
            # replacement is unusable.
            if len(set(m.group(0).split("_", 1)[1].lower())) <= 2:
                continue
            out.append(Finding(path, i, "provider-id", m.group(0),
                               "provider-side correlation id; these resolve to a private "
                               "transcript and no credential scanner sees them"))

        for m in _INCIDENT_ID.finditer(line):
            out.append(Finding(path, i, "incident-id", m.group(0),
                               "incident timestamp-id; incident ids stay in the operator's "
                               "own state/red-alert/"))

        for m in _SNAPSHOT.finditer(line):
            if not _NAME_BEARS_IDENTIFIER.search(m.group("name")):
                continue
            out.append(Finding(path, i, "capture-filename", m.group(0),
                               "capture/snapshot filename carries an identifier; publish "
                               "the shape, not the name"))

        for m in _HOME_PATH.finditer(line):
            out.append(Finding(path, i, "home-path", m.group(0), "operator home path"))

        for m in _CITY_CLOCK.finditer(line):
            out.append(Finding(path, i, "city-clock", m.group(0).strip(),
                               "city plus local clock discloses the operator's location"))

        for m in _HOST_EMAIL.finditer(line):
            out.append(Finding(path, i, "host-or-email", m.group(0),
                               "operator host or personal address"))

        for m in _MS_TIMESTAMP.finditer(line):
            out.append(Finding(path, i, "transcript-timestamp", m.group(0),
                               "millisecond-precision timestamp correlates to one private "
                               "transcript; keep the shape, drop the clock"))
    return out


def scan_file(path: str) -> tuple[list[Finding], str | None]:
    """Return (findings, skip_reason). An unreadable file is REPORTED, never skipped."""
    # No extension allowlist: skipping by filename is the encoding-defeat class this
    # scanner claims to foreclose -- `leak.png` would simply never be read. Content
    # decides, and every skip is counted.
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return [Finding(path, 0, "unreadable", path, f"cannot read ({exc}) -- "
                        "reported rather than skipped, so an unreadable file cannot "
                        "defeat this gate")], None
    # Decode BEFORE deciding "binary". A UTF-16 file is full of NUL bytes, so a naive
    # NUL check calls it binary and skips it silently -- and a session id inside it is
    # never seen. That is the hole this gate claims to foreclose, so try the text codecs
    # a real file might use, and only then call it binary.
    text = None
    for codec in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            cand = raw.decode(codec)
        except (UnicodeDecodeError, UnicodeError):
            continue
        # A wrong-codec decode yields mojibake peppered with NULs; a right one does not.
        if "\x00" in cand:
            continue
        text = cand
        break

    if text is None:
        if b"\x00" in raw[:8192]:
            return [], "binary content (decodes as no text codec)"
        return [Finding(path, 0, "undecodable", path,
                        "not decodable as any text codec -- reported rather than "
                        "skipped, so an encoding cannot defeat this gate")], None

    return _scan_text(path, text), None


def _baseline_self_accepts(findings, baseline_path, baseline_keys):
    """Keys for findings INSIDE the baseline file that merely quote its own index.

    The baseline lists `path::rule::value` in plaintext, so scanning it finds every value
    it accepts -- in the baseline's own file. Those are not a second leak; they are the
    index naming what was already accepted somewhere else.

    This is deliberately NOT a blanket exemption for the file: a value is self-accepted
    only when that exact (rule, value) pair is accepted for some OTHER path. Paste a NEW
    identifier into the baseline and it still alarms, so the accept-list cannot be used as
    a hiding place.
    """
    accepted_pairs = set()
    for k in baseline_keys:
        try:
            pth, rule, value = k.split("::", 2)
        except ValueError:
            continue
        if pth != baseline_path:
            accepted_pairs.add((rule, value))
    return {f.key() for f in findings
            if f.path == baseline_path and (f.rule, f.value) in accepted_pairs}


def load_baseline(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        data = json.load(fh)
    return {e["key"]: e.get("reason", "") for e in data.get("accepted", [])}


def _resolve_mode(args) -> int:
    """Print the candidates that resolve to something real under a given HOME.

    Why this exists as a shipped mode rather than a one-off script: deriving the
    fix-now population by hand was attempted FOUR times while building this gate, by
    three different parties, and produced a different answer every time (3, then 14, then
    21 real session ids) because each attempt enumerated the stores it happened to know:
    `~/.claude/projects`, then `~/.config/superpowers/conversation-archive`, then
    `~/.codex/sessions` and `~/.gemini/antigravity-cli`. Enumerating stores is the bug.
    This searches the filesystem instead, so the next person does not have to rediscover
    the method -- or the stores.

    It NEVER runs in CI (a runner has no operator HOME) and never writes a resolved value
    into a tracked file, because that would republish exactly what the gate forbids.
    """
    home = os.path.expanduser(args.resolve_against)
    candidates: dict[str, set[str]] = {}
    for path in args.files:
        if not os.path.isfile(path):
            continue
        fs, skip = scan_file(path)
        if skip:
            continue
        for f in fs:
            if f.rule in ("session-id", "session-id-prefix", "provider-id",
                          "incident-id", "capture-filename"):
                candidates.setdefault(f.value, set()).add(f.path)

    print(f"resolving {len(candidates)} candidates against {home} "
          f"(filesystem search, NOT a curated store list)\n")
    hits = 0
    for val in sorted(candidates):
        found = None
        for dirpath, dirnames, filenames in os.walk(home):
            # Only the places a session id is ever keyed, but discovered rather than listed.
            depth = dirpath[len(home):].count(os.sep)
            if depth > 8:
                dirnames[:] = []
                continue
            for n in filenames + dirnames:
                if val.lower() in n.lower():
                    found = os.path.join(dirpath, n)
                    break
            if found:
                break
        if found:
            hits += 1
            print(f"  REAL      {val}")
            print(f"            published in: {', '.join(sorted(candidates[val]))}")
            print(f"            resolves to:  {found}")
        else:
            print(f"  unresolved {val}")
    print(f"\n{hits} of {len(candidates)} candidates resolve to something real. "
          f"FIX THESE -- do not baseline them.")
    return 1 if hits else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", help="files to scan (typically $(git ls-files))")
    ap.add_argument("--baseline", help="JSON of accepted pre-existing occurrences")
    ap.add_argument("--write-baseline", action="store_true",
                    help="rewrite the baseline from what is found NOW (review the diff!)")
    ap.add_argument("--git-ls", action="store_true",
                    help="enumerate tracked files internally via `git ls-files -z` "
                         "instead of taking them as arguments (CI uses this)")
    ap.add_argument("--min-files", type=int, default=0,
                    help="fail if fewer than N files were scanned -- ENFORCED coverage, "
                         "because printed coverage is not read on a green run")
    ap.add_argument("--resolve-against", metavar="HOME",
                    help="DEVELOPER MODE, never in CI: resolve every candidate against a "
                         "real $HOME by filesystem search, and print only the ones that "
                         "hit. This is how the fix-now population is derived.")
    args = ap.parse_args(argv)

    if args.git_ls:
        # Taking `$(git ls-files)` on the command line word-splits on whitespace and lets
        # any path starting with `-` be eaten as a flag. Enumerate it ourselves.
        out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
        args.files = [p for p in out.stdout.decode().split("\0") if p]

    if args.resolve_against:
        return _resolve_mode(args)

    if not args.files:
        # Fail-safe: scanning nothing must never look like finding nothing.
        print("operator-identifier-scan: NO FILES GIVEN -- refusing to report success",
              file=sys.stderr)
        return 2

    baseline = load_baseline(args.baseline)
    findings: list[Finding] = []
    scanned = skipped = 0
    for path in args.files:
        if not os.path.isfile(path):
            continue
        fs, skip = scan_file(path)
        if skip:
            skipped += 1
            continue
        scanned += 1
        findings.extend(fs)

    if args.write_baseline:
        payload = {
            "_comment": "Accepted PRE-EXISTING operator-identifier occurrences. Keyed by "
                        "file+value, never line number. This file is a WORKLIST, not a "
                        "permission: burning it down is the follow-up. Nothing new may be "
                        "added without a reason.",
            "accepted": [{"key": f.key(), "rule": f.rule, "path": f.path,
                          "reason": "pre-existing at gate introduction"} for f in findings],
        }
        with open(args.baseline, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {len(findings)} accepted entries to {args.baseline}")
        return 0

    if scanned < args.min_files:
        print(f"operator-identifier-scan: scanned only {scanned} files, below the "
              f"committed floor of {args.min_files} -- refusing to report success. "
              f"This is coverage ENFORCED rather than printed: a gate that silently "
              f"scanned nothing is how #156's secret-scan stayed green.", file=sys.stderr)
        return 2

    self_accepted = _baseline_self_accepts(findings, args.baseline, baseline) \
        if args.baseline else set()
    new = [f for f in findings
           if f.key() not in baseline and f.key() not in self_accepted]
    # A baseline entry that no longer matches anything is dead weight that quietly
    # grows a permission list. Report it so the baseline stays a worklist that shrinks.
    live = {f.key() for f in findings}
    stale = sorted(k for k in baseline if k not in live)
    print(f"operator-identifier-scan: scanned {scanned} files, skipped {skipped} binary, "
          f"{len(baseline)} baselined, {len(findings)} total hits, {len(new)} NEW, "
          f"{len(stale)} stale baseline entries, "
          f"{len(_PRAGMA_HITS)} lines exempted by pragma")
    if stale:
        print("\nSTALE baseline entries (the occurrence is gone -- delete these lines):")
        for k in stale:
            print("  " + k)
    if new:
        print("\nNEW operator identifiers -- these must not reach a public repo:\n",
              file=sys.stderr)
        for f in sorted(new, key=lambda f: (f.path, f.line_no)):
            print("  " + f.render(), file=sys.stderr)
        print("\nFix the value (a visibly synthetic uuid, or a <placeholder>). Add to the "
              "baseline ONLY with a reason.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
