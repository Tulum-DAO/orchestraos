"""B1 content-novelty discriminator — the bytes-axis root fix (DEC-1788493111).

The old bytes axis (telemetryd._fast) upgraded a seat to `streaming` on ANY
non-empty pipe-pane burst. Terminal chrome — SIGWINCH redraws, ANSI repaints,
spinner/elapsed-counter ticks — emits bytes with ZERO model activity, so one
fleet-wide redraw made every attached seat read `streaming` for >=5s (the
observed false-active burst). The fix changes WHAT is measured: **novel content**,
not bytes. Model generation APPENDS text that was not on screen before; a redraw
RE-EMITS text that was.

`classify_burst(raw, tail, *, profile) -> (verdict, new_tail)` where verdict is
one of {"novel_content", "repaint", "chrome"}. Only "novel_content" should
upgrade a seat to streaming (telemetryd sets bytes_flowing from it). PURE +
fixture-testable; no IO.

PINS (spec §9.1, both congruence legs):
 1. Chrome-region exclusion FIRST: strip ANSI, then the per-provider *dynamic
    chrome text* (spinner glyphs, elapsed counters "for 43s", clock digits,
    context-% footer, block-bars). This closes the ticking-chrome hole — an
    idle pane whose only motion is an elapsed counter must NOT read streaming.
 2. Novel appended content = streaming, even in re-emission-dominated bursts
    (claude's TUI repaints the lower region per generated token): a suffix→prefix
    content-append test finds the new token beyond the known tail and wins —
    ESC/erase domination NEVER vetoes genuinely-new text.
 3. Repetitive-output guard (agy): a model emitting `|---|---|` or `\n\n\n`
    APPENDS (pure forward text, no cursor-up/rewrite) and reads novel; a redraw
    re-painting the identical screen moves the cursor up / rewrites in place and
    reads repaint. Structure (cursor motion), not content identity, is the tie-
    breaker when no new-vs-tail content is found.
 4. Implementation ceiling (agy): ANSI strip + chrome-signature strip + a bounded
    rolling tail (a few KB/seat). NEVER a VT100/pyte terminal emulator — 47
    in-memory grids at 1s ticks is out of budget by construction.
"""
import re

from .ansi import strip_ansi

TAIL_MAX = 2048          # bounded rolling content tail per seat (few KB, pin 4)
CONTENT_CAP = 2048       # only the burst's TAIL is examined for novelty: new
                         # tokens land at the end (appended-tail pin), so a
                         # full-screen redraw costs the same as a small append —
                         # keeps B1 O(KB)/burst, never O(screen) (pin 4 ceiling)

# Provider-blind volatile-chrome scrubbers: dynamic text that changes with ZERO
# model activity. Kept generic (spinners/durations/clocks/bars/percent); the
# per-provider `chrome` regexes (working/idle_footer/placeholder/done_summary)
# are layered on top from the profile.
_SPINNER_GLYPHS = "✻✽✳✶✴✹✵✷✸⬹" \
                  "⢾⢽⢻⣿⡿⣟⣯⣷⠇⠋"
_GLYPH_CLASS = "[" + re.escape(_SPINNER_GLYPHS) + "]"
_GENERIC_CHROME = [
    # whole completion/spinner status LINE to end-of-line (2.1.260 stale post-turn
    # spinner: "✻ Brewed for 40s · done 1:13 AM" — the trailing "· done <clock>"
    # residue was reading as novel content -> false streaming).
    re.compile(_GLYPH_CLASS + r"[^\n]*?\bfor\s+\d+[hms]\b[^\n]*"),
    # active gerund spinner: "Cooking… (39s · ↓1.2k tokens)" (ellipsis + elapsed
    # paren is the signature; consumes the whole line so no "Cooking…" residue).
    re.compile(r"\b\w+ing[….\s]*\(\s*\d+[hms][^)]*\)[^\n]*"),
    # glyph-led spinner with an elapsed paren but no "for": "✻ Churning (39s …)"
    re.compile(_GLYPH_CLASS + r"[^\n]*?\(\s*\d+[hms][^)]*\)[^\n]*"),
    # codex/gemini working spinner: "◦ Working (46s • esc to interrupt)"
    re.compile(_GLYPH_CLASS + r"?\s*Working\s*\([^)]*\)"),
    re.compile(_GLYPH_CLASS),                           # any stray spinner glyph
    re.compile(r"\bfor\s+\d+[hms](?:\s*\d+[ms])?\b"),   # "for 43s", "for 2m 3s"
    re.compile(r"\(\s*\d+s\b[^)]*\)"),                  # "(46s ... esc to interrupt)"
    re.compile(r"\b\d+m\s*\d+s\b"),                     # bare "2m 3s" duration
    re.compile(r"[█▓▒░]+"),         # block-bar glyphs
    re.compile(r"\b\d{1,3}%"),                          # context-% footer
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),        # clock HH:MM(:SS)
]
_PROFILE_CHROME_KEYS = ("working_re", "idle_footer_re", "idle_placeholder_re",
                        "done_summary_re")

# Re-emission structure: upward/absolute cursor movement, screen/line erase, or a
# bare carriage-return in-place rewrite. A pure forward append (printable text +
# newlines, only SGR colour which strip_ansi removes) has NONE of these.
_REEMIT_STRUCT = re.compile(
    rb"\x1b\[[0-9;]*[HfAJK]"     # cursor home/position/up, erase display/line
    rb"|\r(?!\n)"               # bare CR (rewrite current line), not CRLF
)


