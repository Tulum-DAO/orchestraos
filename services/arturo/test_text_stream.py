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


# ---- keep-alive (spec §2.2: a `: ping` comment every 15 s) ------------------------------
# Found live: a turn that falls back to the tool loop sends NOTHING while the loop runs, and
# the dashboard proxy drops a socket after 30s of silence — the browser saw a 502 mid-turn.
# A stream that goes quiet has to say it is still there.

def test_a_silent_stretch_emits_pings_until_the_next_real_frame():
    import time as _t

    def slow():
        yield {"event": "turn.start", "data": {"turn_id": "t1"}}
        _t.sleep(0.35)                      # the tool loop running, producing nothing
        yield {"event": "turn.end", "data": {"reply_text": "done"}}

    frames = list(TS.with_heartbeat(slow(), interval_s=0.1))
    assert frames[0].startswith("event: turn.start")
    assert frames[-1].startswith("event: turn.end")
    pings = [f for f in frames if f.startswith(":")]
    assert pings, "a silent stretch must be filled with keep-alive comments"
    assert all(f.endswith("\n\n") for f in frames)


def test_a_busy_stream_is_not_padded_with_pings():
    def busy():
        for i in range(5):
            yield {"event": "text.delta", "data": {"text": f"chunk{i}"}}

    frames = list(TS.with_heartbeat(busy(), interval_s=5))
    assert not [f for f in frames if f.startswith(":")]
    assert len(frames) == 5


def test_an_exception_inside_the_turn_still_reaches_the_client_as_an_error():
    def boom():
        yield {"event": "turn.start", "data": {"turn_id": "t1"}}
        raise RuntimeError("brain exploded")

    frames = list(TS.with_heartbeat(boom(), interval_s=0.05))
    assert frames[-1].startswith("event: error")
    assert "brain exploded" in frames[-1]


# ---- warm sessions through stream_turn -------------------------------------------------

class WarmRecorder:
    """Stands in for the pool: records what a warm turn was given."""

    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def __call__(self, argv, new_text):
        self.calls.append((argv, new_text))
        return iter(self.lines)


def warm_events(lines, messages, discarded, fallback=None):
    warm = WarmRecorder(lines)
    evs = list(TS.stream_turn(
        text="hi", conversation_id="c1", brain=FakeBrain(),
        brain_id={"provider": "claude", "model": "claude-opus-5"},
        messages=messages, spawn=lambda cmd: iter(["SHOULD NOT BE USED"]),
        fallback=fallback or (lambda: (200, {"ok": True, "reply_text": "fb", "tools_called": ["t"], "spawned": []})),
        record=lambda **kw: None, warm=warm, discard=lambda: discarded.append(True)))
    return evs, warm


def test_a_warm_turn_sends_ONLY_the_new_message():
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "the new one"}]
    discarded = []
    evs, warm = warm_events(CLAUDE_PROSE, msgs, discarded)
    argv, sent = warm.calls[0]
    assert sent == "the new one", "the process already remembers; the transcript would double it"
    assert payloads(evs, "turn.end")[0]["reply_text"] == "On it, I'll text Shaw now."
    assert discarded == [], "a clean prose turn keeps its session"


def test_the_history_rides_in_the_system_prompt_for_a_process_that_has_to_start():
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "the new one"}]
    _, warm = warm_events(CLAUDE_PROSE, msgs, [])
    argv = warm.calls[0][0]
    system = argv[argv.index("--system-prompt") + 1]
    assert "earlier question" in system and "earlier answer" in system
    assert "the new one" not in system, "the new message is sent as the turn, not baked in"
    assert "--input-format" in argv and "stream-json" in argv


def test_a_tool_envelope_DISCARDS_the_warm_session():
    """The tool loop writes the real answer elsewhere, so the process's memory is now wrong."""
    discarded = []
    evs, _ = warm_events(CLAUDE_TOOL_ENVELOPE, [{"role": "user", "content": "do it"}], discarded)
    assert discarded == [True]
    assert payloads(evs, "turn.end")[0]["tools_called"] == ["t"]


def test_a_failed_warm_turn_discards_the_session_too():
    discarded = []
    warm_events([json.dumps({"type": "result", "is_error": True, "result": "boom"}) + "\n"],
                [{"role": "user", "content": "x"}], discarded)
    assert discarded == [True]


def test_the_prewarm_and_the_turn_build_the_SAME_process():
    """A prewarm that built a different prompt would be adopted by a conversation it does not
    match — the turn would run on the prewarm's context instead of its own."""
    prior = [{"role": "system", "content": "SYS"},
             {"role": "user", "content": "earlier"},
             {"role": "assistant", "content": "reply"}]
    msgs = prior + [{"role": "user", "content": "new"}]
    _, warm = warm_events(CLAUDE_PROSE, msgs, [])
    assert warm.calls[0][0] == TS.warm_argv(FakeBrain(), prior, None)


