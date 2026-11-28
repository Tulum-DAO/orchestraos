"""Extraction result + grounding. An item survives only if its quote is in the transcript
verbatim (after normalization); no fuzzy path (design §2, codex round 2)."""
import re
import unicodedata
from dataclasses import dataclass, field

_PUNCT = re.compile(r"[^\w\s]")
_TS = re.compile(r"_\[(\d{1,3}):(\d{2})\]_")
_TURN = re.compile(r"^\*\*([^*\n]+)\*\*[ \t]+_\[\d{1,3}:\d{2}\]_[ \t]*$", re.M)
_STOP = frozenset("a an the to of for and or with you your me my i we our it this that by on in at "
                  "be will just soon later talk sounds good okay ok thanks thank".split())
MIN_CONTENT_WORDS = 3


class AttributionError(Exception):
    """The extractor's claim about which diarization label is Shaw is not backed by the transcript."""


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
    speaker: str = ""
    trust: str = "inferred"
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


def turns(transcript):
    """[(speaker_label, body_start, body_end)] for each `**LABEL** _[MM:SS]_` turn."""
    heads = list(_TURN.finditer(transcript))
    out = []
    for n, m in enumerate(heads):
        end = heads[n + 1].start() if n + 1 < len(heads) else len(transcript)
        out.append((m.group(1).strip(), m.end(), end))
    return out


def _speaker_of(span, call_turns):
    for label, start, end in call_turns:
        if start <= span[0] and span[1] <= end:
            return label
    return None


def _substantive(text):
    words = [w for w in _normalized_with_map(text or "")[0].split() if w not in _STOP]
    return len(words) >= MIN_CONTENT_WORDS


def norm_name(name):
    """The one form every speaker-name comparison uses: NFC, whitespace collapsed, casefolded."""
    return unicodedata.normalize("NFC", " ".join(name.split())).casefold() if isinstance(name, str) else ""


def _shaw_names():
    """Shaw's own name(s) as a recorder labels him (POSTCALL_SHAW_NAMES, comma list; set by the sweep's
    environment, never written in code)."""
    import os
    return {norm_name(n) for n in (os.environ.get("POSTCALL_SHAW_NAMES") or "").split(",") if n.strip()}


def shaw_voices(shaw_label, labels):
    """The labels that are Shaw's voice. Labels carrying a configured form of his name
    (POSTCALL_SHAW_NAMES) are all his, since Pocket can split him in two ("Shaw Cole" + "Shaw"; Shaw
    2026-11-28), and the claim must be one of them, or none count. With no such label, only the
    claimed label (whose proof ground() checks). Empty when the claim is not a transcript label."""
    names = _shaw_names()
    named = {lab for lab in labels if norm_name(lab) in names}
    if named:
        return named if shaw_label in named else set()
    return {shaw_label} if shaw_label in labels else set()


def _named_shaw_label(label, call_turns):
    """A claimed label that IS one of Shaw's configured names proves itself."""
    return bool(label) and norm_name(label) in _shaw_names() and any(lab == label for lab, _, _ in call_turns)


def ground(call, extraction):
    """Grounded items. A diarized call binds each quote to one speaker turn (inferred trust); a call
    that is not diarized (or has no turns) keeps the item with speaker "" and trust "unknown", the
    owner as the extractor gave it (design §2), and needs no Shaw-label evidence."""
    raw_all = extraction.raw or {}
    call_turns = turns(call.transcript)
    diarized = bool(getattr(call, "diarized", True)) and bool(call_turns)
    shaw_label = raw_all.get("shaw_label")
    names = _shaw_names()
    if diarized and raw_all.get("items") and not _named_shaw_label(shaw_label, call_turns) and any(
            norm_name(lab) in names for lab, _, _ in call_turns):
        raise AttributionError(f"{call.call_id}: a label names Shaw but shaw_label is {shaw_label!r}")
    voices = shaw_voices(shaw_label, [lab for lab, _, _ in call_turns])
    if diarized and raw_all.get("items") and not _named_shaw_label(shaw_label, call_turns):
        evidence = locate_quote(raw_all.get("shaw_label_evidence"), call.transcript)
        if not shaw_label or evidence is None or _speaker_of(evidence, call_turns) != shaw_label:
            raise AttributionError(f"{call.call_id}: shaw_label {shaw_label!r} not evidenced in that speaker's turn")
    items = []
    for raw in raw_all.get("items") or []:
        span = locate_quote(raw.get("quote"), call.transcript)
        if span is None or not _substantive(raw.get("commitment_text")):
            continue
        owner = raw.get("owner") or ""
        if owner not in ("shaw", "participant"):
            continue
        if diarized:
            speaker = _speaker_of(span, call_turns)
            if speaker is None or (owner == "shaw") != (speaker in voices):
                continue
            trust = "inferred"
        else:
            speaker, trust = "", "unknown"
        items.append(Item(owner=owner, quote=raw["quote"], span=span,
                          citation=f"{call.call_id}@{_offset_label(call.transcript, span[0])}",
                          speaker=speaker, trust=trust, fields=dict(raw)))
    return items
