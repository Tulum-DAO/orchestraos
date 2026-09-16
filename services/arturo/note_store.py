# note_store.py — VQ-10: supersede/conflict resolution for remember_note.
#
# the operator's concern: successive remember_note saves pile up and CONTRADICT — a note asserting a
# since-fixed bug lingers next to the newer "it's fixed" note, because the old dedup only dropped
# EXACT-text duplicates. Real auto-memory updates the prior fact instead of appending a conflicting
# twin. This module supersedes prior notes ABOUT THE SAME SUBJECT (high token overlap) with the new
# one, so recency genuinely wins on a topic rather than merely being appended after the stale claim.
import re

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "and", "or", "but",
    "in", "on", "at", "for", "with", "that", "this", "it", "its", "i", "you", "we", "he", "she",
    "they", "not", "no", "now", "has", "have", "had", "do", "does", "did", "so", "as", "by", "from",
    "about", "into", "out", "up", "down", "then", "than", "just", "still", "again", "his", "her",
}


def _keywords(text):
    t = (text if isinstance(text, str) else str(text)).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return {w for w in t.split() if w not in _STOP and len(w) > 2}


def same_subject(a, b, thresh=0.5, min_shared=2):
    """True if two notes are about the SAME subject. Uses the OVERLAP COEFFICIENT
    (|shared| / min(|ka|,|kb|)), NOT Jaccard: a state-change note deliberately swaps its predicate
    ('broken'→'fixed'), which would tank Jaccard on the very contradiction we want to catch. The
    overlap coefficient keys on the shared SUBJECT tokens instead. A >=min_shared floor prevents a
    single common word (e.g. a project name) from wrongly merging unrelated notes."""
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb:
        return False
    inter = len(ka & kb)
    if inter < min_shared:
        return False
    denom = min(len(ka), len(kb)) or 1
    return (inter / denom) >= thresh


def supersede_notes(existing, new_entry, cap=50):
    """Return the updated notes list: drop any prior note that (a) is byte-identical (case-insens)
    OR (b) is about the SAME subject as new_entry (recency wins on a topic — the contradicting stale
    twin is replaced, not kept), then append new_entry and cap length. `existing`: list[dict] with
    a 'note' field. Preserves order of the survivors."""
    new_text = new_entry.get("note", "")
    new_low = new_text.lower()
    survivors = []
    superseded = 0
    for n in existing:
        txt = n.get("note", "")
        if txt.lower() == new_low or same_subject(txt, new_text):
            superseded += 1
            continue
        survivors.append(n)
    survivors.append(new_entry)
    return survivors[-cap:], superseded
