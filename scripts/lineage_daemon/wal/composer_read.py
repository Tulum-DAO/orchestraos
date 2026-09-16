"""composer_read — pure, runtime-agnostic read of a pane's composer (input) line for the
Blue-Green obs (DEC-1789517918317482 G1, work-loss guard).

agent-status.py's chrome parser recognizes only the Claude prompts (U+276F / ASCII '>')
framed by rule lines; the codex prompt is U+203A with no framing, so codex composer
occupancy was invisible to the beat. This reader keys on the BOTTOM-MOST prompt line of a
stripped screen (`tmux capture-pane -p`), any runtime:
  * placeholder / bare prompt  -> ""      (composer empty: safe to swap)
  * typed text                 -> text    (unsubmitted work: never swap)
  * no prompt line at all      -> None    (unreadable: decide_bg suppresses, fail-closed)
"""
import re

PROMPT_GLYPHS = ("❯", "›", ">")   # ❯ (claude), › (codex), > (claude alt / gemini)

# Ghost/placeholder text each runtime renders in an EMPTY composer.
_PLACEHOLDER_RES = (
    re.compile(r'^Try "[^"]{0,60}"$'),                 # claude
    re.compile(r"^Ask Codex to do anything$"),         # codex
    re.compile(r"^Type your message", re.I),           # gemini / antigravity
)


def _is_placeholder(text):
    return any(r.match(text) for r in _PLACEHOLDER_RES)


def composer_text(lines):
    """Typed composer content of the bottom-most prompt line, '' if empty/placeholder,
    None when no prompt line is present (menu screen, dialog, dead pane, empty capture)."""
    for raw in reversed(list(lines or [])):
        t = (raw or "").replace("\xa0", " ").strip()
        if not t:
            continue
        glyph = next((g for g in PROMPT_GLYPHS if t.startswith(g)), None)
        if glyph is None or t.startswith(">>"):
            continue
        text = t[len(glyph):].strip()
        return "" if (not text or _is_placeholder(text)) else text
    return None
