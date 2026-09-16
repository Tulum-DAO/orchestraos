"""ANSI/VT escape stripping — provider-blind (a pty is a pty). Strips CSI/SGR
colour+cursor sequences and OSC strings so the token extractor emits clean text
deltas. Per-runtime CHROME classification (spinner/prompt glyphs) lives in the
signature layer (runtime_signatures / provider_profiles), NOT here — this is only
the universal byte-level strip."""
import re

# CSI ... final-byte (colours, cursor moves, erases); OSC ... BEL/ST; single
# ESC-char sequences. Deliberately broad and non-capturing.
_ANSI = re.compile(
    r"""
    \x1b \[ [0-?]* [ -/]* [@-~]      # CSI sequence
    | \x1b \] .*? (?: \x07 | \x1b\\) # OSC string (BEL or ST terminated)
    | \x1b [@-Z\\-_]                 # two-char ESC sequence
    """,
    re.VERBOSE | re.DOTALL,
)


def strip_ansi(text):
    if not isinstance(text, str):
        text = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else str(text)
    return _ANSI.sub("", text)
