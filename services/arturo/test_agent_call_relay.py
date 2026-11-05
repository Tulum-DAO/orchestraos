"""Agent calls on GPT-Live: the relay, not GPT-Live, decides who answers (stream_relay AGENT_*).
In a live test GPT-Live answered every line of a call to the agent itself (all OPENAI-NONDELEGATED). Here: every
caller line reaches the agent once, and only the greeting and the agent's own sentences are heard."""
import threading
import time

import pytest

from services.arturo import stream_relay as sr
from services.arturo.test_agent_call import _FakeAC
from services.arturo.test_stream_relay_openai import _audio, _ev, _manager, _wait


class _AC(_FakeAC):
    def __init__(self, reply=("Two files.", "Both pass."), block=None, **kw):
        super().__init__(**kw)
        self.reply, self.block = reply, block

    def run_turn(self, seat, q, *, say, say_out_of_band, **kw):
        self.turns.append((seat, q))
        if self.block is not None:
            self.block.wait(5)
        for line in self.reply:
            say(line)
        return {"seat": seat, "ack": "silence", "spoken": " ".join(self.reply), "outcome": "answered"}


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(sr, "AGENT_FORCE_QUIET_S", 0.25)
    made = []

    def make(ac=None):
        ac = ac or _AC()
        journal = []
        m = _manager(journal=lambda cid, role, text, **meta: journal.append((role, text)))
        m.agent_calls = ac
        m.scrub = lambda t: t
        m.feed_audio("a1", b"\x00" * 3200, agent="dev-demo")
        assert _wait(lambda: m._t)
        h = m._holders["a1"]
        assert h.agent_mode
        made.append(m)
        return m, m._t[0], h, ac, journal
    yield make
    for m in made:
        m.shutdown()


def _say(s, words):
    for w in words.split():
        s.push({"type": "session.input_transcript.delta", "delta": " " + w})


def _speak(s, text, frames=3):
    s.push({"type": "session.output_transcript.delta", "delta": " " + text})
    for _ in range(frames):
        s.push(_audio(640))


def _quiet(s, n=10):
    for _ in range(n):
        s.push(_audio(0))


def _commentary(s):
    return [f["content"] for f in s.sent if f.get("type") == "session.commentary.append"]


def _texts(m):
    return " ".join(e.get("text") or "" for e in _ev(m, cid="a1", typ="agent_response"))


def test_gpt_lives_own_answer_is_muted_and_the_line_goes_to_the_agent(call):
    m, s, h, ac, journal = call()
    _say(s, "what changed")
    _speak(s, "I'm Marin, how can I help?")       # GPT-Live answering itself
    _quiet(s)
    assert _wait(lambda: ac.turns == [("dev-demo", "what changed")])
    assert _wait(lambda: _commentary(s) == ["Two files.", "Both pass."])
    assert all(f["delegation_id"] is None for f in s.sent if f.get("type") == "session.commentary.append")
    assert _ev(m, cid="a1", typ="audio") == [] and "Marin" not in _texts(m)
    _speak(s, "Two files. Both pass.")             # GPT-Live reading the agent's words: heard
    assert _wait(lambda: len(_ev(m, cid="a1", typ="audio")) == 3)
    _quiet(s)
    assert _wait(lambda: "Two files. Both pass." in _texts(m))
    assert not any("Marin" in t for _, t in journal)
    assert ("user", "what changed") in journal and ("assistant", "Two files. Both pass.") in journal


def test_the_greeting_is_heard_when_it_comes_first(call):
    m, s, h, ac, journal = call()
    _speak(s, "Hey, dev-demo here.")
    assert _wait(lambda: len(_ev(m, cid="a1", typ="audio")) == 3)
    _quiet(s)
    time.sleep(0.4)
    assert ac.turns == []


def test_a_greeting_after_the_caller_spoke_first_is_muted_and_their_line_still_goes(call):
    m, s, h, ac, journal = call()
    _say(s, "hello there")
    _speak(s, "Hi! What can I do for you?")
    _quiet(s)
    assert _wait(lambda: ac.turns == [("dev-demo", "hello there")])
    assert _ev(m, cid="a1", typ="audio") == []