# Full-screen in-place redraw signature (attach/resize SIGWINCH repaint, incident
# qnr_d49f5401 / gm commission msg_3f856349): cursor-HOME present plus erase-line
# / erase-display ops at SCREEN scale. Real per-token generation positions the
# cursor (ESC[row;colH / cursor-up) and erases a line or two — the live capture
# b1-generation-perchar.ansi has 0x ESC[H, 2x ESC[K; the live resize capture
# b1-resize-repaint.ansi has 4x ESC[H, 108x ESC[K. A redraw of this shape is a
# repaint even when the tail has never seen its text (the composer/status-bar
# UI lines ride ONLY repaint bursts, so containment can never learn them), and
# its content is LEARNED into the tail so partial re-emissions contain later.
_CURSOR_HOME = re.compile(rb"\x1b\[(?:0?;0?|1;1)?[Hf]")   # ESC[H ESC[;H ESC[1;1H
_ERASE_OP = re.compile(rb"\x1b\[[0-9;]*[JK]")
FULL_REDRAW_ERASES = 24   # one terminal-height of erase-line ops (screen scale)


def _is_full_screen_redraw(raw_bytes):
    return bool(_CURSOR_HOME.search(raw_bytes)) and \
        len(_ERASE_OP.findall(raw_bytes)) >= FULL_REDRAW_ERASES


def _to_text(raw):
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    return str(raw).encode("utf-8", "replace")


def _chrome_strip(text, profile):
    for rx in _GENERIC_CHROME:
        text = rx.sub("", text)
    chrome = getattr(profile, "chrome", None) or {}
    for key in _PROFILE_CHROME_KEYS:
        pat = chrome.get(key)
        if pat:
            try:
                text = re.sub(pat, "", text)
            except re.error:
                pass
    return text


def _norm(text):
    """Collapse whitespace runs to single spaces; strip ends. Keeps content
    tokens + order; drops the pure-whitespace churn a repaint shuffles."""
    return re.sub(r"\s+", " ", text).strip()


def _alnum(text):
    """Lowercase alphanumeric-only projection — a punctuation/whitespace/box-glyph
    -insensitive form for the repaint containment test (│ vs |, spacing shifts)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _extend(tail, addition):
    add = _norm(addition)
    if not add:
        return tail
    joined = (tail + " " + add) if tail else add
    return joined[-TAIL_MAX:]


def classify_burst(raw, tail, *, profile):
    """Classify one pipe-pane burst. Returns (verdict, new_tail).

    verdict: "novel_content" (real generation -> stream), "repaint" (a redraw /
    chrome tick re-emitting known screen content in place -> not streaming), or
    "chrome" (pure ANSI / spinner glyph with no output at all -> not streaming).

    STRUCTURE-FIRST (pin 3): an in-place rewrite (cursor home/position/up, screen
    or line erase, or a bare CR) OVERWRITES existing screen regions — it never
    appends to the output stream. A pure forward burst (only SGR colour, text and
    newlines) DOES append. So structure selects the novelty test:
      * in-place  -> novel ONLY if it paints text not already in the tail (a
        growing per-token line is novel; a redraw / counter tick is not);
      * forward   -> the model wrote new output (even repeated separators or bare
        newlines), unless the burst is pure chrome after stripping."""
    raw_bytes = _to_text(raw)
    reemit = bool(_REEMIT_STRUCT.search(raw_bytes))   # over full raw (cheap early match)
    # content work is bounded to the burst tail: novel tokens land at the end, a
    # redraw's tail is its footer (already in the rolling tail). O(KB)/burst.
    stripped = strip_ansi(raw_bytes[-CONTENT_CAP:].decode("utf-8", "replace"))
    clean = _chrome_strip(stripped, profile)
    printable = clean.strip()

    if not printable:
        # no textual content after chrome exclusion. A FORWARD burst of PURE
        # newlines (the burst had no text even before chrome-strip) is real
        # repeated-newline output (pin 3). But a burst whose TEXT was removed BY
        # chrome-strip (e.g. a stale "✻ Brewed for 40s · done 1:13 AM" spinner
        # line, leaving only its trailing newline) is chrome, not output.
        if not reemit and "\n" in clean and not _norm(stripped):
            return "novel_content", tail
        return "chrome", tail

    if reemit:
        if not tail:
            # first burst we ever see for this seat is a cursor-positioned paint:
            # generation is forward-append, so an in-place paint on an empty tail
            # is a redraw/first-render. Seed the tail, never upgrade (kills the
            # daemon-startup false-active on the first fleet redraw).
            return "repaint", _extend(tail, clean)
        # a structurally full-screen redraw (cursor-home + screen-scale erases:
        # attach/resize repaint) is a repaint even when containment fails —
        # learn its content so later partial repaints contain. Cost bound: a
        # resize landing mid-generation is at most ONE repaint-classed tick;
        # the next per-char burst (no cursor-home) reclasses novel_content.
        if _is_full_screen_redraw(raw_bytes):
            return "repaint", _extend(tail, clean)
        # in-place repaint: novel ONLY if it paints text not already in the tail
        # (a growing per-token line), else a redraw / counter tick.
        if _alnum(clean) not in _alnum(tail):
            return "novel_content", _extend(tail, clean)
        return "repaint", tail

    # forward append with printable content: the model wrote new output.
    return "novel_content", _extend(tail, clean)