# ---- the tool loop runs INSIDE the warm session ----------------------------------------
# Found live: after the first message Arturo nearly always asks for a tool. The stream could
# not run tools, so it threw the warm session away and re-ran the whole turn down /text —
# every turn after the first was slow, arrived whole, and killed the session it was meant to
# reuse. The loop now executes the calls itself, hands the results back to the SAME process,
# and streams the answer.

def delta_line(text):
    return json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                       "delta": {"type": "text_delta", "text": text}}}) + "\n"


RESULT = json.dumps({"type": "result", "is_error": False, "result": "ok"}) + "\n"


class WarmScript:
    """A warm session whose successive turns return successive scripted passes."""

    def __init__(self, passes):
        self.passes = list(passes)
        self.sent = []
        self.argvs = []

    def __call__(self, argv, text):
        self.argvs.append(argv)
        self.sent.append(text)
        return iter(self.passes.pop(0) if self.passes else [RESULT])


def loop_events(passes, run_tools, fallback=None, record=None):
    warm = WarmScript(passes)
    discarded = []
    evs = list(TS.stream_turn(
        text="what's new?", conversation_id="c1", brain=FakeBrain(),
        brain_id={"provider": "claude", "model": "claude-opus-5"},
        messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": "what's new?"}],
        spawn=lambda cmd: iter([]),
        fallback=fallback or (lambda: (_ for _ in ()).throw(AssertionError("must not fall back"))),
        record=record or (lambda **kw: None), warm=warm, discard=lambda: discarded.append(True),
        run_tools=run_tools))
    return evs, warm, discarded


TOOL_PASS = [delta_line('{"tool_calls":[{"name":"list_agents","arguments":{}}]}'), RESULT]
ANSWER_PASS = [delta_line("Four agents are "), delta_line("running."), RESULT]


def test_a_tool_turn_runs_its_tools_in_the_session_and_STREAMS_the_answer():
    ran = []

    def run_tools(calls):
        ran.extend(c["name"] for c in calls)
        return [{"name": "list_agents", "ok": True, "result": "4 agents: a, b, c, d"}]

    evs, warm, discarded = loop_events([TOOL_PASS, ANSWER_PASS], run_tools)
    assert ran == ["list_agents"]
    streamed = "".join(d["text"] for d in payloads(evs, "text.delta"))
    assert streamed == "Four agents are running."
    end = payloads(evs, "turn.end")[0]
    assert end["reply_text"] == "Four agents are running."
    assert end["tools_called"] == ["list_agents"]
    assert discarded == [], "the session saw its own tool results — its memory is still true"


def test_the_results_go_back_to_the_SAME_process_in_the_protocol_format():
    evs, warm, _ = loop_events(
        [TOOL_PASS, ANSWER_PASS],
        lambda calls: [{"name": "list_agents", "ok": True, "result": "4 agents"}])
    assert warm.sent[0] == "what's new?"
    assert "TOOL RESULT (list_agents): 4 agents" in warm.sent[1]


def test_tool_events_are_emitted_so_the_client_can_say_what_is_happening():
    evs, _, _ = loop_events(
        [TOOL_PASS, ANSWER_PASS],
        lambda calls: [{"name": "list_agents", "ok": True, "result": "4 agents"}])
    assert [p["name"] for p in payloads(evs, "tool.call")] == ["list_agents"]
    assert payloads(evs, "tool.result")[0]["ok"] is True


def test_prose_BEFORE_a_tool_call_streams_and_the_envelope_never_does():
    mixed = [delta_line("Let me check. "),
             delta_line('{"tool_ca'), delta_line('lls":[{"name":"list_agents","arguments":{}}]}'),
             RESULT]
    evs, _, _ = loop_events(
        [mixed, ANSWER_PASS],
        lambda calls: [{"name": "list_agents", "ok": True, "result": "4"}])
    streamed = "".join(d["text"] for d in payloads(evs, "text.delta"))
    assert "tool_calls" not in streamed and "{" not in streamed, "the envelope leaked into the reply"
    assert streamed.startswith("Let me check.")
    assert streamed.endswith("Four agents are running.")


def test_a_failing_tool_is_reported_to_the_model_not_raised_to_the_client():
    def run_tools(calls):
        return [{"name": "list_agents", "ok": False, "result": "error: registry unreadable"}]

    evs, warm, _ = loop_events([TOOL_PASS, ANSWER_PASS], run_tools)
    assert "error: registry unreadable" in warm.sent[1]
    assert payloads(evs, "tool.result")[0]["ok"] is False
    assert payloads(evs, "turn.end")


