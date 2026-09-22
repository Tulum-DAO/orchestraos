"""RED-first for `orchestra pair` — the terminal half of onboarding."""
import json

from orchestra_cli.pair_cmd import build_pair_output, qr_text_or_none


def test_output_carries_the_raw_string_beneath_whatever_is_drawn():
    """The contract: a QR plus THE RAW STRING beneath it. The raw string is what makes this
    work when the QR cannot be drawn, or photographed, or scanned."""
    payload = json.dumps({"code": "abc123", "base_url": "https://box:8443"})
    out = build_pair_output(payload)
    assert payload in out


def test_output_warns_in_PLAIN_WORDS_that_this_is_a_password():
    """Operator-facing text, not jargon: someone screensharing a terminal must understand
    what they are showing before they show it."""
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}))
    low = out.lower()
    assert "password" in low
    assert "screenshare" in low or "screen share" in low or "share your screen" in low


def test_output_says_how_long_it_lasts():
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}), ttl_s=600)
    assert "10 minutes" in out or "10 min" in out


def test_qr_is_absent_rather_than_faked_when_no_encoder_exists():
    """If nothing on the box can draw a QR we print no QR — we do NOT draw ASCII art that
    is not a scannable code. A fake QR is worse than none: it fails at the moment someone
    points a phone at it, in front of a room."""
    assert qr_text_or_none("payload", encoder=None) is None


def test_qr_is_drawn_when_an_encoder_IS_available():
    class FakeEncoder:
        def render(self, payload):
            return "##QR##"
    assert qr_text_or_none("payload", encoder=FakeEncoder()) == "##QR##"


def test_a_missing_qr_still_produces_usable_output():
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}), qr=None)
    assert "c" in out and "password" in out.lower()
    # and it tells the operator what to do instead of showing a QR
    assert "type" in out.lower() or "paste" in out.lower() or "enter" in out.lower()
