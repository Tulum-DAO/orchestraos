"""Agent calls in "voice" mode (agent_call.mode, the default): GPT-Live answers naturally AS the
agent, with the agent's recent conversation, delegates lookups to our CLM, and work is commissioned into the agent's
own session from there. Nothing the caller says goes into the agent's session during the call."""
import json

import pytest

from services.arturo import agent_call as AC
from services.arturo import openai_live as OL
from services.arturo import stream_relay as sr
from services.arturo.test_agent_call import _FakeAC
from services.arturo.test_stream_relay_openai import FakeOpenAISocket, _audio, _ev, _wait


class _VoiceAC(_FakeAC):
    def __init__(self, mode="voice", **kw):
        super().__init__(**kw)
        self._mode = mode

    def mode(self, a):
        return self._mode

    def voice_context(self, a):
        self.ctx_reads = getattr(self, "ctx_reads", 0) + 1
        return "Operator: status? sk-ant-SECRET"

    def run_turn(self, seat, q, **kw):
        self.turns.append((seat, q))
        return {"seat": seat, "outcome": "answered"}


def _call(mode="voice", delegate=None):
    socks, kws, journal = [], [], []

    def factory(cid, **kw):
        kws.append(kw)
        s = FakeOpenAISocket()
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.openai_delegate = delegate
    m.journal_hook = lambda cid, role, text, **meta: journal.append((role, text, meta))
    m.scrub = lambda t: t.replace("sk-ant-SECRET", "[redacted]")
    ac = _VoiceAC(mode=mode)
    m.agent_calls = ac
    m.feed_audio("v1", b"\x00" * 3200, agent="dev-demo")
    assert _wait(lambda: socks)
    return m, socks[0], m._holders["v1"], ac, kws, journal


def test_voice_mode_is_the_agents_call_but_not_forced():
    m, s, h, ac, kws, _ = _call()
    try:
        assert h.agent_voice and not h.agent_mode
        # its context is read once, scrubbed, and handed to the socket (a reconnect reuses it)
        assert kws[0] == {"agent": "dev-demo", "agent_voice": True, "agent_context": "Operator: status? [redacted]"}
        assert ac.ctx_reads == 1
        assert ("mark", "dev-demo", "v1") in ac.marks   # the seat is not rotated while its call is live
        assert m.agent_mode("v1") is True           # the call lives in the agent's chat, not an Arturo thread
        assert m.agent_voice_seat("v1") == "dev-demo"
        m._el_map["el-123"] = "v1"
        assert m.agent_voice_seat("el-123") == "dev-demo"   # a callback's id resolves
        assert m.agent_voice_seat(None) is None
    finally:
        m.shutdown()


def test_forced_mode_is_unchanged():
    m, s, h, ac, kws, _ = _call(mode="forced")
    try:
        assert h.agent_mode and not h.agent_voice
        assert kws[0] == {"agent": "dev-demo", "agent_mode": True}
        assert m.agent_voice_seat("v1") is None
    finally:
        m.shutdown()


def test_gpt_live_speaks_for_itself_and_nothing_goes_to_the_session():
    m, s, h, ac, _, journal = _call()
    try:
        for w in "how are you".split():
            s.push({"type": "session.input_transcript.delta", "delta": " " + w})
        s.push({"type": "session.output_transcript.delta", "delta": " Doing well, thanks."})
        for _ in range(3):
            s.push(_audio(640))
        for _ in range(12):
            s.push(_audio(0))
        assert _wait(lambda: _ev(m, cid="v1", typ="audio"))
        assert _wait(lambda: any(e.get("text") == "Doing well, thanks." for e in _ev(m, cid="v1", typ="agent_response")))
        import time
        time.sleep(0.5)
        assert ac.turns == []                       # never forced into the agent's session
        assert journal and all(meta.get("agent") == "dev-demo" for _, _, meta in journal)
    finally:
        m.shutdown()


def test_delegation_goes_to_the_agents_own_session_marked_as_said():
    asked, delivered = [], []

    def delegate(cid, messages):          # Arturo's brain: never used on an agent's call
        asked.append(messages)
        return "nope"
    m, s, h, ac, _, journal = _call(delegate=delegate)
    try:
        ac.deliver = lambda seat, text: delivered.append((seat, text)) or {"ok": True}

        def run_turn(seat, q, *, deliver, say, **kw):
            ac.turns.append((seat, q))
            deliver(seat, q)
            say("Two files changed.")
            return {"seat": seat, "outcome": "answered", "spoken": "Two files changed."}
        ac.run_turn = run_turn
        ac.warm_line = lambda a: None
        ac.ack_after_s = lambda a: 3.0
        ac.record_turn = lambda rec, cid: None
        ac.VOICE_SAID_TAG = AC.VOICE_SAID_TAG
        s.push({"type": "session.input_transcript.delta", "delta": " what changed today"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" and f["content"] == "Two files changed."
                                 for f in s.sent))
        assert asked == [] and ac.turns == [("dev-demo", "what changed today")]
        assert delivered == [("dev-demo", "what changed today " + AC.VOICE_SAID_TAG)]
        assert h._voice_turns["what changed today"][0] == 1
    finally:
        m.shutdown()


