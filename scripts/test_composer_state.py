"""RED-first tests for composer_state — the one robust composer reader.

Fixtures carry REAL SGR captured by effect from live CLIs (tmux capture-pane -e -p):
  * gemini idle:  \\x1b[38;5;111m>\\x1b[39m
  * codex  idle:  \\x1b[1m\\u203a\\x1b[0m \\x1b[2mAsk Codex to do anything\\x1b[0m
  * claude bare:  \\x1b[39m\\u276f\\xa0
The Claude ghost/typed fixtures are built from the SAME verified SGR-dim grammar
(message-router.py:input_line_state, empirically pinned on Claude Code 2.1.x) — a ghost
is ESC[2m-wrapped words + an ESC[7m reverse cursor, exactly the mechanism the real codex
placeholder above proves on live data. The headline bug (a CONTENT ghost read as typed)
uses a neutral example sentence.
"""
import sys

sys.path.insert(0, "scripts")
from scripts.composer_state import classify_input_line, read_composer  # noqa: E402
from scripts.runtime_signatures import PROMPT_SIGNATURES  # noqa: E402

CLAUDE = PROMPT_SIGNATURES["claude"]
GEMINI = PROMPT_SIGNATURES["gemini"]
CODEX = PROMPT_SIGNATURES["codex"]

# --- real / verified-grammar fixtures --------------------------------------------------
CLAUDE_EMPTY = "\x1b[39m❯\xa0 "
CLAUDE_GHOST_PLACEHOLDER = "❯ \x1b[2mTry \"fix the tests\"\x1b[0m\x1b[7m \x1b[27m"
# the exact bug: a CONTENT ghost suggestion, dim-wrapped, NOT human input
CLAUDE_GHOST_CONTENT = (
    "❯ \x1b[2myes\x1b[0m \x1b[2mexample.com\x1b[0m \x1b[2mis\x1b[0m \x1b[2mprimary\x1b[0m"
    " \x1b[2mnow\x1b[0m\x1b[7m \x1b[27m")
CLAUDE_TYPED = "❯ deploy the build\x1b[7m \x1b[27m"
# human typed a prefix, the runtime ghost-completes the tail -> still TYPED (human input)
CLAUDE_TYPED_GHOST_TAIL = "❯ remove old\x1b[2m-domain.com from search console\x1b[0m\x1b[7m \x1b[27m"

GEMINI_EMPTY = "\x1b[38;5;111m>\x1b[39m"
GEMINI_TYPED = "\x1b[38;5;111m>\x1b[39m ship it now"

CODEX_GHOST_PLACEHOLDER = "\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m"
CODEX_TYPED = "\x1b[1m›\x1b[0m run the migration"

CLAUDE_WORKING_SCREEN = "✶ Sautéing… (12s · esc to interrupt)\n❯\xa0 "


# --- classify_input_line: per state, per runtime ---------------------------------------

def test_claude_empty():
    assert classify_input_line(CLAUDE_EMPTY, CLAUDE) == ("empty", "")

def test_claude_ghost_placeholder():
    assert classify_input_line(CLAUDE_GHOST_PLACEHOLDER, CLAUDE) == ("ghost", "")

def test_claude_content_ghost_is_ghost_not_typed():
    """THE bug: a dim content suggestion must be 'ghost', never 'typed'."""
    state, text = classify_input_line(CLAUDE_GHOST_CONTENT, CLAUDE)
    assert state == "ghost", (state, text)
    assert text == ""

def test_claude_typed():
    state, text = classify_input_line(CLAUDE_TYPED, CLAUDE)
    assert state == "typed"
    assert text == "deploy the build"

def test_claude_typed_with_ghost_tail_is_typed():
    """Human typed a prefix + runtime ghost-completed the rest -> TYPED (preserve it)."""
    state, text = classify_input_line(CLAUDE_TYPED_GHOST_TAIL, CLAUDE)
    assert state == "typed"
    assert text == "remove old"

def test_gemini_empty():
    assert classify_input_line(GEMINI_EMPTY, GEMINI) == ("empty", "")

def test_gemini_typed():
    state, text = classify_input_line(GEMINI_TYPED, GEMINI)
    assert state == "typed"
    assert text == "ship it now"

def test_codex_placeholder_is_ghost():
    assert classify_input_line(CODEX_GHOST_PLACEHOLDER, CODEX) == ("ghost", "")

def test_codex_typed():
    state, text = classify_input_line(CODEX_TYPED, CODEX)
    assert state == "typed"
    assert text == "run the migration"


# --- read_composer: full contract via injected capture ---------------------------------

def _cap(text):
    return lambda pane: text

def test_read_composer_claude_content_ghost():
    r = read_composer("seat", runtime="claude", capture_fn=_cap("some output\n" + CLAUDE_GHOST_CONTENT))
    assert r["state"] == "ghost" and r["text"] == "" and r["runtime"] == "claude"

def test_read_composer_claude_typed_returns_text():
    r = read_composer("seat", runtime="claude", capture_fn=_cap(CLAUDE_TYPED))
    assert r["state"] == "typed" and r["text"] == "deploy the build"

def test_read_composer_submitted_working_outranks_line():
    r = read_composer("seat", runtime="claude", capture_fn=_cap(CLAUDE_WORKING_SCREEN))
    assert r["state"] == "submitted"

def test_read_composer_unknown_runtime_never_typed():
    """An unrecognized runtime with a line that LOOKS typed must be 'unknown', not 'typed'."""
    r = read_composer("seat", runtime="borkbork", capture_fn=_cap("> some human text"))
    assert r["state"] == "unknown"
    assert r["state"] != "typed"

def test_read_composer_capture_failure_is_unknown():
    r = read_composer("seat", runtime="claude", capture_fn=lambda p: None)
    assert r["state"] == "unknown"

def test_read_composer_no_prompt_line_is_unknown():
    r = read_composer("seat", runtime="claude", capture_fn=_cap("just some scrollback\nno prompt here"))
    assert r["state"] == "unknown"


# --- the -e requirement: a STRIPPED capture cannot see a content ghost -------------------

def test_stripped_capture_cannot_see_content_ghost_but_e_capture_can():
    """Proves WHY every reader must capture with -e. On a STRIPPED capture (SGR removed) the
    dim styling is gone, so a CONTENT ghost is indistinguishable from typed text and reads as
    typed — the exact defect. The SAME line captured WITH -e classifies as ghost."""
    import re as _re
    stripped = _re.sub(r"\x1b\[[0-9;]*m", "", CLAUDE_GHOST_CONTENT)  # what capture-pane -p gives
    from scripts.composer_state import composer_text
    assert composer_text([stripped], runtime="claude"), "stripped: ghost misread as typed (the bug)"
    with_e = read_composer("seat", runtime="claude", capture_fn=_cap(CLAUDE_GHOST_CONTENT))
    assert with_e["state"] == "ghost" and with_e["text"] == "", "-e capture classifies it ghost"
