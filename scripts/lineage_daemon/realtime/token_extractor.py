"""B1(b) token extractor — ANSI-strip pty deltas to iOS, COURT-SCRUB GATED.

Two paths, one gate:

  stream_pty_delta(runtime, raw_pty, *, lineage_flagged, flag_readable)
     The LIVE low-latency path: ANSI-strip and stream immediately. Bound by the
     EMISSION-TIME PIN (ob, DEC-1788456089 fold): the raw pty stream is ONE
     unlabeled interleaved flow — model-voice and tool-output are
     indistinguishable until the block boundary lands LATER. Therefore a FLAGGED
     lineage = FULL block-mode: NO pty streaming at all, not even
     "probably-tool-output". The world-output streaming allowance is NOT
     exercisable here (no RED-proven emission-time discriminator exists), so
     flagged (or fail-closed) => text="" and streamed=False.

  reconcile_clean_block(runtime, raw_body, *, lineage_flagged, flag_readable)
     The replace-with-clean path (through Build C's wal->TranscriptEnvelope
     projection): reuses Build A's normalize.render_body — normalize-before-scrub
     (gemini protobuf-decode FIRST), class defense (flagged => block), then the
     known-instance court_scrub, fail-closed. Never scrubs provider-raw bytes.

  disposition_for_stream(runtime, raw, lineage_root, flag_store)
     Convenience: read the durable per-lineage flag (fail-closed) then dispose
     the live pty path. A missing/corrupt flag store blocks EVERYTHING.

Block mode ALWAYS returns text="" so ZERO verbatim model-voice bytes can leak.
"""
from ..wal import normalize as _normalize
from .ansi import strip_ansi


def _block(reason):
    return {"stream_mode": "block", "text": "", "streamed": False,
            "live_model_voice_tokens": False, "reason": reason}


def stream_pty_delta(runtime, raw_pty, *, lineage_flagged, flag_readable=True):
    """The live pty path. Emission-time PIN: flagged/fail-closed => full block."""
    if not flag_readable:
        return _block("fail-closed")               # rider 2: unreadable flag => block
    if lineage_flagged:
        return _block("flagged-emission-pin")      # PIN: unlabeled bytes = model-voice-until-proven
    text = strip_ansi(raw_pty)
    return {"stream_mode": "clean", "text": text, "streamed": True,
            "live_model_voice_tokens": True, "reason": "clean"}


def reconcile_clean_block(runtime, raw_body, *, lineage_flagged, flag_readable=True):
    """The replace-with-clean path — normalize-before-scrub via Build A's
    disposition. Returns the render_body dict (adds streamed=False since this is
    the durable reconcile, not the live token stream)."""
    d = _normalize.render_body(runtime, raw_body, lineage_flagged=lineage_flagged,
                               flag_readable=flag_readable)
    d["streamed"] = False
    return d


def disposition_for_stream(runtime, raw, *, lineage_root, flag_store):
    """Read the durable flag (fail-closed) and dispose the LIVE pty path."""
    flagged, readable = flag_store.status(lineage_root)
    return stream_pty_delta(runtime, raw, lineage_flagged=flagged,
                            flag_readable=readable)
