"""GPT-Live ("openai") vendor path of the relay (DEC-1791605997016955 rev 1; gm conditions 1-5).
FakeOpenAISocket replays the event SHAPES measured in the 2026-10-10 scratch session. No network."""
import base64
import json
import logging
import threading
import time

import numpy as np

from services.arturo import stream_relay as sr
from services.arturo.test_stream_relay_hume import _wait


class FakeOpenAISocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        if self.closed:
            raise RuntimeError("down")
        self.sent.append(json.loads(payload))

    def push(self, ev):
        with self._cv:
            self._incoming.append(json.dumps(ev))
            self._cv.notify()

    def recv(self):
        with self._cv:
            while not self._incoming and not self.closed:
                self._cv.wait(timeout=0.1)
            if self.closed and not self._incoming:
                raise RuntimeError("closed")
            return self._incoming.pop(0)

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


def _manager(delegate=None, journal=None, **kw):
    socks = []

    def factory(cid, **_):
        s = FakeOpenAISocket()
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150, **kw)
    m.openai_delegate = delegate
    m.journal_hook = journal
    m._t = socks
    return m


def _frame(rms, n=1600, seed=1):
    if not rms:
        return base64.b64encode(b"\x00" * (n * 2)).decode()
    x = np.random.default_rng(seed).standard_normal(n)
    x = np.clip(np.rint(x / np.sqrt((x * x).mean()) * rms), -32768, 32767).astype("<i2")
    return base64.b64encode(x.tobytes()).decode()


def _audio(rms):
    return {"type": "session.output_audio.delta", "delta": _frame(rms)}


def _live(m, cid="o1"):
    m.feed_audio(cid, b"\x00" * 3200)
    assert _wait(lambda: m._t)
    return m._t[0], m._holders[cid]


def _ev(m, cid="o1", typ=None):
    evs = m.events(cid, 0)[0]
    return [e for e in evs if typ is None or e["type"] == typ]


def test_uplink_is_session_input_audio_append():
    m = _manager()
    try:
        s, h = _live(m)
        assert _wait(lambda: any(f.get("type") == "session.input_audio.append" for f in s.sent))
    finally:
        m.shutdown()


def test_silence_and_dither_are_never_speech_or_forwarded():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(10):
            s.push(_audio(0))
            s.push(_audio(3))
        time.sleep(0.4)
        assert _ev(m, typ="audio") == [], "silence frames must not reach the app (the watch would gate its mic forever)"
        assert not any(e.get("speaking") for e in _ev(m, typ="agent_speaking"))
        assert h._last_agent_audio_ts == 0.0
    finally:
        m.shutdown()


def test_speech_opens_a_span_and_the_quiet_tail_closes_it_with_the_reply_text():
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "session.output_transcript.delta", "delta": " Hey!"})
        s.push({"type": "session.output_transcript.delta", "delta": " How's your night?"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        assert any(e.get("speaking") for e in _ev(m, typ="agent_speaking"))
        assert h._last_agent_audio_ts > 0
        for _ in range(15):                          # 1.5 s of exact zero: past the span tail
            s.push(_audio(0))
        assert _wait(lambda: _ev(m, typ="assistant_end"))
        finals = [e for e in _ev(m, typ="agent_response") if e.get("partial") is False]
        assert finals and finals[-1]["text"] == "Hey! How's your night?"
        n_audio = len(_ev(m, typ="audio"))
        for _ in range(5):
            s.push(_audio(0))
        time.sleep(0.3)
        assert len(_ev(m, typ="audio")) == n_audio, "zero frames after the span are not forwarded"
    finally:
        m.shutdown()


def test_user_speech_during_arturo_speech_is_a_barge_in():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " Hey"})
        s.push({"type": "session.input_transcript.delta", "delta": " wait"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        assert len(_ev(m, typ="barge_in")) == 1, "one barge_in per interrupted span"
        evs = _ev(m)
        i_b = max(i for i, e in enumerate(evs) if e["type"] == "barge_in")
        assert any(e["type"] == "agent_speaking" and e["speaking"] is False for e in evs[:i_b])
    finally:
        m.shutdown()


def test_delegation_goes_to_our_brain_and_comes_back_as_commentary():
    calls = []

    def delegate(cid, messages):
        calls.append((cid, messages))
        return "gm is reviewing the restart card."
    m = _manager(delegate=delegate)
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " What is GM"})
        s.push({"type": "session.input_transcript.delta", "delta": " working on right now"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_A", "type": "delegation",
                                                                     "target": "client"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        c = [f for f in s.sent if f.get("type") == "session.commentary.append"][0]
        assert c["delegation_id"] == "item_A" and c["content"] == "gm is reviewing the restart card."
        assert calls[0][0] == "o1"
        assert calls[0][1][-1] == {"role": "user", "content": "What is GM working on right now"}
        assert any(e["type"] == "user_transcript" and e["text"] == "What is GM working on right now"
                   for e in _ev(m))
    finally:
        m.shutdown()


def test_no_backend_answer_is_spoken_as_an_explicit_nothing_found():
    m = _manager(delegate=lambda cid, msgs: "")
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " anything new"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_B"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        c = [f for f in s.sent if f.get("type") == "session.commentary.append"][0]
        assert c["content"] == "I couldn't find anything on that."
    finally:
        m.shutdown()


def test_an_interrupted_answer_is_posted_to_the_transcript_never_lost():
    m = _manager(delegate=lambda cid, msgs: "dev-acme has three open tasks.")
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " what is OB doing"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_C"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        for _ in range(3):
            s.push(_audio(640))                      # Arturo starts speaking the answer
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " hold on"})
        assert _wait(lambda: any("dev-acme has three open tasks." in (e.get("text") or "")
                                 and e.get("interrupted") for e in _ev(m, typ="agent_response")))
    finally:
        m.shutdown()