def test_a_delegation_and_the_timer_send_the_line_once(call, monkeypatch):
    m, s, h, ac, journal = call()
    _say(s, "run the tests")
    s.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
    assert _wait(lambda: ac.turns == [("dev-demo", "run the tests")])
    time.sleep(0.6)                                # past the force wait
    assert ac.turns == [("dev-demo", "run the tests")]
    assert [f["delegation_id"] for f in s.sent if f.get("type") == "session.commentary.append"] == ["d1", "d1"]
    _say(s, "and the build")
    assert _wait(lambda: len(ac.turns) == 2)       # the timer first ...
    s.push({"type": "session.delegation.created", "delegation": {"id": "d2"}})
    time.sleep(0.3)
    assert ac.turns[1] == ("dev-demo", "and the build") and len(ac.turns) == 2   # ... then an empty delegation: nothing


def test_talking_over_a_muted_answer_is_no_barge_in_and_the_whole_line_goes(call):
    m, s, h, ac, journal = call()
    _say(s, "wait")
    _speak(s, "Sure, I can help with")
    _say(s, "actually check the logs")
    _quiet(s)
    assert _wait(lambda: len(ac.turns) >= 1)
    assert _ev(m, cid="a1", typ="barge_in") == []
    assert " ".join(q for _, q in ac.turns) == "wait actually check the logs"


def test_a_self_answer_during_a_long_agent_turn_is_muted(call):
    gate = threading.Event()
    m, s, h, ac, journal = call(_AC(block=gate))
    _say(s, "deploy it")
    assert _wait(lambda: ac.turns == [("dev-demo", "deploy it")])
    _speak(s, "Deploying now, it's all done!")     # GPT-Live making it up while the agent works
    _quiet(s)
    time.sleep(0.3)
    assert _ev(m, cid="a1", typ="audio") == [] and "Deploying" not in _texts(m)
    gate.set()
    assert _wait(lambda: _commentary(s) == ["Two files.", "Both pass."])


def test_a_line_said_over_the_agents_reply_reaches_the_agent(call):
    m, s, h, ac, journal = call()
    _say(s, "status")
    assert _wait(lambda: len(_commentary(s)) == 2)
    _speak(s, "Two files. Both pass.")
    assert _wait(lambda: len(_ev(m, cid="a1", typ="audio")) == 3)
    _say(s, "and the docs")
    _quiet(s)
    assert _wait(lambda: ac.turns[-1] == ("dev-demo", "and the docs"))


def test_the_end_of_the_call_commits_the_last_line_once_and_never_forces_it(call):
    m, s, h, ac, journal = call()
    _say(s, "one more thing")
    assert _wait(lambda: h._oa_user.strip() == "one more thing")
    h.flush_at_end()
    time.sleep(0.5)
    assert ac.turns == []


def test_a_call_that_is_not_an_agents_still_hears_gpt_live():
    m = _manager()
    try:
        m.feed_audio("o1", b"\x00" * 3200)
        assert _wait(lambda: m._t)
        s = m._t[0]
        assert not m._holders["o1"].agent_mode
        _say(s, "hello")
        _speak(s, "Hi there!")
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
    finally:
        m.shutdown()


def _n_audio(m):
    return len(_ev(m, cid="a1", typ="audio"))


