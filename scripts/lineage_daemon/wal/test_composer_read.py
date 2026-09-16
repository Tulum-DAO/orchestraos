"""RED (DEC-1789517918317482 G1): a pure, runtime-agnostic composer reader for the BG obs.
agent-status's chrome parser knows only the Claude U+276F / ASCII '>' prompts framed by rule
lines; the codex prompt is U+203A with no framing, so codex composer occupancy was invisible.
Bottom-most prompt line wins; placeholder => ''; typed => the text; no prompt line => None
(unreadable => decide_bg suppress:composer, fail-closed)."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.composer_read import composer_text  # noqa: E402

CODEX_IDLE = [
    "  The task is to identify and apply the correct abbreviated value format",
    "  step is to update the relevant literal values if requested.",
    "",
    "› Ask Codex to do anything",
    "  gpt-5.6-terra medium fast · ~/scripts/agent-orchestra",
]


def test_codex_bare_prompt_is_empty_composer():
    assert composer_text(CODEX_IDLE) == ""


def test_codex_typed_text_is_returned():
    lines = CODEX_IDLE[:3] + ["› fix the abbreviated value format", CODEX_IDLE[4]]
    assert composer_text(lines) == "fix the abbreviated value format"


def test_claude_placeholder_is_empty_composer():
    lines = ["────────────────", "❯\xa0Try \"fix lint errors\"", "────────────────",
             "  ⏵⏵ accept edits on"]
    assert composer_text(lines) == ""


def test_claude_typed_text_is_returned():
    lines = ["────────────────", "❯\xa0please rerun the suite", "────────────────"]
    assert composer_text(lines) == "please rerun the suite"


def test_bottom_most_prompt_wins_over_history():
    lines = ["❯ earlier submitted prompt", "  assistant output", "› "]
    assert composer_text(lines) == ""


def test_no_prompt_line_is_unreadable():
    assert composer_text(["  1. Option A", "  2. Option B", "  Enter to select"]) is None
    assert composer_text([]) is None