def test_arturos_own_call_has_neither_mode():
    socks, kws = [], []

    def factory(cid, **kw):
        kws.append(kw)
        socks.append(FakeOpenAISocket())
        return socks[-1]
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.agent_calls = _VoiceAC()
    try:
        m.feed_audio("a1", b"\x00" * 3200)
        assert _wait(lambda: socks)
        h = m._holders["a1"]
        assert not h.agent_voice and not h.agent_mode and kws[0] == {}
        assert m.agent_voice_seat("a1") is None
    finally:
        m.shutdown()


# --- agent_call: mode and the agent's recent conversation ---------------------------------------------------------
def _seat(tmp_path, monkeypatch, rows, mode=None):
    orch = tmp_path / "orch"
    (orch / "state" / "headless" / "dev-demo").mkdir(parents=True)
    conf = {"on": True, **({"mode": mode} if mode else {})}
    (orch / "state" / "agent-calls.json").write_text(json.dumps({"seats": {"dev-demo": conf}}))
    cwd = "/home/x/.orchestra/projects/demo"
    sid = "11111111-2222-4333-8444-0000000000c1"
    (orch / "state" / "headless" / "dev-demo" / "seat.json").write_text(json.dumps({"session_id": sid, "cwd": cwd}))
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-home-x--orchestra-projects-demo"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(AC, "display_name", lambda s, o=None: "Demo")
    return str(orch)


def _u(text, **kw):
    return {"type": "user", "message": {"role": "user", "content": text}, **kw}


def _a(*blocks, **kw):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}, **kw}


def test_mode_defaults_to_voice(tmp_path, monkeypatch):
    orch = _seat(tmp_path, monkeypatch, [])
    assert AC.mode("dev-demo", orch) == "voice"
    orch2 = _seat(tmp_path / "b", monkeypatch, [], mode="forced")
    assert AC.mode("dev-demo", orch2) == "forced"


def test_voice_context_keeps_the_conversation_text_only(tmp_path, monkeypatch):
    orch = _seat(tmp_path, monkeypatch, [
        _u("fix the header <page-context>The person is looking at X</page-context>"),
        _a({"type": "thinking", "thinking": "hmm"}, {"type": "tool_use", "name": "Bash", "input": {}}),
        _u([{"type": "tool_result", "content": "secret output"}]),
        _a({"type": "text", "text": "Done: the header is fixed."}),
        _u("<command-name>/exit</command-name>"),
        _u("caveat", isMeta=True),
        _a({"type": "text", "text": "side work"}, isSidechain=True),
        _u("[Arturo Voice]: relayed task"),
        _u("[from gm] mail"),
        _u("This session is being continued from a previous conversation ...", isCompactSummary=True),
        {"type": "system", "content": "x"},
    ])
    ctx = AC.voice_context("dev-demo", orch)
    assert ctx == "Operator: fix the header\nDemo: Done: the header is fixed."


def test_voice_context_drops_the_oldest_past_the_cap(tmp_path, monkeypatch):
    orch = _seat(tmp_path, monkeypatch, [_u("a" * 50), _a({"type": "text", "text": "b" * 50}), _u("newest")])
    ctx = AC.voice_context("dev-demo", orch, max_chars=80)
    assert ctx.endswith("Operator: newest") and "aaaa" not in ctx


def test_voice_context_is_empty_when_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert AC.voice_context("nobody", str(tmp_path)) == ""


# --- GPT-Live's instructions ---------------------------------------------------------------------------------------
def test_voice_instructions_name_the_agent_and_carry_its_context():
    t = OL.voice_agent_instructions("dev-demo", "Demo", "Operator: hi\nDemo: hello")
    assert t.startswith("You are Demo,") and "DELEGATE" in t and "Operator: hi\nDemo: hello\n\n## Now" in t
    assert t.rstrip().endswith("you are Demo.")            # the greeting rule comes last, after the context
    assert "full-duplex voice call" in t and "does not apply to this call" in t   # the chat's "text only" is not this call
    assert "never answered" in t                            # screen/chat/message questions are delegated, never "I don't see it"
    assert "## " not in OL.voice_agent_instructions("dev-demo", "Demo", "")


