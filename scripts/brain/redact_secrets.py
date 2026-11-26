"""Redact secrets-at-rest from the brain, and stop them coming back.

Written after a live bot token was found
sits in state/brain/facts.db because agents pasted infrastructure.md's `curl` line and the brain
drained it as facts. Measured read-only before any write: 34 rows of facts.text, 34 of
statements.content, and 47 rows of chunks.text in state/semantic-memory.db.

WHY THIS IS A MODULE AND NOT A ONE-LINE UPDATE. `statements` carries two UNCONDITIONAL
append-only triggers:

    CREATE TRIGGER statements_no_update BEFORE UPDATE ON statements
      BEGIN SELECT RAISE(ABORT, 'statements is append-only'); END
    CREATE TRIGGER statements_no_delete BEFORE DELETE ON statements ...

That is the L1 immutable statement ledger invariant the Universal Worldview architecture rests on,
and the drain spec's §3.3 watermark reasoning cites it by name ("the append-only triggers make it
immutable"). A plain UPDATE does not fail silently here — it ABORTS. So redacting these rows means
deliberately suspending the invariant, and the only responsible way to do that is: inside one
transaction, with the restore in a `finally`, and with a test that PROVES the triggers bite again
afterwards. `test_the_append_only_triggers_are_RESTORED_after_the_purge` and
`test_a_failure_mid_purge_leaves_the_triggers_in_place` are that proof.

WHAT DOES NOT BREAK, checked rather than assumed: `statement_id` is derived from
(wal_db, seq, integrity) — `wal_emit.py:140` — NOT from the content. So redacting content does not
orphan the id, and a later drain pass over the same WAL rows recomputes the SAME id and is
suppressed by `INSERT OR IGNORE`. The secret cannot walk back in through dedup.

WHAT THIS DOES NOT DO: it does not revoke the token (Shaw's card), and it does not touch the
source transcripts on disk, where the string also lives. Purging the brain is one layer.
"""
from __future__ import annotations

import os
import re
import sqlite3

__all__ = ["SHAPE", "PLACEHOLDER", "redact", "redact_for_output", "scrub_fact_text", "purge", "count"]

# A Telegram bot token: <numeric id>:AA<base64-ish>. Anchored on the literal "AA" and a real
# minimum length so ordinary "word: AA..." prose and "sha256:..." digests do not match.
SHAPE = re.compile(r"[0-9]{8,}:AA[A-Za-z0-9_-]{20,}")
PLACEHOLDER = "<TELEGRAM_TOKEN_REDACTED>"

# (table, id column, text column) — every place the brain stores free text.
TARGETS = (
    ("facts", "fact_id", "text"),
    ("statements", "statement_id", "content"),
    # state/semantic-memory.db — found by scanning every text column of every
    # table on the box. 47 chunks carried the same token. A table absent from a given DB is
    # skipped, so one TARGETS list serves both stores.
    ("chunks", "chunk_id", "text"),
)
# wide=True (one reviewed live write): CONTENT columns only, matched + rewritten with redact_for_output.
# Never facts.provenance / statements.source_ref: they carry mixed-case source_ids (freq_<Company>_...) that the
# display redactor flags but that are join keys.
WIDE_TARGETS = (
    ("facts", "fact_id", "text"),
    ("statements", "statement_id", "content"),
    ("beliefs", "belief_id", "subject"),
    ("beliefs", "belief_id", "current_value"),
    ("chunks", "chunk_id", "text"),
)
APPEND_ONLY = {
    "statements_no_update": ("CREATE TRIGGER statements_no_update BEFORE UPDATE ON statements"
                             " BEGIN SELECT RAISE(ABORT, 'statements is append-only'); END"),
    "statements_no_delete": ("CREATE TRIGGER statements_no_delete BEFORE DELETE ON statements"
                             " BEGIN SELECT RAISE(ABORT, 'statements is append-only'); END"),
}


def redact(text: str) -> str:
    """Replace every token-shaped run with the placeholder, leaving the sentence intact."""
    return SHAPE.sub(PLACEHOLDER, text or "")


# ---------------------------------------------------------------- output filter (read side)
# redact() above is the PURGE/ingest filter and stays Telegram-only: it rewrites the append-only ledger, so its
# net must not widen silently. redact_for_output is the READ-side filter every facts.db consumer applies before
# text reaches a seat (recall CLI, prompt hook): facts.db holds raw pasted pages (Cloudflare token screens, curl
# lines with Bearer headers), and nothing is lost by over-redacting a display string.
OUT_PLACEHOLDER = "<REDACTED>"
_OUT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"[0-9]{6,}:[A-Za-z0-9_-]{30,}"),                                    # Telegram-style (wider than SHAPE)
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),    # JWT
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),                                         # OpenAI / Anthropic
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),   # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                            # AWS access key id
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),                                 # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                                        # Google API key
    # <vendor>_live_/_test_ keys of any vendor and length (Stripe sk/rk/pk and others). Never anchored on a word
    # boundary: a \b before [spr]k_ never fires inside a longer prefix, and such keys once survived
    re.compile(r"[A-Za-z]{1,12}_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"whsec_[A-Za-z0-9]{16,}"),                                          # Stripe webhook secret
)
# a value right after a credential word: Bearer xyz / API_TOKEN=xyz / "token is xyz"
# The gap may not contain '<': it must never skip over a placeholder, or a second pass eats the next long run
# (found 10-09 purging semantic-memory chunks: redaction was not idempotent).
_OUT_AFTER_WORD = re.compile(
    r"(?i)(\b(?:bearer|token|secret|password|passwd|passphrase|api[_ -]?key|access[_ -]?key|private[_ -]?key|"
    r"client[_ -]?secret|[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)|[A-Z0-9_]*_KEY)\b[^\n<]{0,40}?[\s=:\"'])"
    r"([A-Za-z0-9._~+/=-][A-Za-z0-9_._~+/=-]{15,})")