def test_per_call_cap_warns_then_closes(monkeypatch):
    monkeypatch.setattr(sr, "OPENAI_CALL_WARN_S", 10.0)
    monkeypatch.setattr(sr, "OPENAI_CALL_CAP_S", 20.0)
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "session.usage.updated", "usage": {"seconds": 11.0}})
        s.push({"type": "session.usage.updated", "usage": {"seconds": 12.0}})
        assert _wait(lambda: any("minute left" in (e.get("text") or "") for e in _ev(m, typ="agent_response")))
        assert len([e for e in _ev(m, typ="agent_response") if "minute left" in (e.get("text") or "")]) == 1
        s.push({"type": "session.usage.updated", "usage": {"seconds": 21.0}})
        assert _wait(lambda: any(f.get("type") == "session.close" for f in s.sent))
        assert any(e["type"] == "vendor_unavailable" for e in _ev(m))
    finally:
        m.shutdown()


def test_a_turn_answered_without_delegating_is_journaled_and_counted(caplog):
    caplog.set_level(logging.INFO, logger="arturo-stream-relay")
    seen = []
    m = _manager(journal=lambda cid, role, text, **meta: seen.append((cid, role, text, meta)))
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " how's it going"})
        s.push({"type": "session.output_transcript.delta", "delta": " I'm on it."})
        for _ in range(2):
            s.push(_audio(640))
        for _ in range(15):
            s.push(_audio(0))
        assert _wait(lambda: any(r == "arturo" for _, r, _, _ in seen))
        assert ("o1", "user", "how's it going", {"delegated": False}) in seen
        assert any(r == "arturo" and t == "I'm on it." and meta == {"delegated": False} for _, r, t, meta in seen)
        assert h.oa_nondelegated == 1
        assert "OPENAI-ACK" in caplog.text
        # the caller's line reaches the client as a FINAL too, before Arturo's reply is finalized
        evs = _ev(m)
        finals = [e for e in evs if e["type"] == "user_transcript"]
        assert [e["text"] for e in finals] == ["how's it going"]
        reply = [i for i, e in enumerate(evs) if e["type"] == "agent_response" and not e.get("partial")]
        assert reply and evs.index(finals[0]) < reply[0]
    finally:
        m.shutdown()


def test_supersede_and_async_hold_gates_are_open_for_openai():
    m = _manager()
    try:
        s, h = _live(m)
        assert m.note_clm_request("o1", time.time(), "what is gm doing") is True
        t = [1e9]

        def clock():
            return t[0]

        def sleep(d):
            t[0] += d
        assert m.wait_turn_settled("o1", time.time(), clock=clock, sleep=sleep) != "gone"
    finally:
        m.shutdown()


