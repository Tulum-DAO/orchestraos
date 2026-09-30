"""RED-first for Phase 2 §2.3 — first-character classification + streaming sanitisers.

The one property that matters: a reply sanitised INCREMENTALLY must equal the same reply
sanitised in one batch. Voice already sanitises in batch (voice_guards.strip_tool_code ->
strip_thought_block -> strip_leading_tool_reasoning); streaming must not become a second,
subtly different answer. So every case here is asserted against the batch functions
themselves, and across EVERY chunk boundary, not one convenient split.
"""
import json
from pathlib import Path

import pytest

from services.arturo import voice_guards as VG
from services.arturo.stream_sanitize import (
    PassClassifier, StreamingSanitizer, batch_sanitize, HOLD, PROSE,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "services" / "arturo" / "fixtures"


def chunkings(text):
    """Every split that matters: one byte at a time, one big chunk, and each single cut."""
    yield [text]
    yield list(text)
    for i in range(1, len(text)):
        yield [text[:i], text[i:]]


def stream_all(text, chunks):
    s = StreamingSanitizer()
    out = []
    for c in chunks:
        out.append(s.feed(c))
    out.append(s.finish())
    return "".join(out)


CORPUS = {
    "plain": "On it, I'll text Shaw now.",
    "thought_multi": "thought\nThe user is reporting a bug. I should check.\n\nI've checked — it's fixed.",
    "thought_single": "thought\nThe user is reporting a bug and I should check the logs.",
    "ticked_leading": "Therefore, the `ask_gm` tool is appropriate here.On it, I'll text Shaw.",
    "ticked_all": "The `ask_gm` tool is right. I will use `ask_gm` now.",
    "tool_fence": "Sure.\n\n```tool_code\nprint(default_api.ask_gm(x=1))\n```\n\nDone.",
    "default_api_fence": "Here goes.\n\n```python\nprint(default_api.send(x=1))\n```\n\nSent.",
    "legit_fence": "Try this:\n\n```python\nprint('hello')\n```\n\nThat works.",
    "bare_tool_code": "Okay.\ntool_code\nprint(default_api.ask_gm(x=1))\nAll set.",
    "blank_runs": "First.\n\n\n\nSecond.",
    "trailing_ws": "Answer.   \n\n  ",
    "leading_ws": "   \n  Answer.",
    "empty": "",
    "only_tool": "```tool_code\nprint(default_api.x())\n```",
    # a ticked name that is not a tool is prose; the dots inside the span are not sentence ends
    "ticked_model": "I'm on Codex (`gpt-5.6-luna`) via your `codex` CLI. No API key is involved.",
    "ticked_model_all": "I am running on Codex, model `gpt-5.6-luna`, via your `codex` CLI.",
    "ticked_mixed": "I'll route it via `ask_gm`. You're on `codex` tonight. Want the list?",
    "ticked_dotted_tool": "I will call `default_api.send_telegram` now. Sent.",
}


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_streaming_equals_batch_on_every_chunk_boundary(name):
    text = CORPUS[name]
    expected = batch_sanitize(text)
    for chunks in chunkings(text):
        got = stream_all(text, chunks)
        assert got == expected, f"{name}: {chunks!r} -> {got!r} != {expected!r}"


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_batch_sanitize_is_the_voice_pipeline_itself(name):
    """Not a reimplementation: the batch reference IS what voice journals today."""
    text = CORPUS[name]
    assert batch_sanitize(text) == VG.strip_leading_tool_reasoning(
        VG.strip_thought_block(VG.strip_tool_code(text)))


def test_streaming_equals_batch_on_the_repo_fixture_corpus():
    for path in sorted(FIXTURES.glob("*.txt")):
        text = path.read_text()
        expected = batch_sanitize(text)
        for chunks in ([text], list(text), [text[:7], text[7:]]):
            assert stream_all(text, chunks) == expected, path.name


def test_streaming_equals_batch_on_the_native_markup_fixture_json():
    cases = json.loads((FIXTURES / "context_line_cases.json").read_text())
    texts = [c for c in _walk_strings(cases)][:40]
    for text in texts:
        expected = batch_sanitize(text)
        assert stream_all(text, [text]) == expected
        assert stream_all(text, list(text)) == expected


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


# ---- first-character classification (§2.3) -------------------------------------------

def test_classifier_holds_a_pass_that_opens_with_json():
    c = PassClassifier()
    assert c.feed('  {"tool"') == HOLD


def test_classifier_holds_a_pass_that_opens_with_a_code_fence():
    assert PassClassifier().feed("```tool_code\n") == HOLD


def test_classifier_holds_a_pass_that_opens_with_native_markup():
    assert PassClassifier().feed("<tool_call>") == HOLD


def test_classifier_calls_ordinary_prose_prose():
    assert PassClassifier().feed("On it, I'll") == PROSE


def test_classifier_waits_rather_than_guessing_on_whitespace_alone():
    c = PassClassifier()
    assert c.feed("   ") is None, "no first character yet — undecided, not a guess"
    assert c.feed("\n {") == HOLD


def test_classifier_decides_once_and_stays_decided():
    c = PassClassifier()
    assert c.feed("Hello") == PROSE
    # a fence LATER in a prose pass is the sanitiser's problem, not a reclassification
    assert c.feed("\n```tool_code\n") == PROSE


def test_a_held_pass_still_streams_nothing_before_its_verdict():
    """The point of HOLD: nothing user-visible escapes until the pass is parsed whole."""
    c = PassClassifier()
    c.feed('{"name":"ask_gm"')
    assert c.buffer == '{"name":"ask_gm"'
    assert c.emitted == ""
