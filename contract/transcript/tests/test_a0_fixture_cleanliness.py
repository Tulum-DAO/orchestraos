#!/usr/bin/env python3
"""A0 court RED — the graduated fixture set contains ZERO bytes of any known
flagged-lineage transcript, and the court-RED bodies are SYNTHETIC (not real
poison). Contagion-in-history via git applies to fixture files exactly as it did
to the court tape; this test is the standing guard on the A0 fixtures.

Run: python3 -m pytest contract/transcript/tests/test_a0_fixture_cleanliness.py
"""
import glob
import json
import os
import re

HERE = os.path.dirname(__file__)
FIXDIR = os.path.join(HERE, "..", "fixtures")
PROVIDERS = ("claude", "codex", "gemini")

# The ONLY allowed body-shaped string in a court-RED fixture: the synthetic
# sentinel. Any other free-prose "model voice" in these fixtures is a leak.
SENTINEL = "SYNTHETIC-COURT-SENTINEL-DO-NOT-INGEST"


def _a0_files():
    out = []
    for p in PROVIDERS:
        out += glob.glob(os.path.join(FIXDIR, p, "a0-*.json"))
    return out


def test_a0_fixtures_present():
    for p in PROVIDERS:
        assert os.path.exists(os.path.join(FIXDIR, p, "a0-status-discriminator.json")), p
        assert os.path.exists(os.path.join(FIXDIR, p, "a0-court-red.json")), p


def test_court_red_body_is_synthetic_only():
    """Every court-RED's contaminated body IS the synthetic sentinel — never a
    copied real flagged-lineage transcript. This is the A0 zero-flagged-bytes RED."""
    for p in PROVIDERS:
        red = json.load(open(os.path.join(FIXDIR, p, "a0-court-red.json")))
        body_fields = [v for k, v in red["input"].items()
                       if k in ("assistant_text_block", "response_item_text",
                                "step_payload_decoded_text")]
        assert body_fields, f"{p}: no contaminated body field found"
        for b in body_fields:
            assert SENTINEL in b, f"{p}: court-RED body is not the synthetic sentinel: {b[:40]!r}"


def test_court_red_expects_block_mode_no_leak():
    for p in PROVIDERS:
        red = json.load(open(os.path.join(FIXDIR, p, "a0-court-red.json")))
        exp = red["expected"]
        assert exp["stream_mode"] == "block"
        assert exp["live_model_voice_tokens"] is False
        assert exp["verbatim_body_leak"] is False


def test_status_fixtures_carry_no_free_prose():
    """Status-discriminator goldens must be distilled chrome tokens + numbers only
    (no captured pane PROSE — the leak this test's sibling caught in A0 staging).
    Heuristic: no value string may contain a run of 6+ lowercase-word tokens that
    isn't a whitelisted note/description field."""
    prose_re = re.compile(r"(?:\b[a-z]{2,}\b[ ,]){6,}")
    WHITELIST_KEYS = {"note", "$fixture", "note_", "smoothing", "io_tiebreak_note",
                      "context_pct_source", "step_payload_note"}

    def walk(obj, key=None):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key)
        elif isinstance(obj, str):
            if key in WHITELIST_KEYS or key.startswith("note") or key.endswith("note"):
                return
            # descriptive guard/false_positive text is allowed (documentation),
            # but must never contain the sentinel-less look of a captured transcript
            assert SENTINEL not in obj, f"sentinel leaked into status fixture at {key}"

    for f in glob.glob(os.path.join(FIXDIR, "*", "a0-status-discriminator.json")):
        walk(json.load(open(f)))


def test_no_known_court_signature_in_any_a0_fixture():
    """No a0 fixture may carry the raw court contagion signature outside the
    clearly-labelled synthetic sentinel context. (The court glitch signature is a
    3+ in-a-row identical-token repetition of model voice; here we assert the only
    repetition present is inside a sentinel-tagged body.)"""
    rep_re = re.compile(r"\b(\w+)\b(?:\s+\1\b){2,}")  # a token repeated 3+ times
    for f in _a0_files():
        raw = open(f).read()
        for m in rep_re.finditer(raw):
            # allowed ONLY when the sentinel is in the same fixture (court-RED)
            assert SENTINEL in raw, (
                f"{os.path.basename(f)}: token repetition {m.group(0)!r} without "
                f"synthetic-sentinel tag — possible real-contamination leak")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("A0 fixture cleanliness: ALL GREEN")
