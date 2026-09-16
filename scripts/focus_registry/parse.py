"""Parse the fleet-audit markdown focus-cluster tables into focus entities (WS1).

The parser is the idempotent importer core: re-reading a refreshed audit
re-derives the focus records. Pure (text in, dataclasses out) so it is trivially
testable and carries no IO.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# Annotations the audit uses to mark a live/kept agent (vs a cull/superseded one).
_LIVE_FLAGS = ("KEEP", "ACTIVE", "UNCERTAIN")
# Explicit owner hint inside an agent annotation, e.g. "(KEEP, live owner)".
_OWNER_HINT = "owner"

_CAT_RE = re.compile(r"^###\s+(.+?)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


@dataclass
class Focus:
    canonical: str
    category: str
    pct_done: Optional[int]
    relevance: Optional[str]
    importance: Optional[str]
    owner: Optional[str]  # entity id "agent:<name>" of the live owner, or None
    agents: List[str] = field(default_factory=list)  # all member agent entity ids
    notes: str = ""


def _split_row(line: str) -> Optional[List[str]]:
    """Return the cell list for a markdown table row, or None if not a data row."""
    s = line.strip()
    if not s.startswith("|"):
        return None
    # Drop the leading/trailing pipe then split.
    cells = [c.strip() for c in s.strip("|").split("|")]
    return cells


def _is_separator(cells: List[str]) -> bool:
    return all(set(c) <= set("-: ") and c for c in cells)


def _parse_pct(cell: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*%", cell)
    return int(m.group(1)) if m else None


def _split_top_level(cell: str) -> List[str]:
    """Split on commas that are NOT inside parentheses.

    Agent annotations contain commas ('(KEEP, live owner)', '(cull, DONE)') — a
    naive comma split keeps the status tail as a garbage agent id ('agent:DONE)').
    """
    parts: List[str] = []
    depth = 0
    cur = []
    for ch in cell:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _parse_agents(cell: str):
    """Return (all_agent_ids, owner_id_or_None) from the Agents cell."""
    all_ids: List[str] = []
    keep_ids: List[str] = []
    owner_id: Optional[str] = None
    # Agents are comma-separated; each may be **name (ANNOTATION)** or name (annotation).
    for chunk in _split_top_level(cell):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Strip bold markers to read the raw text, but keep the annotation.
        raw = chunk.replace("**", "").strip()
        # Name = the leading whitespace-delimited token of the pre-"(" text.
        # Real agent ids never contain spaces, so a free-text note after the id
        # ('client-page-access Stage-2 overlap') is discarded to its first token.
        pre_paren = raw.split("(", 1)[0]
        tokens = pre_paren.split()
        name = tokens[0] if tokens else ""
        if not name:
            continue
        entity_id = f"agent:{name}"
        all_ids.append(entity_id)
        annotation = raw[len(name):]  # includes the "(...)" if present
        upper = annotation.upper()
        is_live = any(flag in upper for flag in _LIVE_FLAGS)
        if is_live:
            keep_ids.append(entity_id)
            if _OWNER_HINT in annotation.lower() and owner_id is None:
                owner_id = entity_id
    if owner_id is None and keep_ids:
        owner_id = keep_ids[0]
    return all_ids, owner_id


def parse_audit(md_text: str) -> List[Focus]:
    focuses: List[Focus] = []
    category = ""
    for line in md_text.splitlines():
        cat_m = _CAT_RE.match(line.strip())
        if cat_m:
            category = cat_m.group(1).strip()
            continue
        cells = _split_row(line)
        if not cells or len(cells) < 6:
            continue
        if _is_separator(cells):
            continue
        # Skip the header row.
        if cells[0].strip().lower() == "focus":
            continue
        canonical = _BOLD_RE.sub(r"\1", cells[0]).strip()
        if not canonical:
            continue
        agents, owner = _parse_agents(cells[1])
        focuses.append(
            Focus(
                canonical=canonical,
                category=category,
                pct_done=_parse_pct(cells[2]),
                relevance=cells[3].strip() or None,
                importance=cells[4].strip() or None,
                owner=owner,
                agents=agents,
                notes=cells[5].strip(),
            )
        )
    return focuses
