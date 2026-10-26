"""A voice call lives inside its Arturo thread: lines archived as shown, the brain answers from the thread."""
import json

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo.call_threads import CallThreads
from services.arturo.thread_store import ThreadStore, VOICE_PREFIX, CALL_ENDED_LINE
from services.arturo import relay_live_bridge as B


def test_typed_and_spoken_messages_share_one_ordered_thread(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.record_turn("th", "typed question", "typed answer", principal="fleet")
    assert s.append_message("th", "user", "said on the call", mode="voice") == 2
    assert s.append_message("th", "assistant", "Spoken reply--", mode="voice") == 3
    s.append_message("th", "assistant", "Call ended (vc_client_x). [voice-call: vc_client_x /p.json]",
                     mode="voice", meta={"kind": "call_end", "call_id": "vc_client_x"})
    turns = s.get_thread("th")["turns"]
    assert [t["content"] for t in turns][:4] == ["typed question", "typed answer", "said on the call", "Spoken reply--"]
    assert "mode" not in turns[0] and turns[2]["mode"] == "voice"
    assert turns[4]["meta"] == {"kind": "call_end", "call_id": "vc_client_x"}
    hist = s.history("th")
    assert hist[2]["content"] == VOICE_PREFIX + "said on the call"       # the brain knows it was spoken
    assert hist[4]["content"] == CALL_ENDED_LINE                          # not the card's path grammar
    assert s.turn_count("th") == 5                                        # a warm CLI sees the thread moved


def test_a_call_started_in_a_new_thread_creates_it_with_a_title(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    s.append_message("new", "assistant", "Hey there!", mode="voice", principal="fleet")
    s.append_message("new", "user", "What's running on the fleet?", mode="voice")
    th = s.get_thread("new")
    assert th["title"] == "What's running on the fleet?" and s.started_by("new") == "fleet"


def test_only_final_lines_are_archived_and_the_end_closes_the_stretch(tmp_path):
    s = ThreadStore(tmp_path / "t.db")
    seen = []
    ct = CallThreads(s, on_line=lambda *a: seen.append(a),
                     end_marker=lambda cid: ("Call ended (vc_client_1). [voice-call: vc_client_1 /x.json]",
                                             {"kind": "call_end", "call_id": "vc_client_1"}))
    ct.observe("c1", {"type": "user_transcript", "text": "ignored: no thread yet"})
    assert not ct.bind("c1", "bad id with spaces")
    assert ct.bind("c1", "web_123", principal="fleet") and ct.call("web_123") == "c1"
    for ev in [{"type": "user_partial", "text": "what"},
               {"type": "user_transcript", "text": "what's up"},
               {"type": "agent_response", "text": "All", "partial": True},
               {"type": "agent_response", "text": "All good."},
               {"type": "agent_response", "text": "Read-out of a typed reply", "echo": True},
               {"type": "audio", "audio": "AAAA"}]:
        ct.observe("c1", ev)
    assert ct.end("c1") and ct.call("web_123") is None and not ct.end("c1")
    turns = s.get_thread("web_123")["turns"]
    assert [(t["role"], t["content"]) for t in turns] == [
        ("user", "what's up"), ("assistant", "All good."),
        ("assistant", "Call ended (vc_client_1). [voice-call: vc_client_1 /x.json]")]
    assert seen[-1] == ("web_123", "assistant", CALL_ENDED_LINE)


def test_a_failing_archive_never_breaks_the_call(tmp_path):
    class Boom:
        def append_message(self, *a, **k):
            raise OSError("disk full")
    ct = CallThreads(Boom())
    ct.bind("c1", "th")
    ct.observe("c1", {"type": "user_transcript", "text": "hello"})      # logged, not raised


def test_the_hello_names_the_thread_once(monkeypatch):
    sess = B.RelayLiveSession(None, "http://x", {})
    sess._note_page(json.dumps({"route": "/", "focusedEntity": None, "threadId": "web_abc"}))
    sess._note_page(json.dumps({"event": "context", "route": "/agents", "threadId": "web_other"}))
    assert sess.headers["X-Arturo-Thread"] == "web_abc"
    bad = B.RelayLiveSession(None, "http://x", {})
    bad._note_page(json.dumps({"route": "/", "threadId": "../etc/passwd x"}))
    assert "X-Arturo-Thread" not in bad.headers


class _Buf:
    def __init__(self):
        self.events = []

    def put(self, cid, ev):
        self.events.append((cid, ev))


class _Relay:
    def __init__(self):
        self.buffer = _Buf()
        self.ended = []

    def end(self, cid):
        self.ended.append(cid)
        return True


def test_a_delegated_voice_question_is_a_turn_on_the_thread(P, monkeypatch):
    P._THREADS.record_turn("web_t1", "my typed question earlier", "the typed answer", principal="fleet")
    relay = _Relay()
    P._wire_call_threads(relay)
    P._CALL_THREADS.bind("cid-1", "web_t1", principal="fleet")
    relay.buffer.put("cid-1", {"type": "user_transcript", "text": "and what about the voice part?"})
    sent = {}
    monkeypatch.setattr(P, "_OPENAI_POST", lambda cid, msgs: sent.update(cid=cid, msgs=msgs) or "")
    monkeypatch.setattr(P, "_sse_text", lambda body: "ok")
    P._openai_delegate("cid-1", [{"role": "assistant", "content": "Hi!"},
                                 {"role": "user", "content": "and what about the voice part?"}])
    contents = [m["content"] for m in sent["msgs"]]
    assert "my typed question earlier" in contents and "the typed answer" in contents   # the thread, not the call
    assert contents[-1] == VOICE_PREFIX + "and what about the voice part?"
    assert contents.count(VOICE_PREFIX + "and what about the voice part?") == 1       # never twice
    # the call's end lands in the thread as the card's marker, and a typed turn after it sees the call
    relay.end("cid-1")
    turns = P._THREADS.get_thread("web_t1")["turns"]
    assert turns[-1]["meta"]["kind"] == "call_end" and turns[-1]["content"].startswith("Call ended (vc_client_")
    hist = [h["content"] for h in P._conversation_history("web_t1")]
    assert VOICE_PREFIX + "and what about the voice part?" in hist and hist[-1] == CALL_ENDED_LINE


def test_a_call_without_a_thread_keeps_its_own_history(P, monkeypatch):
    relay = _Relay()
    P._wire_call_threads(relay)
    sent = {}
    monkeypatch.setattr(P, "_OPENAI_POST", lambda cid, msgs: sent.update(msgs=msgs) or "")
    monkeypatch.setattr(P, "_sse_text", lambda body: "ok")
    msgs = [{"role": "user", "content": "hi"}]
    P._openai_delegate("cid-x", msgs)
    assert sent["msgs"] == msgs


def test_a_typed_turn_during_a_call_is_also_spoken(P, monkeypatch):
    relay = _Relay()
    spoken = []
    relay.speak = lambda cid, say, note=None, echo=True: spoken.append((cid, say, note, echo)) or True
    monkeypatch.setattr(P, "_STREAM_RELAY", relay)
    P._wire_call_threads(relay)
    P._CALL_THREADS.bind("cid-9", "web_t9", principal="fleet")
    P._record_text_turn(conversation_id="web_t9", text="[Context: route=/]\nwhat's this link about?",
                        reply="It's a post about GPT-Live.", principal="fleet")
    (cid, say, note, echo), = spoken
    assert cid == "cid-9" and say == "It's a post about GPT-Live." and echo is True
    assert "TYPED" in note and "what's this link about?" in note and "[Context" not in note
    # no call in the thread: nothing is spoken
    P._record_text_turn(conversation_id="web_other", text="hi", reply="hello", principal="fleet")
    assert len(spoken) == 1


def test_a_read_out_never_reaches_the_screen_twice():
    assert B.translate({"type": "agent_response", "text": "said again", "echo": True}) == []



def test_an_answer_that_finishes_after_the_call_ended_lands_in_the_conversation(P, monkeypatch):
    """2026-10-24: "yeah, do that", then the call ended; Arturo acted 16 s later and its answer vanished."""
    monkeypatch.setattr(P, "_CALL_THREADS", CallThreads(P._THREADS, on_line=None, end_marker=None))
    P._CALL_THREADS.bind("cid_late", "web_late")

    def post(cid, messages):
        P._CALL_THREADS.end(cid)                          # the operator hangs up while the turn is worked on
        return 'data: {"choices":[{"delta":{"content":"Done: I asked the GM to rename the seats."}}]}\n\ndata: [DONE]'
    monkeypatch.setattr(P, "_OPENAI_POST", post)
    assert "rename" in P._openai_delegate("cid_late", [{"role": "user", "content": "yeah, do that"}])
    turns = P._THREADS.get_thread("web_late")["turns"]
    assert turns[-1]["content"] == "Done: I asked the GM to rename the seats." and turns[-1]["meta"]["kind"] == "after_call"


def test_an_answer_during_the_call_is_not_posted_twice(P, monkeypatch):
    monkeypatch.setattr(P, "_CALL_THREADS", CallThreads(P._THREADS, on_line=None, end_marker=None))
    P._CALL_THREADS.bind("cid_live", "web_live")
    monkeypatch.setattr(P, "_OPENAI_POST", lambda cid, m: 'data: {"choices":[{"delta":{"content":"Sure."}}]}')
    P._openai_delegate("cid_live", [{"role": "user", "content": "hi"}])
    assert not any((t.get("meta") or {}).get("kind") == "after_call"
                   for t in (P._THREADS.get_thread("web_live") or {"turns": []})["turns"])