def test_user_speech_while_arturo_is_silent_is_not_a_barge_in():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(5):
            s.push(_audio(0))                        # GPT-Live streaming its silent frames
        s.push({"type": "session.input_transcript.delta", "delta": " What is GM working on"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        time.sleep(0.2)
        assert _ev(m, typ="barge_in") == [], "a normal turn must never flush the client's playback"
    finally:
        m.shutdown()


def test_user_first_words_are_never_lost_and_a_greeting_yields_to_them():
    """gm msg_6d4f93d7 (the operator: 'the first turn comes from the user and is nullified'): the operator talking BEFORE Arturo's
    first audio must not lose their words. If a greeting starts anyway and they keep talking, it yields (barge_in)
    and the delegation carries EVERYTHING he said, before and during the greeting."""
    got = []
    m = _manager(delegate=lambda cid, msgs: (got.append(msgs[-1]["content"]), "Here's what I found.")[1])
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " Okay so"})          # user first, Arturo silent
        assert _wait(lambda: _ev(m, typ="user_partial"))
        assert _ev(m, typ="barge_in") == []
        for _ in range(3):
            s.push(_audio(640))                                                          # a greeting starts anyway
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " what is gm doing"})  # they keep talking
        assert _wait(lambda: _ev(m, typ="barge_in")), "the greeting must yield to them"
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_U"}})
        assert _wait(lambda: got)
        assert got[0] == "Okay so what is gm doing", f"their first words were lost: {got[0]!r}"
    finally:
        m.shutdown()


def test_a_stale_send_failure_never_retires_the_socket_that_replaced_it():
    """Soak 2026-10-10 01:09 ET: after a socket died, the uplink send that was in flight on the DEAD socket failed
    later (10 s timeout) and feed() retired whatever socket was CURRENT: the healthy replacement. One drop became
    two reconnects. A send failure may only retire the socket it was sent on."""
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "session.started"})            # a healthy session (a frameless death trips the cooldown)
        assert _wait(lambda: h._got_frame)

        def send_after_replacement(payload):
            h._retire_socket("socket_down", reconnect=True, detail="reader saw it first")
            assert _wait(lambda: h.sock is not None and h.sock is not s)
            raise ConnectionError("openai live: socket closed")
        s.send = send_after_replacement
        m.feed_audio("o1", b"\x00" * 3200)
        time.sleep(0.3)
        assert len(m._t) == 2, f"the replacement socket was retired too: {len(m._t)} sockets opened"
        assert h.sock is m._t[1] and not m._t[1].closed
    finally:
        m.shutdown()