def test_the_gate_is_decided_afresh_after_an_overlap_span_or_a_barge_in(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_gate, h._oa_span_open, h._oa_over = "agent", True, {"audio_s": 0.0}
    h._oa_end_span()                               # the overlap branch returns early: the gate still resets
    assert h._oa_gate is None
    h._oa_gate = "agent"
    h._oa_barge_in()
    assert h._oa_gate is None
    _speak(s, "Let me make something up.")         # the next self-answer is muted
    _quiet(s)
    time.sleep(0.3)
    assert _n_audio(m) == 0


def test_an_agent_sentence_that_begins_inside_a_muted_utterance_is_heard(call):
    m, s, h, ac, journal = call(_AC(reply=("Two files changed today.", "Both test suites pass.")))
    _say(s, "status")
    assert _wait(lambda: len(_commentary(s)) == 2)
    _speak(s, "Sure thing let me see here")        # its own words: muted
    time.sleep(0.2)
    assert _n_audio(m) == 0
    _speak(s, "Two files changed today.")           # then the agent's sentence, same utterance: heard from here
    assert _wait(lambda: _n_audio(m) == 3)


def test_a_read_out_with_a_short_lead_in_is_heard(call):
    m, s, h, ac, journal = call(_AC(reply=("Two files changed today.", "Both test suites pass.")))
    _say(s, "status")
    assert _wait(lambda: len(_commentary(s)) == 2)
    _speak(s, "Okay so two files changed today.")
    assert _wait(lambda: _n_audio(m) == 3)


def test_sentences_never_read_out_do_not_block_newer_ones(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    for i in range(6):
        h._oa_said_push(f"Stale line number {i} here.")
    h._oa_said_push("Fresh words now.")
    _speak(s, "Fresh words now.")
    assert _wait(lambda: _n_audio(m) == 3)
    assert h._oa_said == []


def test_a_wordless_start_plays_provisionally_and_is_muted_once_its_words_are_not_the_agents(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_said_push("Two files.")
    s.push(_audio(640))
    time.sleep(0.75)                               # past AGENT_HOLD_MAX_S with no words yet
    s.push(_audio(640))
    assert _wait(lambda: _n_audio(m) == 2)          # played provisionally
    s.push({"type": "session.output_transcript.delta", "delta": " Deploying now, all done here."})
    for _ in range(3):
        s.push(_audio(640))
    time.sleep(0.3)
    assert _n_audio(m) == 2                         # muted from its words on
    assert len(h._oa_said) == 1                     # and the agent's sentence is still waiting to be read
    _quiet(s)
    time.sleep(0.2)
    n = _n_audio(m)
    assert n == 2, "the muted utterance's quiet tail is not forwarded either"
    _speak(s, "Two files.")
    assert _wait(lambda: _n_audio(m) == 5)


def test_an_empty_delegation_leaves_no_trace(call):
    m, s, h, ac, journal = call()
    turn, hist = h._turn_no, list(h._oa_hist)
    s.push({"type": "session.delegation.created", "delegation": {"id": "dx"}})
    time.sleep(0.3)
    assert h._turn_no == turn and h._oa_hist == hist and ac.turns == []


def test_a_provisional_start_that_is_not_the_agents_shows_none_of_its_words_and_closes_its_span(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_said_push("Two files changed today.")
    s.push(_audio(640))
    time.sleep(0.75)
    s.push(_audio(640))
    assert _wait(lambda: _n_audio(m) == 2)
    s.push({"type": "session.output_transcript.delta", "delta": " Deploying"})
    s.push({"type": "session.output_transcript.delta", "delta": " now, all done here."})
    time.sleep(0.3)
    assert "Deploying" not in _texts(m) and not any("Deploying" in t for _, t in journal)
    assert not h._oa_span_open and h._oa_gate == "muted"
    assert len(h._oa_said) == 1


def test_gpt_lives_own_filler_never_passes_for_a_short_or_stale_ack(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_said_push("Let me check.")
    _speak(s, "Sure, let me check that for you.")   # a short ack must open the utterance
    _quiet(s)
    time.sleep(0.3)
    assert _n_audio(m) == 0
    h._oa_said = [(time.time() - 60, ["one", "sec"])]
    _speak(s, "One sec, checking.")                 # a stale short ack never matches
    _quiet(s)
    time.sleep(0.3)
    assert _n_audio(m) == 0


def test_a_barge_in_mid_span_then_a_muted_utterance_leaves_no_span_open(call):
    m, s, h, ac, journal = call()
    _say(s, "status")
    assert _wait(lambda: len(_commentary(s)) == 2)
    _speak(s, "Two files. Both pass.")
    assert _wait(lambda: h._oa_span_open)
    h._oa_barge_in()                                # resets the gate with the span still open
    time.sleep(0.6)                                 # past the talkover hold
    _speak(s, "Something of my own to add here now.")
    _quiet(s)
    assert _wait(lambda: not h._oa_span_open)


def test_an_unmuted_read_out_is_shown_from_where_the_agents_sentence_starts(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_said_push("Two files changed in the repo today.")
    _speak(s, "Hmm well I am not sure about")
    time.sleep(0.2)
    _speak(s, "this but two files changed in the repo today.")
    _quiet(s)
    assert _wait(lambda: "two files changed in the repo today." in _texts(m))
    def finals():
        return [e["text"] for e in _ev(m, cid="a1", typ="agent_response") if e.get("partial") is False]
    assert _wait(lambda: finals())
    assert finals()[-1].startswith("two files") and "sure" not in finals()[-1]


def test_a_short_self_answer_during_an_agent_turn_is_muted_before_any_audio(call):
    m, s, h, ac, journal = call()
    h._oa_heard_caller, h._oa_spans = True, 1
    h._oa_said_push("Two files changed today.")
    s.push({"type": "session.output_transcript.delta", "delta": " Got it."})
    for _ in range(3):
        s.push(_audio(640))
    _quiet(s)
    time.sleep(0.3)
    assert _n_audio(m) == 0 and h._oa_said and "Got it" not in _texts(m)
