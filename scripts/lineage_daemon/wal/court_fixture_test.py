"""THE CENTERPIECE PROOF (spec §3.3 risk-0) — REAL court-fixture sanitization.

This is the safety-critical assertion gm re-runs BY EFFECT at review: a WAL
seeded with a REAL captured court-corruption sample must produce a digest with
ZERO occurrences of the corruption signature, and model-voice must never appear
verbatim.

INGEST HYGIENE: the fixture is handled as OPAQUE BYTES — this test reads the
file and greps the digest output for the signature; the builder never reads,
quotes, or reproduces the poison. The signature is derived from the fixture
itself (or a companion fixtures/court_signature.txt gm provides).

STATE: skip-if-absent until gm provides fixtures/court_sample.jsonl (the
acme-merge raw_output_tail). When present, this test goes RED and DRIVES the
concrete `court_scrub` detector build (RED-first) — a skipped test is not a
proof; gm will re-run this with the real sample.
"""
import os

import pytest

_FIX_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_SAMPLE = os.path.join(_FIX_DIR, "court_sample.jsonl")
_SIGFILE = os.path.join(_FIX_DIR, "court_signature.txt")


def _load_signature():
    """The signature = an explicit marker file if gm provides one, else derived
    from the sample by the (not-yet-built) court detector. We never inline it."""
    if os.path.exists(_SIGFILE):
        return open(_SIGFILE, "rb").read().strip()
    return None


@pytest.mark.skipif(not os.path.exists(_SAMPLE),
                    reason="awaiting gm's REAL court sample at fixtures/court_sample.jsonl")
def test_real_court_fixture_yields_zero_signature_digest(tmp_path):
    from lineage_daemon.wal.store import WalStore
    from lineage_daemon.wal.digest import render_digest
    # court_scrub must exist once the real sample is here — RED until built.
    from lineage_daemon.wal.court_scrub import court_scrub

    raw = open(_SAMPLE, "rb").read()
    sig = _load_signature()

    # gm's canonical RED baseline (count only, never reading content): the INPUT
    # is genuinely poisoned — the documented "3-in-a-row" court signature.
    assert raw.lower().count(b"court") == 3

    # Seed a WAL where the poison lives in WORLD-OUTPUT (tool_result) and in
    # MODEL-VOICE (response) bodies — the two vectors §3.3 must both defend.
    store = WalStore(str(tmp_path / "court.db"))
    store.append(ts=0.0, lineage_root="ios-watch-dev", generation=6, sid="s",
                 runtime="claude", kind="response", summary="text (poison)",
                 body_ref="court:0", source_path="p", source_off=0, integrity="h")
    store.append(ts=0.0, lineage_root="ios-watch-dev", generation=6, sid="s",
                 runtime="claude", kind="tool_result", summary="result (poison)",
                 body_ref="court:1", source_path="p", source_off=0, integrity="h")

    poison = raw.decode("utf-8", "replace")
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: poison, scrub=court_scrub)

    # (1) ZERO occurrences of the corruption signature in the digest — proven by
    # re-running the same detector over the OUTPUT: it must find nothing.
    assert court_scrub(d["text"])[1] is False
    # gm's canonical GREEN: the OUTPUT has ZERO court-token occurrences
    # (input had 3) — the poison did not survive into the digest.
    assert d["text"].lower().count("court") == 0
    if sig is not None:
        assert sig.decode("utf-8", "replace") not in d["text"]
    # (2) model-voice body (the response poison) NEVER verbatim regardless.
    assert poison not in d["text"]
    # (3) the contaminated tool_result (world-output) span is flagged + excluded.
    assert d["contaminated"] is True


@pytest.mark.skipif(not os.path.exists(_SAMPLE),
                    reason="awaiting gm's REAL court sample at fixtures/court_sample.jsonl")
def test_class_defense_blocks_model_voice_without_any_scrub(tmp_path):
    """Layer-1 (class defense) is signature-INDEPENDENT: a poisoned MODEL-VOICE
    body never reaches the digest even with NO scrub, because response/thinking
    bodies are never resolved. Proven on the REAL court sample."""
    from lineage_daemon.wal.store import WalStore
    from lineage_daemon.wal.digest import render_digest

    poison = open(_SAMPLE, "rb").read().decode("utf-8", "replace")
    store = WalStore(str(tmp_path / "court.db"))
    store.append(ts=0.0, lineage_root="ios-watch-dev", generation=6, sid="s",
                 runtime="claude", kind="response", summary="text (poison)",
                 body_ref="court:0", source_path="p", source_off=0, integrity="h")
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: poison)  # DEFAULT no-op scrub
    from lineage_daemon.wal.court_scrub import court_scrub
    assert poison not in d["text"]
    assert court_scrub(d["text"])[1] is False   # zero signature, no scrub needed