def test_interrupting_a_self_answered_reply_keeps_the_two_lines_apart(monkeypatch):
    """A user report: 'I want to try interrupting you' showed in place until they cut in, then was
    prepended to the interrupting line. The line Arturo was answering ends at the barge-in; the new words start
    a fresh line, and only that one is delegated."""
    monkeypatch.setattr(sr, "OPENAI_REPLY_MIN_S", 0.2)
    got = []
    m = _manager(delegate=lambda cid, msgs: (got.append(msgs[-1]["content"]), "Go ahead.")[1])
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " I want to try interrupting you"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        time.sleep(0.05)
        s.push({"type": "session.output_transcript.delta", "delta": " Sure, when testing interruptions"})
        s.push(_audio(640))
        assert _wait(lambda: _ev(m, typ="audio"))
        time.sleep(0.3)                                                   # Arturo has been replying a while
        s.push(_audio(640))
        s.push({"type": "session.input_transcript.delta", "delta": " I'm interrupting you"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_I"}})
        assert _wait(lambda: got)
        assert got[0] == "I'm interrupting you"
        evs = _ev(m)
        finals = [e["text"] for e in evs if e["type"] == "user_transcript"]
        assert finals == ["I want to try interrupting you", "I'm interrupting you"]
        first = next(i for i, e in enumerate(evs) if e["type"] == "user_transcript")
        cut = next(i for i, e in enumerate(evs) if e["type"] == "agent_response" and not e.get("partial"))
        assert first < cut, "the interrupted line comes before Arturo's cut-off reply"
    finally:
        m.shutdown()


def _live_web(m, cid="o1"):
    m.feed_audio(cid, b"\x00" * 3200, surface_device="web")
    assert _wait(lambda: m._t)
    return m._t[0], m._holders[cid]


def test_on_the_dashboard_a_cut_off_answer_ends_at_the_last_word_with_dashes(monkeypatch):
    """A user report: no '(You cut in before I finished)' + the whole answer; '--' where he stopped."""
    monkeypatch.setattr(sr, "heard_part", lambda text, *a: text)     # all of it heard (the cut point: below)
    seen = []
    m = _manager(delegate=lambda cid, msgs: "dev-acme has three open tasks.",
                 journal=lambda cid, role, text, **meta: seen.append((role, text, meta)))
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " what is OB doing"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_C"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        s.push({"type": "session.output_transcript.delta", "delta": " dev-acme has three,"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " hold on"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        texts = [e.get("text") for e in _ev(m, typ="agent_response") if not e.get("partial")]
        assert "dev-acme has three--" in texts
        assert not any("cut in" in (t or "") for t in texts)
        assert _wait(lambda: any(r == "arturo" and meta.get("interrupted") for r, _, meta in seen))   # journal keeps it
    finally:
        m.shutdown()


def test_an_interrupting_line_survives_arturo_talking_through_it(monkeypatch):
    """A user report: GPT-Live kept talking over an interruption and the interrupting line was never
    committed. When his span finally ends and they have gone quiet, it lands, before his continuation."""
    monkeypatch.setattr(sr, "OPENAI_USER_QUIET_S", 0.1)
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 0.1)
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.output_transcript.delta", "delta": " Your starter team is up"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " Mm-hmm"})          # the interruption
        assert _wait(lambda: _ev(m, typ="barge_in"))
        s.push({"type": "session.output_transcript.delta", "delta": " and two issues are open."})
        for _ in range(3):
            s.push(_audio(640))                                                          # he talks on
        time.sleep(0.2)                                                                  # they are quiet
        for _ in range(15):
            s.push(_audio(0))                                                            # his span ends
        assert _wait(lambda: _ev(m, typ="user_transcript"))
        evs = _ev(m)
        assert [e["text"] for e in evs if e["type"] == "user_transcript"] == ["Mm-hmm"]
        # what he said over them was never played, so it is not shown either (2026-10-24)
        assert not any("two issues" in (e.get("text") or "") for e in evs if e["type"] == "agent_response")
    finally:
        m.shutdown()


def test_the_callers_line_is_final_when_arturo_starts_replying_not_when_he_finishes(monkeypatch):
    """A user report: their line stayed live ('...', faded) until Arturo FINISHED, then both went final
    together. It goes final the moment he starts; the journal still pairs it with his reply."""
    monkeypatch.setattr(sr, "OPENAI_USER_TURN_GAP_S", 0.1)
    seen = []
    m = _manager(journal=lambda cid, role, text, **meta: seen.append((role, text, meta)))
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " Can you hear me okay"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        time.sleep(0.2)                                                       # they stopped talking
        s.push({"type": "session.output_transcript.delta", "delta": " Yeah, loud and clear."})
        s.push(_audio(640))                                                   # Arturo starts
        assert _wait(lambda: _ev(m, typ="user_transcript"))
        assert _ev(m, typ="assistant_end") == [], "final while Arturo is still speaking"
        for _ in range(15):
            s.push(_audio(0))                                                 # he finishes
        assert _wait(lambda: _ev(m, typ="assistant_end"))
        assert [e["text"] for e in _ev(m, typ="user_transcript")] == ["Can you hear me okay"]
        assert ("user", "Can you hear me okay", {"delegated": False}) in seen
        assert ("arturo", "Yeah, loud and clear.", {"delegated": False}) in seen
    finally:
        m.shutdown()


def test_a_line_shown_early_is_still_the_delegated_question(monkeypatch):
    monkeypatch.setattr(sr, "OPENAI_USER_TURN_GAP_S", 0.1)
    got = []
    m = _manager(delegate=lambda cid, msgs: (got.append(msgs[-1]["content"]), "Three tasks.")[1])
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " What is OB doing"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        time.sleep(0.2)
        s.push({"type": "session.output_transcript.delta", "delta": " Let me check."})
        s.push(_audio(640))                                                   # a filler first
        assert _wait(lambda: _ev(m, typ="user_transcript"))
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_E"}})
        assert _wait(lambda: got)
        assert got[0] == "What is OB doing"
        assert [e["text"] for e in _ev(m, typ="user_transcript")] == ["What is OB doing"]
    finally:
        m.shutdown()


def _greet(m, s):
    s.push(_audio(640))
    for _ in range(15):
        s.push(_audio(0))
    assert _wait(lambda: _ev(m, typ="assistant_end"))


def test_after_the_greeting_a_reply_ends_their_turn_even_with_no_pause():
    """2026-10-23 log: their last words reached us with his first, so a quiet-gap rule left the line live ~4 s."""
    m = _manager()
    try:
        s, h = _live_web(m)
        _greet(m, s)
        s.push({"type": "session.input_transcript.delta", "delta": " give me a longer phrase"})
        s.push({"type": "session.output_transcript.delta", "delta": " Sure. Imagine"})   # no gap at all
        assert _wait(lambda: _ev(m, typ="user_transcript"))
        assert [e["text"] for e in _ev(m, typ="user_transcript")] == ["give me a longer phrase"]
        assert len(_ev(m, typ="assistant_end")) == 1, "only the greeting has ended"
    finally:
        m.shutdown()


def test_a_reply_that_runs_on_after_a_barge_in_ends_the_interrupting_turn(monkeypatch):
    """2026-10-23: 'And I'm interrupting' stayed live until 'Got it. I'll stop there.' had finished."""
    monkeypatch.setattr(sr, "OPENAI_USER_TURN_GAP_S", 0.1)
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 0.1)
    m = _manager()
    try:
        s, h = _live_web(m)
        _greet(m, s)
        s.push({"type": "session.input_transcript.delta", "delta": " give me a longer phrase"})
        s.push({"type": "session.output_transcript.delta", "delta": " Imagine a rainy street,"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="user_transcript")) == 1)
        s.push({"type": "session.input_transcript.delta", "delta": " And I'm interrupting"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        time.sleep(0.2)                                                        # they finish
        s.push({"type": "session.output_transcript.delta", "delta": " Got it."})   # his new reply, same span
        s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="user_transcript")) == 2)
        assert _ev(m, typ="user_transcript")[1]["text"] == "And I'm interrupting"
        assert len(_ev(m, typ="assistant_end")) == 1, "final before his reply ends"
    finally:
        m.shutdown()