def test_factory_sends_the_voice_persona(monkeypatch):
    sent = []

    class _WS:
        def __init__(self, *a, **k):
            pass

        def send(self, f):
            sent.append(json.loads(f))
    monkeypatch.setattr(OL, "api_key", lambda: "k")
    monkeypatch.setattr(OL, "AsyncWsSocket", _WS)
    monkeypatch.setattr(OL, "call_voice", lambda a: "marin")
    monkeypatch.setattr(AC, "display_name", lambda s, o=None: "Demo")
    OL.openai_socket_factory("c1", agent="dev-demo", agent_voice=True, agent_context="Operator: status?")
    ins = sent[0]["session"]["instructions"]
    assert ins.startswith("You are Demo,") and "Operator: status?" in ins and "speaking as" not in ins
    sent.clear()
    OL.openai_socket_factory("c2", agent="dev-demo", agent_mode=True)
    assert "DELEGATE everything" in sent[0]["session"]["instructions"]


# --- the agent's own chat mirrored into the call (agent_call.ChatMirror) -------------------------------------------
def _echo(text):
    return {"kind": "out", "ev": {"type": "user", "isReplay": True, "message": {"content": text}}}


def _delta(text):
    return {"kind": "out", "ev": {"type": "stream_event", "parent_tool_use_id": None,
                                  "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}}


_END = {"kind": "out", "ev": {"type": "result"}}


class _M(AC.ChatMirror):
    """The mirror without its ("done", prompt) bookkeeping, for the tests about what is noted and said."""

    def feed(self, records):
        return [a for a in super().feed(records) if a[0] != "done"]


def test_mirror_marks_every_turns_end_with_its_prompt():
    m = AC.ChatMirror("Demo")
    m.feed([_echo("ping")])
    assert m.feed([_delta("👍"), _END]) == [("done", "ping")]          # nothing said, but the end is marked


def test_mirror_typed_message_is_a_note_then_its_answer_is_said():
    m = _M("Demo")
    out = m.feed([_echo("what's the status? <page-context>x</page-context>")])
    assert len(out) == 1 and out[0][0] == "note" and '"what\'s the status?"' in out[0][1] and "SENT" in out[0][1]
    assert out[0][2] == "what's the status?"
    out = m.feed([_delta("All green. "), _delta("Two PRs open.\n```\ncode\n```\n"), _END])
    assert out == [("say", "All green. Two PRs open. " + AC.DETAILS, out[0][2], "what's the status?")]
    assert "answered in your chat" in out[0][2] and "what's the status?" in out[0][2]


def test_mirror_commissioned_work_comes_back_as_news():
    m = _M("Demo")
    assert m.feed([_echo("[Voice call]: fix the header")]) == []        # the call itself sent it: no note
    out = m.feed([_delta("Fixed the header."), _END])
    assert out[0][:2] == ("say", "Fixed the header.") and "work you started on this call" in out[0][2]


def test_mirror_ignores_mail_handoff_and_tool_only_turns():
    m = _M("Demo")
    assert m.feed([_echo("[from gm] status?"), _delta("ok"), _END]) == []
    assert m.feed([_echo('<voice-call-transcript id="vc_1">...'), _delta("noted"), _END]) == []
    assert m.feed([_echo("go"), _END])[:1][0][0] == "note"                # typed, but a reply with no text says nothing
    assert [a[0] for a in m.feed([_echo("test"), _delta("👍"), _END])] == ["note"]   # a 👍 reaction is not spoken


def test_mirror_caps_a_long_answer():
    m = _M("Demo")
    m.feed([_echo("long one")])
    out = m.feed([_delta("This is one sentence of words. " * 60), _END])
    say = out[0][1]
    assert len(say) <= AC.MIRROR_SAY_MAX + 40 and say.endswith("the rest is in the chat.")
    assert say.replace(" ... the rest is in the chat.", "").endswith("words.")   # whole sentences only


def test_mirror_a_message_landing_mid_turn_does_not_lose_the_reply():
    m = _M("Demo")
    m.feed([_echo("first question"), _delta("Half an answer.")])
    out = m.feed([_echo("second question")])
    assert out[0][:2] == ("say", "Half an answer.") and out[1][0] == "note" and "second question" in out[1][1]


class _Tail:
    def __init__(self):
        self.recs = []
        self.path = None

    def read(self):
        r, self.recs = self.recs, []
        return r


def test_relay_mirrors_the_agents_chat_into_the_call(monkeypatch):
    monkeypatch.setattr(sr, "AGENT_CHAT_POLL_S", 0.05)
    tail, typed = _Tail(), _Tail()

    class _MirrorAC(_VoiceAC):
        EventTail = staticmethod(lambda p: typed if str(p).endswith("typed.jsonl") else tail)
        ChatMirror = AC.ChatMirror
        typed_path = staticmethod(AC.typed_path)
        typed_note = staticmethod(AC.typed_note)

        def display_name(self, a):
            return "Demo"
    socks = []

    def factory(cid, **kw):
        socks.append(FakeOpenAISocket())
        return socks[-1]
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.scrub = lambda t: t
    m.agent_calls = _MirrorAC()
    try:
        m.feed_audio("v1", b"\x00" * 3200, agent="dev-demo")
        assert _wait(lambda: socks)
        s = socks[0]
        typed.recs = [{"at": 1, "text": "is the  build green?"}]            # the API, on send
        notes = lambda: [f for f in s.sent if f.get("type") == "session.thinking.append" and "build green" in f["content"]]
        assert _wait(lambda: len(notes()) == 1)
        tail.recs = [_echo("is the build green?")]                         # the session takes it: no second note
        import time as _t
        _t.sleep(0.3)
        assert len(notes()) == 1
        typed.recs = [{"at": 2, "text": "is the build green?"}]             # sent twice: two notes, two echoes skipped
        assert _wait(lambda: len(notes()) == 2)
        tail.recs = [_echo("is the build green?")]
        _t.sleep(0.3)
        assert len(notes()) == 2
        tail.recs = [_delta("Yes, all green."), _END]
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" and f["content"] == "Yes, all green."
                                 for f in s.sent))
    finally:
        m.shutdown()


