"""A call with an agent runs in the agent's own session: the switch, the reply's speech, the acknowledgement's timing,
one turn end to end against a fake event log and a fake gateway, and the relay's wiring. No sockets to anywhere, no
gateway, no CLI: stand-ins and temp dirs."""
import json
import os

import pytest

from services.arturo import agent_call as AC


# --- the switch ---------------------------------------------------------------------------------------------------
@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "d"
    (d / "state" / "headless" / "dev-demo").mkdir(parents=True)
    (d / "registry.json").write_text(json.dumps({"agents": {
        "dev-demo": {"runtime": "claude", "tmux_session": "dev-demo", "display_name": "Sample"},
        "pm-demo": {"runtime": "claude", "tmux_session": "pm-demo"}}}))
    (d / "state" / "headless-seats.json").write_text(json.dumps({"seats": {
        "dev-demo": {"headless": True, "runtime": "claude"}}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(d))
    monkeypatch.delenv("ARTURO_AGENT_ACK_AFTER_S", raising=False)
    return d


def _switch(d, seats):
    (d / "state" / "agent-calls.json").write_text(json.dumps({"seats": seats}))


def test_off_unless_switched_on_and_streaming(data):
    assert not AC.enabled("dev-demo")                                  # no switch file
    _switch(data, {"dev-demo": {"on": True}, "pm-demo": {"on": True}})
    assert AC.enabled("dev-demo") and not AC.in_dev("dev-demo")
    assert not AC.enabled("pm-demo")                                   # a tmux seat has no reply stream
    assert not AC.enabled("")
    (data / "state" / "headless" / "dev-demo" / "dev").write_text("")
    assert AC.enabled("dev-demo") and AC.in_dev("dev-demo")            # in Dev: still its call, asked back to chat


def test_ack_threshold_is_a_setting_with_a_3_s_default(data):
    _switch(data, {"dev-demo": {"on": True}})
    assert AC.ack_after_s("dev-demo") == 3.0
    _switch(data, {"dev-demo": {"on": True, "ack_after_s": 5}})
    assert AC.ack_after_s("dev-demo") == 5.0


def test_display_name_from_the_registry(data):
    assert AC.display_name("dev-demo") == "Sample" and AC.display_name("pm-demo") == "pm-demo"


def test_page_context_is_s6s_tag_exactly():
    assert AC.page_context_tag({"title": "Example", "path": "/agents/dev-demo"}) == (
        "<page-context>The person is looking at this page in the dashboard as they send this: Example "
        "(/agents/dev-demo). Use it only if they ask about the page; do not mention it otherwise.</page-context>")
    assert AC.page_context_tag(None) == "" and AC.page_context_tag({}) == ""


def test_acknowledgements_vary_and_never_repeat_back_to_back():
    said = [AC.next_ack("qa-demo") for _ in range(12)]
    assert all(a in AC.ACKS for a in said)
    assert all(a != b for a, b in zip(said, said[1:]))
    assert len(set(said)) == len(AC.ACKS)


# --- speech from the streaming reply ------------------------------------------------------------------------------
def _speak_all(chunks):
    sp, out = AC.Speech(), []
    for c in chunks:
        out += sp.feed(c)
    return out, sp.flush()


def test_sentences_go_out_as_soon_as_they_are_complete():
    sp = AC.Speech()
    assert sp.feed("The build ") == []
    assert sp.feed("passed. Now the te") == ["The build passed."]
    assert sp.feed("sts run! Done") == ["Now the tests run!"]
    assert sp.flush() == ["Done"]


def test_code_and_tables_are_summarised_once():
    out, rest = _speak_all(["Here it is:\n", "```python\nprint(1)\n", "```\n", "| a | b |\n|---|---|\n",
                            "That's all.\n"])
    assert out + rest == ["Here it is:", AC.DETAILS, "That's all."]


def test_markdown_marks_and_links_are_not_read_out():
    out, rest = _speak_all(["## Status\n", "- **Two** files changed in [the repo](http://x).\n", "Ok"])
    assert out + rest == ["Status", "Two files changed in the repo.", "Ok"]


def test_an_unclosed_fence_is_never_read():
    out, rest = _speak_all(["Look:\n```\nsecret_code()\n"])
    assert out + rest == ["Look:", AC.DETAILS]


# --- one turn end to end ------------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Tail:
    """Scripted log records, released by the fake clock's time."""

    def __init__(self, clock, script):
        self.clock, self.script = clock, sorted(script, key=lambda x: x[0])

    def read(self):
        out = []
        while self.script and self.script[0][0] <= self.clock.t:
            out.append(self.script.pop(0)[1])
        return out


def _echo(text):
    return {"kind": "out", "ev": {"type": "user", "isReplay": True, "message": {"role": "user", "content": text}}}


def _delta(text):
    return {"kind": "out", "ev": {"type": "stream_event", "parent_tool_use_id": None, "event": {
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}}


def _tool(name="Bash", parent=None):
    return {"kind": "out", "ev": {"type": "stream_event", "parent_tool_use_id": parent, "event": {
        "type": "content_block_start", "content_block": {"type": "tool_use", "name": name}}}}


END = {"kind": "out", "ev": {"type": "result", "subtype": "success"}}


def _turn(script, q="what changed?", deliver=None, **kw):
    clock = Clock()
    said, later, sent = [], [], []

    def _deliver(seat, text):
        sent.append((seat, text))
        return {"ok": True, "delivered": True, "held": False}
    rec = AC.run_turn("dev-demo", q, deliver=deliver or _deliver, tail=Tail(clock, script(q)), say=said.append,
                      say_out_of_band=later.append, clock=clock, sleep=clock.sleep, **kw)
    return rec, said, later, sent


def test_a_quick_reply_is_silent_then_spoken_sentence_by_sentence():
    rec, said, later, sent = _turn(lambda q: [(0.1, _echo(q)), (1.8, _delta("Two files. ")),
                                              (2.2, _delta("Both tests pass.")), (2.4, END)])
    assert sent == [("dev-demo", "what changed?")]                 # the words, as typed, into its own session
    assert said == ["Two files.", "Both tests pass."] and later == []
    assert rec["ack"] == "silence" and rec["first_speech_s"] == pytest.approx(1.8, abs=0.11)
    assert rec["outcome"] == "answered"


def test_past_the_threshold_one_acknowledgement():
    rec, said, _, _ = _turn(lambda q: [(0.1, _echo(q)), (5.0, _delta("Found it.")), (5.1, END)])
    assert said[0] in AC.ACKS and said[1:] == ["Found it."]
    assert rec["ack"] == "threshold" and rec["first_speech_s"] == pytest.approx(5.0, abs=0.25)


def test_a_tool_start_acknowledges_at_once_and_only_once():
    rec, said, _, _ = _turn(lambda q: [(0.1, _echo(q)), (0.5, _tool()), (1.0, _tool()), (6.0, _delta("Done.")),
                                       (6.1, END)])
    assert [s for s in said if s in AC.ACKS] == [said[0]] and said[-1] == "Done."
    assert rec["ack"] == "tool" and rec["tool_started"]


def test_a_subagents_tools_and_another_turns_words_are_not_ours():
    rec, said, _, _ = _turn(lambda q: [(0.0, _delta("Earlier turn. ")), (0.1, _echo("someone else")),
                                       (0.2, _echo(q)), (0.5, _tool(parent="toolu_1")),
                                       (1.0, _delta("Mine.")), (1.1, END)])
    assert said == ["Mine."] and rec["ack"] == "silence" and not rec["tool_started"]


def test_a_long_turn_says_so_once_then_speaks_out_of_band_with_no_cut_off():
    rec, said, later, _ = _turn(lambda q: [(0.1, _echo(q)), (1.0, _tool()), (95.0, _delta("All done. ")),
                                           (96.0, _delta("Three fixed.")), (96.5, END)])
    assert AC.TAKING_A_WHILE in said and later == ["All done.", "Three fixed."]
    assert rec["long"] and rec["outcome"] == "answered" and rec["turn_s"] > 90


def test_mid_turn_is_held_and_said_so():
    rec, said, _, _ = _turn(lambda q: [(4.0, _echo(q)), (5.0, _delta("Now yours.")), (5.1, END)],
                            deliver=lambda s, t: {"ok": True, "delivered": False, "held": True})
    assert said == [AC.BUSY, "Now yours."] and rec["ack"] == "busy"


def test_a_seat_mid_turn_queues_and_says_so():
    rec, said, _, _ = _turn(lambda q: [(4.0, _echo(q)), (5.0, _delta("Now yours.")), (5.1, END)],
                            deliver=lambda s, t: {"ok": True, "delivered": True, "busy": True})
    assert said == [AC.BUSY, "Now yours."] and rec["ack"] == "busy"


def test_deliver_reads_whether_the_seat_is_mid_turn(monkeypatch):
    from scripts import gateway_deliver as GDS
    sent = []
    monkeypatch.setattr(GDS, "deliver", lambda *a, **k: sent.append((a, k)) or {"ok": True, "delivered": True})
    assert AC.deliver("dev-demo", "x", status={"state": "working"})["busy"] is True
    assert AC.deliver("dev-demo", "x", status={"state": "idle"})["busy"] is False
    (args, kw), _ = sent
    assert args == ("dev-demo", "x") and kw["source"] == "person" and kw["actor"].startswith("operator:")


def test_a_refusal_is_said_and_nothing_is_followed():
    rec, said, _, _ = _turn(lambda q: [], deliver=lambda s, t: {"ok": False, "reason": "busy"})
    assert said == ["I couldn't get that to my session (busy)."] and rec["outcome"] == "not_delivered"


def test_slash_commands_never_go_by_voice():
    rec, said, _, sent = _turn(lambda q: [], q="/compact")
    assert said == [AC.NO_COMMANDS] and sent == [] and rec["outcome"] == "slash_refused"


def test_the_page_rides_on_the_same_line():
    page = {"title": "Example", "path": "/agents/dev-demo"}
    tag = AC.page_context_tag(page)
    rec, said, _, sent = _turn(lambda q: [(0.1, _echo(f"{q} {tag}")), (1.0, _delta("Yes.")), (1.1, END)],
                               page=page)
    assert sent == [("dev-demo", f"what changed? {tag}")] and said == ["Yes."]


def test_a_hang_up_stops_following():
    clock_box = {}

    def script(q):
        return [(0.1, _echo(q))]
    clock = Clock()
    rec = AC.run_turn("dev-demo", "x", deliver=lambda s, t: {"ok": True, "delivered": True},
                      tail=Tail(clock, script("x")), say=lambda t: None, say_out_of_band=lambda t: None,
                      ended=lambda: clock.t > 2.0, clock=clock, sleep=clock.sleep)
    assert rec["outcome"] == "call_ended" and rec["turn_s"] <= 2.2
    assert clock_box == {}


def test_counts_over_the_last_20_turns(data):
    for ack in ["silence"] * 12 + ["threshold"] * 5 + ["tool"] * 3 + ["busy"] * 2:
        AC.record_turn({"seat": "dev-demo", "ack": ack, "spoken": "never stored"}, "c1")
    c = AC.counts()
    assert c == {"turns": 20, "silence": 10, "threshold": 5, "tool": 3, "busy": 2}
    rows = (data / "state" / "voice-calls" / "agent-turns.jsonl").read_text()
    assert "never stored" not in rows                                  # timings only, never the words


def test_the_event_tail_reads_only_new_whole_lines(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text(json.dumps({"old": 1}) + "\n")
    t = AC.EventTail(p)
    assert t.read() == []
    with open(p, "a") as fh:
        fh.write(json.dumps({"a": 1}) + "\n" + '{"b": ')
    assert t.read() == [{"a": 1}]
    with open(p, "a") as fh:
        fh.write("2}\n")
    assert t.read() == [{"b": 2}]


def test_the_warm_line_once_when_the_seat_is_not_up():
    assert AC.warm_line("dev-demo", status={"pid": 9, "state": "idle", "boot": {"booted": True}}) is None
    assert AC.warm_line("dev-demo", status={"pid": None, "state": "restarting"}) == AC.STARTING
    assert AC.warm_line("dev-demo", status={"pid": 9, "state": "idle", "boot": {"booted": False}}) == AC.STARTING


# --- the live-call marker ------------------------------------------------------------------------------------------
def test_the_call_marker_is_written_refreshed_and_cleared_by_its_own_call(data):
    p = data / "state" / "headless" / "dev-demo" / AC.CALL_MARK
    AC.mark_call("dev-demo", "c1")
    assert json.loads(p.read_text())["cid"] == "c1"
    os.utime(p, (1, 1))
    AC.touch_call("dev-demo")
    assert p.stat().st_mtime > 1
    AC.clear_call("dev-demo", "c2")                                   # another call's end: not ours to clear
    assert p.exists()
    AC.clear_call("dev-demo", "c1")
    assert not p.exists()
    AC.mark_call("pm-demo", "c3")                                     # no supervisor dir: nothing written
    assert not (data / "state" / "headless" / "pm-demo").exists()


# --- the relay: a call with a switched-on agent goes to its own session ------------------------------------------
class _Sock:
    def __init__(self):
        self.sent = []

    def send(self, p):
        self.sent.append(json.loads(p))

    def recv(self):
        raise RuntimeError("closed")

    def close(self):
        pass


class _FakeAC:
    """agent_call's surface the relay uses, scripted."""
    EventTail = staticmethod(lambda p: None)
    events_path = staticmethod(lambda s: "/nonexistent")

    DEV_LINE = AC.DEV_LINE

    def __init__(self, on=True, line=None, dev=False):
        self.on, self.line, self.marks, self.turns, self.recorded, self.dev = on, line, [], [], [], dev

    def in_dev(self, a):
        return self.dev

    def enabled(self, a):
        return self.on

    def mark_call(self, a, cid):
        self.marks.append(("mark", a, cid))

    def touch_call(self, a):
        self.marks.append(("touch", a))

    def clear_call(self, a, cid=None):
        self.marks.append(("clear", a, cid))

    def warm_line(self, a):
        return self.line

    def ack_after_s(self, a):
        return 3.0

    def deliver(self, *a):
        raise AssertionError("the fake run_turn never delivers")

    def run_turn(self, seat, q, *, say, say_out_of_band, **kw):
        self.turns.append((seat, q, kw))
        say("Two files.")
        say("Both pass.")
        return {"seat": seat, "ack": "silence", "spoken": "Two files. Both pass.", "outcome": "answered"}

    def record_turn(self, rec, cid):
        self.recorded.append((rec, cid))


def _manager(ac, factory_calls):
    from services.arturo import stream_relay as sr

    def factory(cid, **kw):
        factory_calls.append(kw)
        return _Sock()
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "")
    m.agent_calls = ac
    m.openai_delegate = lambda cid, msgs: pytest.fail("Arturo's CLM answered an agent call")
    m.scrub = lambda t: t
    return m


def test_an_agent_call_speaks_as_the_agent_and_its_session_answers():
    from services.arturo import stream_relay as sr
    ac, calls, journal = _FakeAC(line=None), [], []
    m = _manager(ac, calls)
    m.journal_hook = lambda cid, role, text, **meta: journal.append((role, text, meta))
    try:
        h = sr._Holder("c-agent", m, "openai", agent="dev-demo")
        m._holders["c-agent"] = h
        assert h.agent_mode and ac.marks[0] == ("mark", "dev-demo", "c-agent")
        h.ensure_socket()
        assert calls[0] == {"agent": "dev-demo", "agent_mode": True}
        sock = _Sock()
        h._oa_delegate(sock, "d1", "what changed?", [])
        assert [f["content"] for f in sock.sent] == ["Two files.", "Both pass."]
        assert all(f["type"] == "session.commentary.append" and f["delegation_id"] == "d1" for f in sock.sent)
        assert ac.turns[0][:2] == ("dev-demo", "what changed?") and ac.recorded[0][1] == "c-agent"
        assert journal == [("user", "what changed?", {"agent": "dev-demo"}),
                           ("assistant", "Two files. Both pass.", {"agent": "dev-demo"})]
        assert m.agent_mode("c-agent")
        h.close()
        assert ac.marks[-1] == ("clear", "dev-demo", "c-agent")
    finally:
        m.shutdown()


def test_the_warm_line_is_said_once_per_call():
    from services.arturo import stream_relay as sr
    ac = _FakeAC(line=AC.STARTING)
    m = _manager(ac, [])
    m.journal_hook = lambda *a, **k: None
    try:
        h = sr._Holder("c-warm", m, "openai", agent="dev-demo")
        s1, s2 = _Sock(), _Sock()
        h._oa_delegate(s1, "d1", "a", [])
        h._oa_delegate(s2, "d2", "b", [])
        assert s1.sent[0]["content"] == AC.STARTING and AC.STARTING not in [f["content"] for f in s2.sent]
    finally:
        m.shutdown()


@pytest.mark.parametrize("vendor,agent,on", [("openai", None, True), ("openai", "dev-demo", False),
                                             ("hume", "dev-demo", True)])
def test_every_other_call_runs_as_before(vendor, agent, on):
    from services.arturo import stream_relay as sr
    ac, calls = _FakeAC(on=on), []
    m = _manager(ac, calls)
    try:
        h = sr._Holder("c-x", m, vendor, agent=agent)
        assert not h.agent_mode and ac.marks == []
        if vendor == "openai":
            h.ensure_socket()
            assert "agent_mode" not in calls[0]
    finally:
        m.shutdown()


def test_the_uplink_keeps_the_marker_fresh_at_most_every_10_s(monkeypatch):
    from services.arturo import stream_relay as sr
    ac = _FakeAC()
    m = _manager(ac, [])
    try:
        h = sr._Holder("c-t", m, "openai", agent="dev-demo")
        now = [1000.0]
        monkeypatch.setattr(sr.time, "time", lambda: now[0])
        for dt in (0, 1, 2, 11, 12):
            now[0] = 1000.0 + dt
            h.touch_call()
        assert [x for x in ac.marks if x[0] == "touch"] == [("touch", "dev-demo")] * 2
    finally:
        m.shutdown()


# --- GPT-Live speaks as the agent ---------------------------------------------------------------------------------
def test_the_agent_calls_instructions_use_its_own_name(data, monkeypatch):
    from services.arturo import openai_live as ol
    from services.arturo import voice_choice as vc
    sent = []

    class _WS:
        def __init__(self, url, headers):
            pass

        def send(self, p):
            sent.append(json.loads(p))
    monkeypatch.setattr(ol, "AsyncWsSocket", _WS)
    monkeypatch.setattr(ol, "api_key", lambda: "test-not-a-key")
    monkeypatch.setattr(vc, "get_voice", lambda vendor, agent=None: "cedar")
    ol.openai_socket_factory("c1", agent="dev-demo", agent_mode=True)
    ol.openai_socket_factory("c2", agent="dev-demo")
    agent_instr, own_instr = (f["session"]["instructions"] for f in sent)
    assert agent_instr.startswith("You are Sample,") and "Sample's own session" in agent_instr
    assert agent_instr.rstrip().endswith("You're speaking as Cedar, on GPT-Live.")
    assert "Arturo" not in agent_instr
    assert "Sample" not in own_instr                                   # the agent's voice only, as before


def test_a_seat_in_dev_is_asked_back_to_chat_never_forced_and_arturo_never_answers():
    from services.arturo import stream_relay as sr
    ac = _FakeAC(dev=True)
    m = _manager(ac, [])
    journal = []
    m.journal_hook = lambda cid, role, text, **meta: journal.append((role, text))
    events = []
    m.buffer.put = lambda cid, ev: events.append(ev)
    try:
        h = sr._Holder("c-dev", m, "openai", agent="dev-demo")
        sock = _Sock()
        h._oa_delegate(sock, "d1", "what changed?", [])
        assert [f["content"] for f in sock.sent] == [AC.DEV_LINE]
        assert {"type": "agent_in_dev", "agent": "dev-demo"} in events
        assert ac.turns == [] and ac.recorded[0][0]["outcome"] == "in_dev"
        assert journal == [("user", "what changed?"), ("assistant", AC.DEV_LINE)]
        ac.dev = False                                                  # the person tapped Back to chat
        h._oa_delegate(_Sock(), "d2", "now?", [])
        assert ac.turns and ac.turns[0][:2] == ("dev-demo", "now?")
    finally:
        m.shutdown()