def test_speak_waits_for_a_pause_and_tags_its_read_out_as_echo():
    m = _manager()
    try:
        s, h = _live(m)
        h._oa_user = "I'm still talking"                  # the caller is mid-sentence: nothing is volunteered
        assert m.speak("o1", "Here's what you typed about.", note="The operator TYPED: hi")
        time.sleep(0.5)
        assert not any(f.get("type") == "session.commentary.append" for f in s.sent)
        h._oa_user, h._last_user_ts = "", 0.0             # a pause
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        note, say = [f for f in s.sent if f.get("type", "").startswith("session.") and "append" in f["type"]
                     and f["type"] != "session.input_audio.append"][:2]
        assert note == {"type": "session.thinking.append", "delegation_id": None, "content": "The operator TYPED: hi"}
        assert say["delegation_id"] is None and say["content"] == "Here's what you typed about."
        # its spoken span is a read-out: tagged echo (not a new line in the thread or on screen)
        s.push({"type": "session.output_transcript.delta", "delta": " So, about that,"})
        for _ in range(3):
            s.push(_audio(640))
        for _ in range(15):
            s.push(_audio(0))
        assert _wait(lambda: _ev(m, typ="assistant_end"))
        ar = _ev(m, typ="agent_response")
        assert ar and all(e.get("echo") for e in ar)
        # the next span is an ordinary one again
        s.push({"type": "session.output_transcript.delta", "delta": " Anything else?"})
        for _ in range(3):
            s.push(_audio(640))
        for _ in range(15):
            s.push(_audio(0))
        assert _wait(lambda: len(_ev(m, typ="assistant_end")) == 2)
        last = [e for e in _ev(m, typ="agent_response") if e.get("partial") is False][-1]
        assert last["text"] == "Anything else?" and not last.get("echo")
    finally:
        m.shutdown()


def test_speak_is_gpt_live_only_and_needs_a_live_call():
    m = _manager()
    try:
        assert not m.speak("nobody", "hello")
        _live(m)
        assert not m.speak("o1", "   ")
    finally:
        m.shutdown()


def test_the_cut_off_line_ends_where_the_caller_stopped_hearing_not_where_the_text_got_to():
    """A user report: "your text moves faster than your voice ... you registered the interruption up to
    the last word that's written." Audio arrives faster than it plays: the line is cut at what was HEARD."""
    text = "one two three four five six seven eight nine ten"
    # 4 s of audio sent for these words, 1 s of it played: about a quarter heard
    assert sr.heard_part(text, 100.0, 4 * sr.BYTES_PER_S, 101.0) == "one two three"
    assert sr.heard_part(text, 100.0, 4 * sr.BYTES_PER_S, 109.0) == text          # all played
    assert sr.heard_part(text, 100.0, 4 * sr.BYTES_PER_S, 100.0) == "one"         # never empty
    assert sr.heard_part(text, None, 0, 100.0) == text


