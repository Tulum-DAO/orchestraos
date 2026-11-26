"""Quote gate for the fact bus: a fact that attributes words, a preference or a decision to the operator is
stored as 'unverified' unless its own source shows the operator said exactly that.

LABEL ONLY. The gate never refuses, drops or deletes a fact; it only changes the state a NEW row is written with,
and records why in provenance.quote_gate. Recall never serves 'unverified' (facts_schema.UNSERVED_STATES).
Off unless QUOTE_GATE_LABEL=1.

What verifies today, read-only at ingest time:
- dream CLI rows: the dream row (found in its day file by text) says actor "shaw" and carries a citation
  {transcript, uuid, part}; that transcript record exists, and its text block contains the fact's text.
- voice rows: the conversation row the fact cites says the operator spoke, and its text is the fact's text.
Every other attributed fact (summaries, inferences, goals) has no quote to check, so it is labelled.

QUOTE_GATE_PROJECTS_DIR sets where transcripts are read (default ~/.claude/projects).
"""
from __future__ import annotations

import json
import os
import re

OPERATOR = "shaw"
FLAG = "QUOTE_GATE_LABEL"

# "Shaw said / prefers / decided ..." in a fact's text. A fact whose actor is the operator is attributed by actor.
_VERBS = (r"said|says|stated|told|tells|asked|asks|wants|wanted|prefers|preferred|decided|decides|chose|chooses|"
          r"insists|insisted|likes|liked|dislikes|hates|loves|approved|approves|rejected|rejects|agreed|agrees|"
          r"requested|requests|instructed|directed|ruled|confirmed|confirms|believes|thinks|feels|expects|"
          r"mentioned|noted|explained|emphasized|stressed|plans|planned|intends|committed|promised")
ATTRIBUTION_RE = re.compile(r"\bShaw(?:\s+Cole)?(?:'s)?\s+(?:also\s+|clearly\s+|explicitly\s+|has\s+|had\s+|"
                            r"would\s+|does\s+not\s+|doesn't\s+|strongly\s+)?(?:" + _VERBS + r")\b", re.IGNORECASE)


def enabled() -> bool:
    return os.environ.get(FLAG, "") == "1"


def attributed(fact: dict) -> bool:
    return (fact.get("actor") or "").lower() == OPERATOR or bool(ATTRIBUTION_RE.search(fact.get("text") or ""))


def _norm(s) -> str:
    return " ".join(str(s or "").split())


class _Sources:
    """Read-only, cached views of the files a citation points into."""

    def __init__(self, projects_dir: str):
        self.projects_dir = projects_dir
        self._jsonl = {}

    def rows(self, path: str) -> list:
        if path not in self._jsonl:
            out = []
            try:
                with open(path, errors="replace") as fh:
                    for line in fh:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            out.append(None)
            except OSError:
                out = None
            self._jsonl[path] = out
        return self._jsonl[path]

    def transcript_block(self, citation: dict):
        """The text of block `part` of the record `uuid` in `transcript`, or None."""
        rel = str(citation.get("transcript") or "")
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            return None
        rows = self.rows(os.path.join(self.projects_dir, rel))
        for o in rows or []:
            if isinstance(o, dict) and o.get("uuid") == citation.get("uuid"):
                c = (o.get("message") or {}).get("content")
                blocks = [c] if isinstance(c, str) else [
                    b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text"]
                part = citation.get("part")
                return blocks[part] if isinstance(part, int) and 0 <= part < len(blocks) else None
        return None


def _check_dream(fact: dict, src: _Sources):
    p = fact.get("provenance") or {}
    rows = src.rows(p.get("path") or "")
    if rows is None:
        return False, "dream day file not readable"
    want = _norm(fact.get("text"))
    row = next((r for r in rows if isinstance(r, dict) and _norm(r.get("text")) == want), None)
    if row is None:
        return False, "dream row not found"
    if (row.get("actor") or "").lower() != OPERATOR:
        return False, f"dream row author is {row.get('actor')!r}"
    cite = row.get("citation")
    if not isinstance(cite, dict) or not cite.get("uuid"):
        return False, "dream row carries no citation"
    block = src.transcript_block(cite)
    if block is None:
        return False, "cited transcript turn not found"
    if want not in _norm(block):
        return False, "fact text not found in the cited turn"
    return True, "quote found in the operator's cited transcript turn"


def _check_voice(fact: dict, src: _Sources):
    p = fact.get("provenance") or {}
    for o in src.rows(p.get("path") or "") or []:
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
    become 'unverified'. Both record provenance.quote_gate = {status, reason}. Other facts come back unchanged."""
    src = _Sources(projects_dir or os.environ.get("QUOTE_GATE_PROJECTS_DIR")
                   or os.path.expanduser("~/.claude/projects"))
    out = []
    for f in facts:
        if f.get("state") != "active" or not attributed(f):
            out.append(f)
            continue
        check = _CHECKS.get((f.get("provenance") or {}).get("source_kind"))
        ok, why = check(f, src) if check else (False, "no quote to check for this source")
        g = dict(f)
        g["provenance"] = {**(f.get("provenance") or {}),
                           "quote_gate": {"status": "verified" if ok else "unverified", "reason": why}}
        if not ok:
            g["state"] = "unverified"
        out.append(g)
    return out
