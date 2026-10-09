"""composer_verify.py — did text typed into an agent pane actually SUBMIT? (pure helpers)

ONE implementation for every typer: message-router.py's inject() (which re-exports these
names, so its E7 tests are unchanged) and boot_inject.py (every boot path). Moved verbatim
from message-router.py (E7, msg_56c5cdb8) under DEC-1791347425871474, Q1.

The rule they encode: visibility alone is NOT submission. Claude Code ingests a paste
asynchronously and collapses a large one into a "[Pasted text #N +NN lines]" chip. An Enter
that arrives early is swallowed and the content SITS in the composer. A typer that greps for its
text reads that strand as success; one that checks the text is gone reads a chip as success.
Submitted means: the composer is clear of BOTH the text and any chip, AND the content
(literal or chip marker) is in the scrollback above it.
"""
import re

# Claude: '[Pasted text #N +NN lines]'. Codex (0.148): '[Pasted Content N chars]'
# (display compression, content intact — dossier §3). Both are composer chips.
_PASTE_CHIP_RE = re.compile(r'\[Pasted (?:text|Content)[^\]]*\]')


def count_paste_chips(line: str) -> int:
    return len(_PASTE_CHIP_RE.findall(line or ""))


def composer_has_stranded_chip(input_line: str) -> bool:
    """ANY pre-existing chip in the composer = a stranded prior paste. It is
    unattributable (our wedged retry OR a human draft), so the router must
    NEVER paste another chip onto it and NEVER press Enter on it (composer-
    draft law). Hold; note_hold/SLA escalation surfaces it."""
    return count_paste_chips(input_line) > 0


def paste_receipt_ok(input_line: str, probe: str, chips_before: int) -> bool:
    """Stage-1 receipt: the literal probe is visible OR a NEW chip appeared."""
    return (probe[:30] in input_line) or count_paste_chips(input_line) > chips_before


def submit_ok(scrollback, input_line: str, probe: str) -> bool:
    """Stage-2 submission: the input line is clear of BOTH the literal text and
    any chip, AND the content (literal or chip marker) moved into scrollback."""
    p = probe[:30]
    input_clear = p not in input_line and count_paste_chips(input_line) == 0
    in_scrollback = (any(p in l for l in scrollback)
                     or any(_PASTE_CHIP_RE.search(l) for l in scrollback))
    return input_clear and in_scrollback


def codex_pane_is_busy(pane_text: str) -> bool:
    """Codex-only busy read: '◦ Working (Ns • esc to interrupt)'. Enter into a
    WORKING codex pane QUEUES to the next tool boundary (verified, dossier §3) —
    the scrollback-moved receipt would then false-ack a message the model has
    not consumed. So the router holds instead of injecting. The queued-composer
    footer ('tab to queue message') is an equivalent busy signal."""
    txt = pane_text or ""
    return ("◦ Working" in txt) or ("tab to queue message" in txt)


def split_capture(capture: str, prompt_char: str, runtime: str):
    """(scrollback_lines, input_line, ok) for one capture-pane text. input_line is the LAST line
    carrying the runtime's prompt char; everything above it is scrollback. A gemini composer
    spans from its prompt line to the bottom. No prompt line -> (lines, "", False)."""
    lines = capture.split("\n")
    last_prompt = -1
    for i, l in enumerate(lines):
        if prompt_char in l:
            last_prompt = i
    if last_prompt == -1:
        return lines, "", False
    if runtime == "gemini":
        return lines[:last_prompt], "\n".join(lines[last_prompt:]), True
    return lines[:last_prompt], lines[last_prompt], True