def test_arturo_talking_over_the_caller_after_they_cut_in_is_never_played(monkeypatch):
    """A user report: "Wait, I interrupted you. Why are you still talking?" GPT-Live talked on through
    the interruption; that talk-over is held back until the caller is quiet, and what follows plays."""
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 0.3)
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.output_transcript.delta", "delta": " The grade came back"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " no wait"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        s.push({"type": "session.output_transcript.delta", "delta": " and the biggest fix is"})
        for _ in range(4):
            s.push(_audio(640))                                                   # he talks on, over them
        time.sleep(0.15)
        assert len(_ev(m, typ="audio")) == 3                                      # none of it reached the page
        assert not any("biggest fix" in (e.get("text") or "") for e in _ev(m, typ="agent_response"))
        time.sleep(0.35)                                                          # they stopped talking
        s.push({"type": "session.output_transcript.delta", "delta": " Sorry, go ahead."})
        s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 4)                       # his reply plays
    finally:
        m.shutdown()


def test_a_backchannel_while_the_caller_talks_plays_and_breaks_their_line(monkeypatch):
    """A user report: he should still be able to say "hmm", "okay", "uh huh" (naturally, any short
    words) while they talk, and it breaks their sentence on screen: their words so far, his, then theirs."""
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 0.1)
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " so what I was thinking is"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        h._uplink_speech_ts = time.time()                                   # they are mid-line
        s.push({"type": "session.output_transcript.delta", "delta": " Mm-hmm."})
        s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 1)                 # it plays
        assert [e["text"] for e in _ev(m, typ="user_transcript")] == ["so what I was thinking is"]
        s.push({"type": "session.input_transcript.delta", "delta": " we spawn a QA agent"})
        time.sleep(0.05)
        assert not _ev(m, typ="barge_in")                                   # talking over it is not cutting him off
        for _ in range(15):
            s.push(_audio(0))                                               # his "mm-hmm" ends: its own line
        assert _wait(lambda: any(e.get("text") == "Mm-hmm." and not e.get("partial")
                                 for e in _ev(m, typ="agent_response")))
        assert h._oa_user.strip() == "we spawn a QA agent"                  # their next words: a new line
    finally:
        m.shutdown()


def test_words_still_being_said_when_the_call_ends_are_kept(monkeypatch):
    """2026-10-24: the operator's last sentence was on screen as a live caption and vanished when the call ended."""
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " okay all right cool"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        seen = []
        put = m.buffer.put
        m.buffer.put = lambda cid, ev: (seen.append(ev), put(cid, ev))
        m.end("o1")
        assert [e["text"] for e in seen if e.get("type") == "user_transcript"] == ["okay all right cool"]
    finally:
        m.shutdown()


def test_a_long_overlap_is_talking_over_them_and_is_cut(monkeypatch):
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 5.0)
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.input_transcript.delta", "delta": " so what I was thinking"})
        h._uplink_speech_ts = time.time()
        s.push({"type": "session.output_transcript.delta", "delta": " Right, and also the report says the quote"})
        s.push(_audio(640))
        assert _wait(lambda: _ev(m, typ="barge_in"))                        # past a backchannel: cut at once
        n = len(_ev(m, typ="audio"))
        for _ in range(3):
            s.push(_audio(640))
        time.sleep(0.15)
        assert len(_ev(m, typ="audio")) == n                                # and the rest of it never plays
    finally:
        m.shutdown()


def test_words_held_back_with_a_run_on_are_shown_when_their_audio_plays(monkeypatch):
    """2026-10-24: he said "Go ahead, I'm here" and only "I'm here." showed: his words arrive ahead of his audio,
    so "Go ahead," came while the hold was on and its audio just after it lifted."""
    monkeypatch.setattr(sr, "OPENAI_HOLD_QUIET_S", 0.2)
    m = _manager()
    try:
        s, h = _live_web(m)
        s.push({"type": "session.output_transcript.delta", "delta": " So when you think about work"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " I'm going to interrupt you"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        s.push({"type": "session.output_transcript.delta", "delta": " Go ahead,"})      # during the hold
        time.sleep(0.3)                                                                 # the hold lifts
        s.push(_audio(640))                                                             # "go ahead" plays
        s.push({"type": "session.output_transcript.delta", "delta": " I'm here."})
        s.push(_audio(640))
        for _ in range(15):
            s.push(_audio(0))
        assert _wait(lambda: any("Go ahead, I'm here." == e.get("text") and not e.get("partial")
                                 for e in _ev(m, typ="agent_response")))
    finally:
        m.shutdown()
