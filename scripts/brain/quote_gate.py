"""Quote gate for the fact bus: a fact that attributes words, a preference or a decision to the operator is
stored as 'unverified' unless its own source shows the operator said exactly that.

LABEL ONLY. The gate never refuses, drops or deletes a fact; it only changes the state a NEW row is written with,
and records why in provenance.quote_gate. Recall serves an 'unverified' fact flagged "no source quote found",
ranked below sourced facts and never as anyone's words.
Off unless QUOTE_GATE_LABEL=1.

What verifies today, read-only at ingest time:
- dream CLI rows: the dream row (the day file line its source_id names) has the fact's text, says actor "shaw"
  and carries a citation {transcript, uuid, part}; that transcript record is a user record, and its text block
  contains the fact's text.
- voice rows: the conversation row the fact cites says the operator spoke, and its text is the fact's text.
Every other attributed fact (summaries, inferences, goals) has no quote to check, so it is labelled.

QUOTE_GATE_PROJECTS_DIR sets where transcripts are read (default ~/.claude/projects).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

OPERATOR = "shaw"
FLAG = "QUOTE_GATE_LABEL"

# "Shaw said / prefers / decided ..." in a fact's text. A fact whose actor is the operator is attributed by actor.
# Each verb as base | 3rd person | past or participle | -ing. A base form counts only after an auxiliary
# ("doesn't want", "would like"), so an instruction about the operator ("let Shaw decide") is not a claim.
_VERB_FORMS = (
    ("say", "says", "said", "saying"), ("state", "states", "stated", "stating"), ("tell", "tells", "told", "telling"),
    ("ask", "asks", "asked", "asking"), ("want", "wants", "wanted", "wanting"),
    ("prefer", "prefers", "preferred", "preferring"), ("decide", "decides", "decided", "deciding"),
    ("choose", "chooses", "chose|chosen", "choosing"), ("insist", "insists", "insisted", "insisting"),
    ("like", "likes", "liked", "liking"), ("dislike", "dislikes", "disliked", "disliking"),
    ("hate", "hates", "hated", "hating"), ("love", "loves", "loved", "loving"),
    ("approve", "approves", "approved", "approving"), ("reject", "rejects", "rejected", "rejecting"),
    ("agree", "agrees", "agreed", "agreeing"), ("request", "requests", "requested", "requesting"),
    ("instruct", "instructs", "instructed", "instructing"), ("direct", "directs", "directed", "directing"),
    ("rule", "", "ruled", "ruling"), ("confirm", "confirms", "confirmed", "confirming"),
    ("believe", "believes", "believed", "believing"), ("think", "thinks", "thought", "thinking"),
    ("feel", "feels", "felt", "feeling"), ("expect", "expects", "expected", "expecting"),
    ("mention", "mentions", "mentioned", "mentioning"), ("note", "", "noted", "noting"),
    ("explain", "explains", "explained", "explaining"), ("emphasize", "emphasizes", "emphasized", "emphasizing"),
    ("stress", "stresses", "stressed", "stressing"), ("plan", "plans", "planned", "planning"),
    ("intend", "intends", "intended", "intending"), ("commit", "commits", "committed", "committing"),
    ("promise", "promises", "promised", "promising"), ("need", "needs", "needed", "needing"),
    ("wish", "wishes", "wished", "wishing"), ("demand", "demands", "demanded", "demanding"),
)


def _alt(i):
    return "|".join(f[i] for f in _VERB_FORMS if f[i])


_ADV = (r"(?:(?:also|clearly|explicitly|strongly|really|always|never|just|still|now|actually|specifically|"
        r"repeatedly|definitely|often|usually|apparently|reportedly|personally|originally|then|not)\s+)*")
_AUX = r"(?:(?:do|does|did|would|will)(?:\s+not|n't|n’t)?|won't|won’t)"
_HAVE = r"(?:has|had|have)(?:\s+not|n't|n’t)?"
_BE = r"(?:is|was|are|has\s+been|had\s+been)(?:\s+not|n't|n’t)?"
_NOUNS = (r"preference|preferences|decision|decisions|request|requests|instruction|instructions|wish|wishes|view|"
          r"views|opinion|position|choice|stance|words|ask|requirement|requirements|demand")
_NAME = r"\bShaw(?:\s+Cole)?"
ATTRIBUTION_RE = re.compile(
    rf"{_NAME}(?:['’]s)?\s+{_ADV}(?:"
    rf"(?:{_alt(1)}|{_alt(2)}|{_alt(3)})"                     # Shaw says / said / saying
    rf"|{_AUX}\s+{_ADV}(?:{_alt(0)})"                        # Shaw doesn't want, would like
    rf"|{_HAVE}\s+{_ADV}(?:{_alt(2)})"                       # Shaw has told
    rf"|{_BE}\s+{_ADV}(?:{_alt(3)})"                         # Shaw is asking
    rf")\b"
    rf"|{_NAME}['’]s\s+(?:own\s+|stated\s+|standing\s+)?(?:{_NOUNS})\b"   # Shaw's preference is
    rf"|\b(?:according\s+to|per)\s+Shaw\b",                   # According to Shaw, ...
    re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get(FLAG, "") == "1"


def attributed(fact: dict) -> bool:
    text = unicodedata.normalize("NFKC", str(fact.get("text") or ""))
    return _lower(fact.get("actor")) == OPERATOR or bool(ATTRIBUTION_RE.search(text))


def _lower(s) -> str:
    return str(s or "").lower()


def _norm(s) -> str:
    return " ".join(str(s or "").split())


class _Sources:
    """Read-only, cached views of the files a citation points into."""

    def __init__(self, projects_dir: str):
        self.projects_dir = projects_dir
        self._jsonl = {}

    def rows(self, path: str, by_line: bool = False) -> list:
        """The parsed rows of a jsonl file (None for a line that does not parse), or None if it cannot be read.
        by_line splits the way the dream adapter numbers lines (str.splitlines), so row n-1 is line n."""
        if not isinstance(path, str) or not path:
            return None
        key = (path, by_line)
        if key not in self._jsonl:
            out = []
            try:
                with open(path, errors="replace") as fh:
                    for line in (fh.read().splitlines() if by_line else fh):
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            out.append(None)
            except (OSError, ValueError):             # ValueError: a NUL byte in the path
                out = None
            self._jsonl[key] = out
        return self._jsonl[key]

    def transcript_turn(self, citation: dict):
        """(record, text of its block `part`) for the record `uuid` in `transcript`. (None, None) if there is
        no such record; the text is None if the record has no such block."""
        rel = str(citation.get("transcript") or "")
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return None, None
        rows = self.rows(os.path.join(self.projects_dir, rel))
        for o in rows or []:
            if isinstance(o, dict) and o.get("uuid") == citation.get("uuid"):
                m = o.get("message")
                c = m.get("content") if isinstance(m, dict) else None
                blocks = [c] if isinstance(c, str) else [
                    b.get("text", "") for b in (c if isinstance(c, list) else [])
                    if isinstance(b, dict) and b.get("type") == "text"]
                part = citation.get("part")
                return o, (blocks[part] if isinstance(part, int) and 0 <= part < len(blocks) else None)
        return None, None


def _user_record(o: dict) -> bool:
    """A record the user typed: type "user", and no message role other than "user"."""
    m = o.get("message")
    return o.get("type") == "user" and (not isinstance(m, dict) or m.get("role", "user") == "user")


def _check_dream(fact: dict, src: _Sources):
    p = fact.get("provenance") or {}
    rows = src.rows(p.get("path"), by_line=True)
    if rows is None:
        return False, "dream day file not readable"
    # source_id is "<day file name>:<1-based line>", as the dream adapter writes it
    name, _, line = str(p.get("source_id") or "").rpartition(":")
    n = int(line) if re.fullmatch(r"[1-9][0-9]*", line) else 0
    row = rows[n - 1] if name == os.path.basename(p["path"]) and 0 < n <= len(rows) else None
    if not isinstance(row, dict):
        return False, "dream row not found"
    want = _norm(fact.get("text"))
    if _norm(row.get("text")) != want:
        return False, "dream row text differs from the fact"
    if _lower(row.get("actor")) != OPERATOR:
        return False, f"dream row author is {row.get('actor')!r}"
    cite = row.get("citation")
    if not isinstance(cite, dict) or not cite.get("uuid"):
        return False, "dream row carries no citation"
    record, block = src.transcript_turn(cite)
    if record is None:
        return False, "cited transcript turn not found"
    if not _user_record(record):
        return False, "cited transcript record is not the user's"
    if block is None:
        return False, "cited transcript turn not found"
    if want not in _norm(block):
        return False, "fact text not found in the cited turn"
    return True, "quote found in the operator's cited transcript turn"


def _check_voice(fact: dict, src: _Sources):
    p = fact.get("provenance") or {}
    for o in src.rows(p.get("path")) or []:
        if not isinstance(o, dict):
            continue
        ts = o.get("ts") or o.get("timestamp")
        if f"{o.get('channel')}:{ts}" != p.get("source_id"):
            continue
        who = o.get("role") or o.get("from")
        if who != OPERATOR:
            return False, f"conversation row speaker is {who!r}"
        text = o.get("content") if o.get("content") is not None else o.get("text")
        if _norm(text) != _norm(fact.get("text")):
            return False, "conversation row text differs from the fact"
        return True, "the operator's own conversation turn"
    return False, "conversation row not found"


_CHECKS = {"dream-facts": _check_dream, "voice": _check_voice}


def label(facts: list, projects_dir: str | None = None) -> list:
    """Copies of `facts` with each attributed, active fact labelled. Verified facts keep their state; the rest
    become 'unverified'. Both record provenance.quote_gate = {status, reason}. Other facts come back unchanged.
    Never raises on a fact's data: a check that fails labels that fact unverified, reason "gate error"."""
    src = _Sources(projects_dir or os.environ.get("QUOTE_GATE_PROJECTS_DIR")
                   or os.path.expanduser("~/.claude/projects"))
    out = []
    for f in facts:
        if f.get("state") != "active":
            out.append(f)
            continue
        try:                                     # one fact's bad source data never reaches ingest
            if not attributed(f):
                out.append(f)
                continue
            check = _CHECKS.get((f.get("provenance") or {}).get("source_kind"))
            ok, why = check(f, src) if check else (False, "no quote to check for this source")
        except Exception:  # noqa: BLE001
            ok, why = False, "gate error"
        g = dict(f)
        g["provenance"] = {**(f.get("provenance") or {}),
                           "quote_gate": {"status": "verified" if ok else "unverified", "reason": why}}
        if not ok:
            g["state"] = "unverified"
        out.append(g)
    return out
