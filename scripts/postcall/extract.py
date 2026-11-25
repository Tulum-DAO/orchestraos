"""Extraction result + grounding. An item survives only if its quote is in the transcript
verbatim (after normalization); no fuzzy path (design §2, codex round 2)."""
import re
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^\w\s]")
_TS = re.compile(r"_\[(\d{1,3}):(\d{2})\]_")


@dataclass
class Extraction:
    ok: bool
    raw: dict | None
    error: str | None = None


@dataclass
class Item:
    owner: str
    quote: str
    span: tuple
    citation: str
    fields: dict = field(default_factory=dict)


def _normalized_with_map(text):
    """Lowercase, punctuation dropped, whitespace collapsed; plus each output char's source index."""
    out, idx, prev_space = [], [], True
    for i, ch in enumerate(text.lower()):
        if _PUNCT.match(ch):
            continue
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch)
        idx.append(i)
    if out and out[-1] == " ":
        out.pop(); idx.pop()
    return "".join(out), idx


def locate_quote(quote, transcript):
    """(start, end) of the quote in the original transcript, or None."""
    nq, _ = _normalized_with_map(quote or "")
    if not nq:
        return None
    nt, idx = _normalized_with_map(transcript)
    at = nt.find(nq)
    if at < 0:
        return None
    return idx[at], idx[at + len(nq) - 1] + 1


def _offset_label(transcript, pos):
    last = None
    for m in _TS.finditer(transcript, 0, pos):
        last = m
    return f"{last.group(1)}:{last.group(2)}" if last else "00:00"


def ground(call, extraction):
    items = []
    for raw in (extraction.raw or {}).get("items") or []:
        span = locate_quote(raw.get("quote"), call.transcript)
        if span is None:
            continue
        items.append(Item(owner=raw.get("owner") or "", quote=raw["quote"], span=span,
                          citation=f"{call.call_id}@{_offset_label(call.transcript, span[0])}",
                          fields=dict(raw)))
    return items