# a standalone 32+ run mixing UPPER, lower and digits (CF/Notion/etc.); hex shas and uuids have no uppercase
# (not a path component after "/", not a file name before ".ext": DESIGN_SPEC_V2_Final.md is not a secret)
_OUT_MIXED = re.compile(r"(?<![A-Za-z0-9_/-])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[0-9])"
                        r"[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])(?!\.[A-Za-z]{1,5}\b)")


def _looks_secret(v: str) -> bool:
    has = (any(c.isdigit() for c in v), any(c.isalpha() for c in v))
    return all(has) and not v.startswith(("http", "/"))


def redact_for_output(text) -> str:
    """Redact credential-shaped substrings for DISPLAY. Never raises; None -> ""."""
    if not text:
        return ""
    t = redact(str(text))                       # the purge's own SHAPE first: this filter is a strict superset
    for pat in _OUT_PATTERNS:
        t = pat.sub(OUT_PLACEHOLDER, t)
    t = _OUT_AFTER_WORD.sub(lambda m: m.group(1) + (OUT_PLACEHOLDER if _looks_secret(m.group(2)) else m.group(2)), t)
    t = _OUT_MIXED.sub(OUT_PLACEHOLDER, t)
    return t


def scrub_fact_text(text: str) -> str:
    """The drain-time filter: call this on the way IN, so a token never becomes a fact.

    Deliberately the same function as the purge uses. A filter that redacts differently from the
    purge would leave two spellings of the same secret in one store. Since the 10-09 wide purge
    that function is redact_for_output, which applies redact() first.
    """
    return redact_for_output(text)


def _matcher(wide):
    return (lambda v: redact_for_output(v) != v) if wide else (lambda v: bool(SHAPE.search(v)))


def count(db_path: str, wide: bool = False) -> dict:
    """Read-only census of token-shaped strings, per table.column."""
    hit = _matcher(wide)
    out: dict[str, int] = {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, _id, col in (WIDE_TARGETS if wide else TARGETS):
            if table not in have:
                continue
            n = sum(1 for (v,) in con.execute(f'SELECT "{col}" FROM "{table}"')
                    if isinstance(v, str) and hit(v))
            if n:
                out[f"{table}.{col}"] = n
    finally:
        con.close()
    return out


def _redact_table(con: sqlite3.Connection, table: str, idcol: str, col: str, wide: bool = False) -> int:
    """Rewrite only the rows that actually match. Returns the number changed."""
    hit, fn = _matcher(wide), (redact_for_output if wide else redact)
    rows = [(r[0], r[1]) for r in con.execute(f'SELECT "{idcol}", "{col}" FROM "{table}"')
            if isinstance(r[1], str) and hit(r[1])]
    for rid, val in rows:
        con.execute(f'UPDATE "{table}" SET "{col}"=? WHERE "{idcol}"=?', (fn(val), rid))
    return len(rows)


def purge(db_path: str, apply: bool = False, wide: bool = False) -> dict:
    """Redact every token-shaped string in the brain's text columns.

    Dry run by DEFAULT: counts what it would change and writes nothing. `apply=True` performs the
    write in ONE transaction, suspending the statements append-only triggers only for its
    duration and restoring them in a `finally` so a crash cannot leave the ledger unguarded.

    Row ids, provenance and every other column are untouched — only the matched substring moves.
    """
    changed = count(db_path, wide=wide)
    if not apply:
        return changed

    con = sqlite3.connect(db_path)
    dropped: list[str] = []
    try:
        con.execute("BEGIN IMMEDIATE")
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for name in APPEND_ONLY:
            if name in have:
                con.execute(f"DROP TRIGGER {name}")
                dropped.append(name)
        result = {}
        for table, idcol, col in (WIDE_TARGETS if wide else TARGETS):
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in tables:
                continue
            n = _redact_table(con, table, idcol, col, wide=wide)
            if n:
                result[f"{table}.{col}"] = n
        for name in dropped:
            con.execute(APPEND_ONLY[name])
        dropped = []
        con.commit()
        return result
    finally:
        # The invariant comes back whether or not the purge did. A half-purged ledger is bad;
        # an UNGUARDED ledger is worse, because nothing downstream would ever notice.
        if dropped:
            con.rollback()
            for name in dropped:
                try:
                    con.execute(APPEND_ONLY[name])
                except sqlite3.Error:
                    pass
            con.commit()
        con.close()


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Redact secrets at rest in a brain DB")
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--wide", action="store_true", help="redact_for_output over WIDE_TARGETS (content columns)")
    a = ap.parse_args()
    print(json.dumps({"db": a.db, "applied": a.apply, "wide": a.wide, "rows": purge(a.db, apply=a.apply, wide=a.wide),
                      "remaining": count(a.db, wide=a.wide)}, indent=2))
