"""normalize-before-scrub — the provider-agnostic model-voice disposition seam
(Build A scope item 3, court-rider-3 / the COURT WRINKLE).

Two functions:

  normalize_body(runtime, raw) -> canonical text
     Turn a provider-raw body into canonical text. claude/codex bodies are
     already text (decode identity). gemini bodies are PROTOBUF-wire blobs, so
     this protobuf-decodes to the leaf string fields FIRST — because a
     claude-jsonl-tuned byte scrub can never see contamination re-serialized as
     protobuf. This is the load-bearing "decode-before-check" rule.

  render_body(runtime, raw, *, lineage_flagged, flag_readable=True) -> dict
     The disposition. ORDER IS LOAD-BEARING:
       1. NORMALIZE the raw body to canonical text (always first);
       2. fail-CLOSED: flag unreadable/absent-schema -> block, never stream;
       3. class defense: flagged lineage -> block (NO verbatim model voice),
          provider-independent by design;
       4. known-instance (layer 2): court_scrub the NORMALIZED text; a known
          court signature -> block;
       5. clean -> render the normalized text.
     Block mode returns text="" so ZERO verbatim model-voice bytes can leak.

This is the shared seam the durable lane's projection/hydrate consumes and that
Build B1's streaming path will reuse; Build A only needs the normalize + the
class-defense disposition (the A0 per-provider court REDs), NOT any streaming.
"""
from . import court_scrub as _court


def _to_bytes(raw):
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8", "replace")
    return b""


def _read_varint(buf, i):
    """Return (value, next_index) or (None, i) if truncated."""
    shift = 0
    result = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            return None, i
    return None, i


def _looks_like_text(chunk):
    try:
        s = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not s:
        return None
    printable = sum(1 for c in s if c.isprintable() or c in "\t\n\r ")
    # a leaf string is overwhelmingly printable; a packed/nested blob is not
    if printable / len(s) >= 0.85 and any(c.isalpha() for c in s):
        return s
    return None


def _pb_extract_strings(buf, depth=0):
    """Best-effort walk of a protobuf-wire blob, collecting leaf string fields
    (recursing into nested messages). Proprietary schema-free: we classify a
    length-delimited field as a string if it decodes as mostly-printable UTF-8,
    else we try to parse it as a nested message. Never surfaces non-text bytes."""
    out = []
    if depth > 12:
        return out
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        if tag is None:
            break
        wire = tag & 0x7
        if wire == 0:            # varint
            _, i = _read_varint(buf, i)
            if i > n:
                break
        elif wire == 2:          # length-delimited: string OR nested message
            ln, i = _read_varint(buf, i)
            if ln is None or i + ln > n:
                break
            chunk = buf[i:i + ln]
            i += ln
            s = _looks_like_text(chunk)
            if s is not None:
                out.append(s)
            else:
                out.extend(_pb_extract_strings(chunk, depth + 1))
        elif wire == 5:          # 32-bit
            i += 4
        elif wire == 1:          # 64-bit
            i += 8
        else:                    # groups / unknown -> stop (fail-safe)
            break
    return out


def normalize_body(runtime, raw):
    """Provider-raw body -> canonical text. gemini = protobuf-decode; others =
    decode-to-text identity. Always safe to call before any scrub/flag check."""
    if runtime == "gemini":
        strings = _pb_extract_strings(_to_bytes(raw))
        return "\n".join(strings)
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    return raw if isinstance(raw, str) else ""


def render_body(runtime, raw, *, lineage_flagged, flag_readable=True):
    """Disposition a body for any human-facing render. See module docstring for
    the load-bearing order. Returns:
      {stream_mode: 'block'|'clean', text: str, contaminated: bool,
       live_model_voice_tokens: bool, normalized_before_check: True}
    Block mode always returns text='' (zero verbatim leak)."""
    # 1. NORMALIZE FIRST — never scrub/flag-check provider-raw bytes.
    text = normalize_body(runtime, raw)

    def _block(contaminated=False):
        return {"stream_mode": "block", "text": "", "contaminated": contaminated,
                "live_model_voice_tokens": False, "normalized_before_check": True}

    # 2. fail-CLOSED on an unreadable/absent flag.
    if not flag_readable:
        return _block()
    # 3. class defense: flagged lineage -> no verbatim model voice, any provider.
    if lineage_flagged:
        return _block()
    # 4. known-instance (layer 2), on the NORMALIZED text.
    clean, contaminated = _court.court_scrub(text)
    if contaminated:
        return _block(contaminated=True)
    # 5. clean render.
    return {"stream_mode": "clean", "text": clean, "contaminated": False,
            "live_model_voice_tokens": True, "normalized_before_check": True}