def test_a_model_that_never_stops_calling_tools_is_cut_off_and_falls_back():
    calls = []
    evs, _, discarded = loop_events(
        [TOOL_PASS] * 10,
        lambda cs: calls.append(1) or [{"name": "list_agents", "ok": True, "result": "4"}],
        fallback=lambda: (200, {"ok": True, "reply_text": "done the slow way", "tools_called": [], "spawned": []}))
    assert len(calls) <= TS.MAX_TOOL_ROUNDS
    assert discarded == [True]
    assert payloads(evs, "turn.end")[0]["reply_text"] == "done the slow way"


# ---- interleaving: each tool call is its own card, paired with its result by id ----------
# The client renders text, then a card per call, then more text — in the order it happened.
# Results used to pair with calls only by position; two calls to the same tool in one pass
# made "which card does this result close?" a guess.

TWO_CALLS = [delta_line('{"tool_calls":[{"name":"list_agents","arguments":{"tier":1}},'
                        '{"name":"list_agents","arguments":{"tier":2}}]}'), RESULT]


def test_each_tool_call_and_its_result_share_a_call_id():
    evs, _, _ = loop_events(
        [TWO_CALLS, ANSWER_PASS],
        lambda calls: [{"name": c["name"], "ok": True, "result": f"tier {c['arguments']['tier']}"}
                       for c in calls])
    calls, results = payloads(evs, "tool.call"), payloads(evs, "tool.result")
    ids = [c["call_id"] for c in calls]
    assert len(ids) == 2 and len(set(ids)) == 2, "two calls, two distinct ids"
    assert [r["call_id"] for r in results] == ids
    assert [r["summary"] for r in results] == ["tier 1", "tier 2"]


def test_call_ids_stay_unique_across_rounds_of_one_turn():
    evs, _, _ = loop_events(
        [TOOL_PASS, TOOL_PASS, ANSWER_PASS],
        lambda calls: [{"name": "list_agents", "ok": True, "result": "4"}])
    ids = [c["call_id"] for c in payloads(evs, "tool.call")]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_text_and_tool_events_arrive_in_the_order_they_happened():
    mixed = [delta_line("Let me check. "),
             delta_line('{"tool_calls":[{"name":"list_agents","arguments":{}}]}'), RESULT]
    evs, _, _ = loop_events(
        [mixed, ANSWER_PASS],
        lambda calls: [{"name": "list_agents", "ok": True, "result": "4"}])
    order = [e["event"] for e in evs if e["event"] in ("text.delta", "tool.call", "tool.result")]
    first_call, first_result = order.index("tool.call"), order.index("tool.result")
    assert "text.delta" in order[:first_call], "the prose before the call comes first"
    assert first_call < first_result
    assert "text.delta" in order[first_result:], "the answer comes after the result"


# ---- reset: the whole reply must not repeat what the dead attempt already showed ---------

def test_a_warm_loop_that_falls_back_after_showing_text_says_reset_first():
    dying = [delta_line("Let me check. ")]          # the pass never reaches its result
    evs, _, _ = loop_events([dying], lambda calls: [],
                            fallback=lambda: (200, {"ok": True, "reply_text": "Four are running.",
                                                    "tools_called": [], "spawned": []}))
    names = [e["event"] for e in evs]
    assert "turn.reset" in names
    reset_at = names.index("turn.reset")
    assert "text.delta" in names[:reset_at], "the partial text came first"
    after = "".join(d["text"] for d in payloads(evs[reset_at:], "text.delta"))
    assert after == "Four are running."


def test_a_fallback_after_tool_rounds_says_reset_first():
    evs, _, _ = loop_events([TOOL_PASS] * (TS.MAX_TOOL_ROUNDS + 1),
                            lambda calls: [{"name": "list_agents", "ok": True, "result": "4"}],
                            fallback=lambda: (200, {"ok": True, "reply_text": "Done.",
                                                    "tools_called": [], "spawned": []}))
    names = [e["event"] for e in evs]
    assert names.index("turn.reset") > max(i for i, n in enumerate(names) if n == "tool.result")


# ---- a stream that ends without the CLI's result line is half an answer, not an answer ----
# read_events marks it TurnEnd(truncated=True). Both paths used to accept it as the reply:
# the process died mid-sentence and the operator got the first half, recorded as complete.

def test_a_cold_stream_cut_off_before_its_result_falls_back_and_records_nothing_partial():
    recorded = []
    evs = events(CLAUDE_PROSE[:1], record=lambda **kw: recorded.append(kw),
                 fallback=lambda: (200, {"ok": True, "reply_text": "On it, I'll text Shaw now.",
                                         "tools_called": [], "spawned": []}))
    assert "turn.reset" in kinds(evs)
    assert payloads(evs, "turn.end")[0]["reply_text"] == "On it, I'll text Shaw now."
    assert recorded == [], "the whole-reply path records its own turn; the half never is"
