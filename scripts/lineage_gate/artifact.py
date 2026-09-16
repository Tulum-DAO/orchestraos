"""lineage_gate.artifact — parse a COMMITTED handoff into structured form.

Gate A reads the committed artifact's RAW BYTES (Amendment 7.3: any pre-grader
transform is itself a rubric modification under the ratchet). The parser is
Gate A's front door; agents author markdown, so markdown is what it parses.

Determinism (Amendment 1.4): no wall clock, no network, no randomness; every
traversal sorted; CRLF normalized before hashing; hashes over canonical bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


class UnparseableArtifact(Exception):
    """H4: an unparseable handoff cannot be graded — refuse, never guess."""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass
class Handoff:
    path: str
    raw_sha256: str
    sections: dict = field(default_factory=dict)   # lowercase title -> body text
    commits: list = field(default_factory=list)    # cited hex shas, sorted unique
    open_loop_refs: list = field(default_factory=list)  # msg_/task/DEC/path tokens
    hazards: list = field(default_factory=list)    # hazard item texts
    decisions: list = field(default_factory=list)  # decision-bearing lines


_SECTION_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.M)
# full-length hex only — mutable refs (HEAD, branch names) are rejected by
# omission: nothing shorter than 7 hex chars is even collected, and provenance
# gates require existence in git, which bare words won't satisfy.
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_REF_RE = re.compile(
    # (?<![~\w/]) — a repo-relative ref is not one when it is the tail of a
    # home-anchored path (`~/scripts/...` is a cwd mention, not a ground)
    r"(?<![~\w/])\b(?:msg_[0-9a-f]{8}_\d+|DEC-\d{10}|qnr_[0-9a-f]{8}(?:_\d+)?"
    r"|task\s*#\d+|(?:docs|state|scripts|prompts|logs)/[\w./\-]+)\b")


def parse_handoff(path: str | Path) -> Handoff:
    p = Path(path)
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8", errors="strict").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError) as e:
        raise UnparseableArtifact(f"{p}: {e}") from e
    if not text.strip():
        raise UnparseableArtifact(f"{p}: empty artifact")

    # split into sections by ##/### headings; preamble keyed ""
    titles = [(m.start(), m.group(1).strip().lower()) for m in _SECTION_RE.finditer(text)]
    sections: dict = {}
    bounds = titles + [(len(text), None)]
    if titles:
        sections[""] = text[: titles[0][0]]
        for (start, title), (nxt, _t) in zip(titles, bounds[1:]):
            body = text[start:nxt].split("\n", 1)
            sections[title] = body[1] if len(body) > 1 else ""
    else:
        sections[""] = text

    shas = sorted(set(_SHA_RE.findall(text)))
    # first_effect targets are TRANSITIONS — false-by-design at authoring
    # (D2), so they are structurally NOT open-loop grounds; harvesting one
    # into G-LOOPS makes the two gates contradictory (found by effect when
    # the real g11 declaration failed its own handoff).
    ref_text = "\n".join(l for l in text.split("\n")
                         if "first_effect:" not in l)
    refs = sorted(set(_REF_RE.findall(ref_text)))

    hazards = _items_under(sections, ("hazard",))
    decisions = _items_under(
        sections, ("current_goal", "world state", "lane state", "next_actions",
                   "guards", "proof"))
    return Handoff(path=str(p), raw_sha256=sha256_bytes(raw), sections=sections,
                   commits=shas, open_loop_refs=refs, hazards=hazards,
                   decisions=decisions)


def _items_under(sections: dict, title_stems: tuple) -> list:
    out = []
    for title in sorted(sections):
        if any(stem in title for stem in title_stems):
            for line in sections[title].split("\n"):
                line = line.strip()
                if re.match(r"^(?:[-*]|\d+\.)\s+\S", line):
                    out.append(line)
    return out


def parse_canary(path: str | Path) -> list:
    """Canary set: pointer-only rows {q, source_pointer}. Any other key on a
    question is H1 material for the rubric to judge — the parser reports
    faithfully, it does not enforce."""
    p = Path(path)
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise UnparseableArtifact(f"{p}: {e}") from e
    qs = data.get("questions")
    if not isinstance(qs, list):
        raise UnparseableArtifact(f"{p}: no questions[] array")
    return qs
