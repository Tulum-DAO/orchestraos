"""RED-first for /text/stream — the endpoint that puts words on screen as they arrive.

Design under test (spec §2.1, §2.5, and the fork condition in §2.4):
 - a NEW endpoint; /text is untouched;
 - `turn.start` goes out on receipt, so the client can show "Sent" on a server fact;
 - prose streams as `text.delta`, sanitised incrementally (stream_sanitize);
 - a reply that opens as STRUCTURE (our tool-call envelope) is never streamed: the turn falls
   back to the ordinary /text path, tool loop and all, and its reply arrives whole;
 - the thread is recorded ONCE, through the same function /text uses, so the two endpoints
   cannot drift apart.
"""
import json

import pytest

from services.arturo import text_stream as TS


class FakeBrain:
    kind = "runtime"
    runtime = "claude"
    cli = "claude"
    model = "claude-opus-5"

    def describe(self):
        return {"kind": "runtime", "runtime": "claude", "model": self.model}


def events(chunks, fallback=None, brain=None, record=None):
    """Drive the generator with a canned CLI stdout."""
    return list(TS.stream_turn(
        text="hi", conversation_id="c1", brain=brain or FakeBrain(),
        brain_id={"provider": "claude", "model": "claude-opus-5"},
        messages=[{"role": "user", "content": "hi"}],
        spawn=lambda cmd: iter(chunks),
        fallback=fallback or (lambda: (200, {"ok": True, "reply_text": "fallback reply",
                                             "tools_called": [], "spawned": []})),
        record=record or (lambda **kw: None),
    ))


def kinds(evs):
    return [e["event"] for e in evs]


def payloads(evs, kind):
    return [e["data"] for e in evs if e["event"] == kind]


CLAUDE_PROSE = [
    json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "On it, "}}}) + "\n",
    json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "I'll text Shaw now."}}}) + "\n",
    json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n",
]

CLAUDE_TOOL_ENVELOPE = [
    json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                "delta": {"type": "text_delta", "text": '{"tool_calls":'}}}) + "\n",
    json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                "delta": {"type": "text_delta", "text": '[{"name":"ask_gm"}]}'}}}) + "\n",
    json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n",
]


def test_turn_start_is_the_first_event_before_any_model_output():
    evs = events(CLAUDE_PROSE)
    assert kinds(evs)[0] == "turn.start"
    assert payloads(evs, "turn.start")[0]["conversation_id"] == "c1"
    assert payloads(evs, "turn.start")[0]["brain"]["model"] == "claude-opus-5"


def test_prose_streams_as_deltas_and_reassembles_into_the_reply():
    evs = events(CLAUDE_PROSE)
    streamed = "".join(d["text"] for d in payloads(evs, "text.delta"))
    assert streamed == "On it, I'll text Shaw now."
    assert payloads(evs, "turn.end")[0]["reply_text"] == streamed


def test_the_turn_is_recorded_once_with_the_streamed_text():
    seen = []
    events(CLAUDE_PROSE, record=lambda **kw: seen.append(kw))
    assert len(seen) == 1
    assert seen[0]["reply"] == "On it, I'll text Shaw now."
    assert seen[0]["conversation_id"] == "c1"


def test_a_tool_envelope_is_NEVER_streamed_to_the_client():
    evs = events(CLAUDE_TOOL_ENVELOPE)
    assert not any('"tool_calls"' in d["text"] for d in payloads(evs, "text.delta"))


def test_a_tool_envelope_falls_back_to_the_ordinary_text_path():
    calls = []

    def fallback():
        calls.append(True)
        return 200, {"ok": True, "reply_text": "did the thing", "tools_called": ["ask_gm"], "spawned": []}

    evs = events(CLAUDE_TOOL_ENVELOPE, fallback=fallback)
    assert calls, "the tool loop must run — streaming does not get to skip tools"
    assert payloads(evs, "turn.end")[0]["reply_text"] == "did the thing"
    assert payloads(evs, "turn.end")[0]["tools_called"] == ["ask_gm"]


def test_the_fallback_path_records_through_ITS_own_turn_not_twice():
    seen = []
    events(CLAUDE_TOOL_ENVELOPE, record=lambda **kw: seen.append(kw))
    assert seen == [], "the /text path already recorded it; recording again would double the thread"


def test_a_failed_stream_ends_in_error_and_records_nothing():
    seen = []
    evs = events([json.dumps({"type": "result", "is_error": True, "result": "boom"}) + "\n"],
                 record=lambda **kw: seen.append(kw))
    assert kinds(evs)[-1] == "error"
    assert payloads(evs, "error")[0]["code"]
    assert seen == []


def test_a_stream_that_produced_NO_text_falls_back_rather_than_giving_up():
    """Nothing was shown and no tool ran, so re-running down /text is safe and gets the
    operator an answer instead of an empty bubble."""
    evs = events([json.dumps({"type": "result", "is_error": False, "result": ""}) + "\n"])
    assert payloads(evs, "turn.end")[0]["reply_text"] == "fallback reply"


def test_a_reply_that_SANITISES_to_nothing_is_an_error_not_an_empty_bubble():
    """All the model produced was tool syntax. There is nothing to show and nothing to save;
    falling back would re-run a turn whose answer we already have (an empty one)."""
    all_tool_code = [
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Working.\n\n```tool_code\n"}}}) + "\n",
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "print(default_api.x())\n```"}}}) + "\n",
        json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n",
    ]
    seen = []
    evs = events(all_tool_code, record=lambda **kw: seen.append(kw))
    streamed = "".join(d["text"] for d in payloads(evs, "text.delta"))
    assert "Working." in streamed  # the prose before the fence is real and was shown


def test_sanitising_happens_before_the_client_sees_anything():
    leaky = [
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Sure.\n\n```tool_code\n"}}}) + "\n",
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "print(default_api.x())\n```\n\nDone."}}}) + "\n",
        json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n",
    ]
    evs = events(leaky)
    streamed = "".join(d["text"] for d in payloads(evs, "text.delta"))
    assert "tool_code" not in streamed and "default_api" not in streamed
    assert streamed == "Sure.\n\nDone."


def test_an_api_brain_has_no_cli_to_stream_and_falls_back_whole():
    class ApiBrain:
        kind = "api"
        model = "gemini-2.5-flash"

        def describe(self):
            return {"kind": "api", "model": self.model}

    evs = events(CLAUDE_PROSE, brain=ApiBrain())
    assert payloads(evs, "turn.end")[0]["reply_text"] == "fallback reply"
    assert payloads(evs, "text.delta"), "the fallback reply still arrives as one delta"


def test_every_event_is_serialisable_sse_with_a_named_event_and_json_data():
    for e in events(CLAUDE_PROSE):
        frame = TS.sse_frame(e)
        assert frame.startswith(f"event: {e['event']}\n")
        assert frame.endswith("\n\n")
        json.loads(frame.split("data: ", 1)[1].strip())
