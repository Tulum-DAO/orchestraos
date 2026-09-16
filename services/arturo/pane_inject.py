# pane_inject.py — pure helpers for the THREE-RUNTIME pane-inject invariant (Bug 1).
#
# Kept pure (no I/O) so the two-phase submit + landed-classification logic is unit-testable against
# real captured pane shapes. The live handler (arturo-proxy.py inject_message / spawn task-inject)
# composes these into: paste(literal) -> settle -> submit(C-m); verify landed; on unverified send
# ANOTHER C-m only (never a re-paste). See test_pane_inject.py / test_inject_invariant.py.
#
# Why two-phase (gemini-gm dx msg_d30b98a6/msg_4fbc8aee, the operator-ratified): a single
# `send-keys '{text}' Enter` submits inconsistently across runtimes — on Gemini/AGY the paste never
# fires onSubmit (bracketed-paste/autocomplete) and on Claude/Codex the trailing Enter is absorbed
# into the paste buffer, stranding the text. Sending the literal text FIRST, letting the composer
# settle, then a STANDALONE carriage return, fires onSubmit reliably on all three.

import re

MAC_PREFIX = "PATH=/opt/homebrew/bin:$PATH "

# Time to let the composer settle after the literal paste before the carriage return: covers
# bracketed-paste flush and autocomplete popups that would otherwise eat the C-m.
SUBMIT_SETTLE_S = 0.35

# How far up from the bottom the composer input can be — enough to clear border + status lines
# without reaching into the transcript above.
_COMPOSER_TAIL_LINES = 8

# Prompt markers that begin the composer input line across the three runtimes.
_PROMPT_MARKERS = ("❯", "›", ">", "|")

# Placeholder input == an EMPTY composer (nothing typed yet).
_PLACEHOLDERS = ("ask codex to do anything", "type your message", "send a message")

# An active turn/spinner: Claude's elapsed+token status line, or an explicit interrupt hint on any
# runtime. Presence of either means the submit fired (a turn is running) => landed.
_ACTIVE_SUBSTR = ("to interrupt", "to cancel", "esc to stop", "ctrl+c to interrupt")
_ACTIVE_RE = re.compile(r"\d+\s*m\s*\d+\s*s.*tokens", re.IGNORECASE)


def _shell_squote(text):
    """Escape a body for embedding inside single quotes in a POSIX shell command."""
    return text.replace("'", "'\\''")


def strip_trailing_submit(text):
    """Drop trailing CR/LF so the literal paste ends at the text and cannot self-submit."""
    return (text or "").rstrip("\r\n")


def paste_command(session, text, mac=False):
    """Phase 1: send the message as LITERAL keystrokes (send-keys -l), NO Enter/C-m.
    `--` guards a body that starts with '-'."""
    body = _shell_squote(strip_trailing_submit(text))
    prefix = MAC_PREFIX if mac else ""
    return f"{prefix}tmux send-keys -t {session} -l -- '{body}'"


def submit_command(session, mac=False):
    """Phase 2 (and the ONLY thing a retry ever sends): a standalone carriage return that fires
    onSubmit. C-m, not the 'Enter' keyword — 'Enter' can be swallowed by a bracketed paste."""
    prefix = MAC_PREFIX if mac else ""
    return f"{prefix}tmux send-keys -t {session} C-m"


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def turn_active(pane_text):
    """True if a turn/spinner is running (=> the submit fired)."""
    low = (pane_text or "").lower()
    if any(m in low for m in _ACTIVE_SUBSTR):
        return True
    return bool(_ACTIVE_RE.search(pane_text or ""))


def composer_input(pane_text):
    """Return the current composer input text (what's typed after the prompt marker), or None if a
    composer prompt line can't be located in the bottom region. Scans upward from the bottom."""
    lines = [ln for ln in (pane_text or "").splitlines()]
    tail = lines[-_COMPOSER_TAIL_LINES:] if len(lines) > _COMPOSER_TAIL_LINES else lines
    for ln in reversed(tail):
        stripped = ln.strip()
        if not stripped:
            continue
        for mk in _PROMPT_MARKERS:
            if stripped.startswith(mk):
                rest = stripped[len(mk):].strip()
                # a pure border line ("|---|" / "----") isn't a prompt
                if rest and set(rest) <= set("-─|│ "):
                    continue
                return rest
    return None


def _snippet(message):
    """A distinctive normalized run of the message, matching how the handler probes."""
    s = re.sub(r"[^a-z0-9 ]", "", (message or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s[:60] if len(s) <= 60 else s[10:70]


def is_landed(pane_text, message):
    """Did the injected message actually SUBMIT? Keyed off the composer state, never whole-pane
    visibility (the old false-positive). Active turn OR cleared composer => landed; the message
    still sitting in the composer => NOT landed; anything ambiguous => NOT landed (caller retries a
    harmless C-m)."""
    if turn_active(pane_text):
        return True
    ci = composer_input(pane_text)
    if ci is None:
        return False
    ci_norm = _norm(ci)
    if not ci_norm or ci_norm in _PLACEHOLDERS:
        return True                       # composer cleared -> submitted (or idle)
    if _snippet(message) in ci_norm:
        return False                      # stranded in the composer -> NOT landed
    return False                          # some other unexpected composer text -> unverified
