"""court_scrub — the KNOWN-INSTANCE sanitization detector (spec §3.3(a)).

Two-layer defense, this is layer 2:
  * LAYER 1 (class defense, in digest.py, signature-INDEPENDENT): model-voice
    bodies (assistant response/thinking, tool_call args) are NEVER resolved into
    the digest. This alone blocks the court vector for model-voice content.
  * LAYER 2 (this module, KNOWN-INSTANCE): for WORLD-OUTPUT (tool_result/
    file_mod) bodies that ARE drilled, detect the KNOWN court-corruption
    signature and hard-exclude the span (contaminated=true). Residual risk
    (novel signatures) is accepted per spec — layer 1 is the class defense,
    this is only the known-instance defense.

INGEST HYGIENE: the known court sample is loaded PROGRAMMATICALLY by path and
sliced into detection anchors held in memory. Its content is NEVER read, printed,
quoted, or logged — detection matches anchors against candidate text and returns
only a boolean. (Contract with fixtures/DO_NOT_READ.md.)
"""
import os

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "court_sample.jsonl")
_ANCHOR_WIN = 48   # chars per anchor
_ANCHOR_N = 6      # distinct anchors sampled across the known instance


def _load_anchors(path=_FIXTURE, n=_ANCHOR_N, win=_ANCHOR_WIN):
    """Derive detection anchors from the known court sample WITHOUT surfacing
    its content. Deterministic, evenly-spaced windows with non-trivial content.
    Returns [] when no known instance is present (layer 1 still holds)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return []
    if len(text) <= win:
        return [text]
    anchors = []
    step = max(1, (len(text) - win) // (n + 1))
    off = step
    while off + win <= len(text) and len(anchors) < n:
        w = text[off:off + win]
        if len(w.split()) >= 2 and w not in anchors:  # skip whitespace-only
            anchors.append(w)
        off += step
    return anchors


_ANCHORS = _load_anchors()


def court_scrub(text):
    """(clean, contaminated). contaminated=True when the KNOWN court signature is
    present -> the caller hard-excludes the span. Clean text passes untouched."""
    if text is None:
        return "", False
    for anchor in _ANCHORS:
        if anchor in text:
            return "", True
    return text, False


def has_known_instance():
    return bool(_ANCHORS)
