"""RED-first: read each CLI's OWN typed streaming events.

Every expectation here comes from output captured from the installed CLIs
(services/arturo/fixtures/cli_streams/*.jsonl, claude 2.1.284 / agy 1.2.13 / codex 0.153.4),
not from a guess about their shapes. Spec §0.13 measured the same three.
"""
import json
from pathlib import Path

import pytest

from services.arturo.cli_events import (
    stream_command, read_events, TextDelta, ThinkingDelta, TurnEnd, StreamError,
)

FIX = Path(__file__).resolve().parent / "fixtures" / "cli_streams"


def lines(name):
    return (FIX / f"{name}.jsonl").read_text().splitlines(keepends=True)


def events(runtime, name=None):
    return list(read_events(runtime, iter(lines(name or runtime))))


def text_of(evs):
    return "".join(e.text for e in evs if isinstance(e, TextDelta))


# ---- claude: API-shaped stream_event envelopes ---------------------------------------

def test_claude_text_deltas_reassemble_the_reply():
    evs = events("claude")
    assert "hello there" in text_of(evs)


def test_claude_thinking_never_reaches_the_spoken_text():
    """The capture carries thinking deltas with EMPTY text — claude's default display is
    `omitted`, so there is reasoning happening and nothing to show. What matters is the
    guarantee: a thinking delta is never appended to the reply, empty or not."""
    raw = "".join(lines("claude"))
    assert '"thinking_delta"' in raw, "the fixture must exercise the thinking channel"
    evs = events("claude")
    assert text_of(evs).strip() == "hello there"
    assert all(not isinstance(e, TextDelta) or "thinking" not in e.text.lower() for e in evs)


def test_a_thinking_delta_with_text_is_reported_on_its_own_channel():
    """If display is ever turned on, reasoning must arrive as ThinkingDelta — not as reply."""
    line = json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta",
        "delta": {"type": "thinking_delta", "thinking": "weighing the options"}}}) + "\n"
    evs = list(read_events("claude", iter([line])))
    assert any(isinstance(e, ThinkingDelta) and e.text == "weighing the options" for e in evs)
    assert text_of(evs) == ""


def test_claude_ends_with_one_turn_end_carrying_the_whole_reply():
    evs = events("claude")
    ends = [e for e in evs if isinstance(e, TurnEnd)]
    assert len(ends) == 1
    assert ends[0].reply_text.strip() == text_of(evs).strip()


# ---- agy: step_update.text_delta -----------------------------------------------------

def test_agy_text_deltas_reassemble_the_reply():
    assert text_of(events("gemini", "agy")).strip() == "hello there"


def test_agy_result_event_closes_the_turn():
    ends = [e for e in events("gemini", "agy") if isinstance(e, TurnEnd)]
    assert len(ends) == 1
    assert ends[0].reply_text.strip() == "hello there"


# ---- codex: no token deltas (§0.13) --------------------------------------------------

def test_codex_emits_the_whole_message_as_ONE_delta_and_says_so():
    evs = events("codex")
    deltas = [e for e in evs if isinstance(e, TextDelta)]
    assert len(deltas) == 1, "codex has no token deltas — one final delta, honestly"
    assert deltas[0].text.strip() == "hello there"
    assert deltas[0].incremental is False


def test_claude_and_agy_deltas_are_marked_incremental():
    for runtime, name in (("claude", "claude"), ("gemini", "agy")):
        d = [e for e in events(runtime, name) if isinstance(e, TextDelta)]
        assert d and all(x.incremental for x in d), runtime


def test_codex_error_items_do_not_become_spoken_text():
    # the capture contains an item.completed of type "error" (a skills-budget notice)
    assert "Skill descriptions" not in text_of(events("codex"))


# ---- robustness ----------------------------------------------------------------------

def test_a_junk_line_is_skipped_not_fatal():
    evs = list(read_events("claude", iter(["not json\n"] + lines("claude"))))
    assert "hello there" in text_of(evs)


def test_a_stream_that_ends_without_a_result_still_closes_the_turn():
    partial = [l for l in lines("agy") if '"result"' not in l]
    evs = list(read_events("gemini", iter(partial)))
    assert isinstance(evs[-1], TurnEnd), "a truncated stream must still end the turn"
    assert evs[-1].truncated is True


def test_an_empty_stream_is_an_error_event_not_a_silent_empty_reply():
    evs = list(read_events("claude", iter([])))
    assert isinstance(evs[-1], StreamError)


# ---- the command that produces those events ------------------------------------------

def test_stream_command_uses_the_flags_each_cli_actually_needs():
    claude = stream_command("claude", "claude", "SYS", "hi", model="claude-opus-5")
    assert "--output-format" in claude.argv and "stream-json" in claude.argv
    # measured: claude REJECTS --output-format stream-json without --verbose (§0.13)
    assert "--verbose" in claude.argv
    assert "--include-partial-messages" in claude.argv
    assert "--model=claude-opus-5" in claude.argv

    agy = stream_command("gemini", "agy", "SYS", "hi", model="gemini-3.8-flash-high")
    assert "--output-format" in agy.argv and "stream-json" in agy.argv
    assert "--model=gemini-3.8-flash-high" in agy.argv

    codex = stream_command("codex", "codex", "SYS", "hi")
    assert "--json" in codex.argv


def test_an_unknown_runtime_refuses_rather_than_inventing_flags():
    with pytest.raises(ValueError):
        stream_command("grok", "grok", "SYS", "hi")