def test_relay_notes_a_typed_message_from_the_session_when_the_api_did_not(monkeypatch):
    monkeypatch.setattr(sr, "AGENT_CHAT_POLL_S", 0.05)
    tail = _Tail()

    class _OldAC(_VoiceAC):                    # no typed_path: the session's echo is the only signal
        EventTail = staticmethod(lambda p: tail)
        ChatMirror = AC.ChatMirror

        def display_name(self, a):
            return "Demo"
    socks = []

    def factory(cid, **kw):
        socks.append(FakeOpenAISocket())
        return socks[-1]
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.scrub = lambda t: t
    m.agent_calls = _OldAC()
    try:
        m.feed_audio("v1", b"\x00" * 3200, agent="dev-demo")
        assert _wait(lambda: socks)
        tail.recs = [_echo("ping")]
        assert _wait(lambda: any(f.get("type") == "session.thinking.append" and '"ping"' in f["content"]
                                 for f in socks[0].sent))
    finally:
        m.shutdown()


def test_event_tail_restarts_on_a_truncated_file(tmp_path):
    p = tmp_path / "typed.jsonl"
    p.write_text('{"text": "a"}\n')
    t = AC.EventTail(p)
    p.write_text('{"text": "a"}\n{"text": "b"}\n')
    assert [r["text"] for r in t.read()] == ["b"]
    p.write_text('{"text": "c"}\n')                                       # rotated: smaller than where we stood
    assert [r["text"] for r in t.read()] == ["c"]


def test_relay_does_not_mirror_the_calls_own_request(monkeypatch):
    monkeypatch.setattr(sr, "AGENT_CHAT_POLL_S", 0.05)
    tail = _Tail()

    class _MirrorAC(_VoiceAC):
        EventTail = staticmethod(lambda p: tail)
        ChatMirror = AC.ChatMirror

        def display_name(self, a):
            return "Demo"
    socks = []

    def factory(cid, **kw):
        socks.append(FakeOpenAISocket())
        return socks[-1]
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.scrub = lambda t: t
    m.agent_calls = _MirrorAC()
    try:
        m.feed_audio("v1", b"\x00" * 3200, agent="dev-demo")
        assert _wait(lambda: socks)
        h = m._holders["v1"]
        h._voice_turns["send me a note"] = [1, __import__("time").time()]   # said on the call, spoken as it streamed
        tail.recs = [_echo("send me a note " + AC.VOICE_SAID_TAG), _delta("Sent it."), _END,
                     _echo("typed one"), _delta("Typed answer."), _END]
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" and f["content"] == "Typed answer."
                                 for f in socks[0].sent))
        sent = [f.get("content") for f in socks[0].sent if f.get("type") in ("session.commentary.append", "session.thinking.append")]
        assert not any("send me a note" in (c or "") or c == "Sent it." for c in sent)
        assert "send me a note" not in h._voice_turns                  # released at the turn's end
    finally:
        m.shutdown()
