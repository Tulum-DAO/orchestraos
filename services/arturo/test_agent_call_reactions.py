"""A live voice call hears the agent's own reactions (spec "Voice calls"): agent_call.ReactionTail reads the API's
/api/reactions/stream for the call's agent and looks up the reacted message; ChatMirror.reaction turns the agent's
own new emoji into a silent note ("<agent> reacted 👍 to '...'"), never spoken. The relay wiring is tested in
test_agent_call_reactions.py's relay test below."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from services.arturo import agent_call as AC


def _until(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


AGENT_EV = {"surface": "agent-chat", "conv": "dev-demo", "ref": "s1~u1#0",
            "reactor": {"kind": "agent", "id": "dev-demo"}, "emoji": "👍", "at": "t"}
USER_EV = {"surface": "agent-chat", "conv": "dev-demo", "ref": "s1~u1#0",
           "reactor": {"kind": "user", "id": "op"}, "emoji": "❤️", "at": "t"}
ITEMS = [{"kind": "text", "role": "user", "text": "is the build green?", "ref": "s1~u1#0"}]


def _api(events, items):
    hits = {"stream": 0, "transcript": 0, "paths": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits["paths"].append(self.path)
            if self.path.startswith("/api/reactions/stream"):
                hits["stream"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if hits["stream"] == 1:
                    self.wfile.write(b"retry: 3000\n\n: ping\n\n")
                    for ev in events:
                        self.wfile.write(b"event: reaction\ndata: " + json.dumps(ev).encode() + b"\n\n")
                    self.wfile.flush()
                time.sleep(0.2)
                return
            if "/transcript" in self.path:
                hits["transcript"] += 1
                raw = json.dumps({"items": items}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(404)
            self.end_headers()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def test_reaction_tail_reads_the_stream_and_looks_up_the_agents_message():
    srv, hits = _api([AGENT_EV, USER_EV], ITEMS)
    tail = AC.ReactionTail("dev-demo", base=f"http://127.0.0.1:{srv.server_address[1]}", retry_s=0.05)
    try:
        got = []
        assert _until(lambda: got.extend(tail.read()) or len(got) == 2)
        assert got[0]["emoji"] == "👍" and got[0]["on"] == "is the build green?"
        assert "on" not in got[1]                                  # the operator's reaction: no lookup
        assert hits["transcript"] == 1
        assert any(p.startswith("/api/reactions/stream?") and "conv=dev-demo" in p and "surface=agent-chat" in p
                   for p in hits["paths"])
        assert _until(lambda: hits["stream"] >= 2)                 # a closed stream is reopened
    finally:
        tail.close()
        srv.shutdown()
        srv.server_close()


def test_message_text_by_ref_or_empty():
    srv, _hits = _api([], ITEMS)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert AC.message_text("dev-demo", "s1~u1#0", base=base) == "is the build green?"
        assert AC.message_text("dev-demo", "s1~zz#0", base=base) == ""
        assert AC.message_text("dev-demo", "s1~u1#0", base="http://127.0.0.1:9") == ""     # unreachable: ""
    finally:
        srv.shutdown()
        srv.server_close()


def test_api_base(monkeypatch):
    monkeypatch.setenv("ORCH_API_URL", "http://10.0.0.5:8000/")
    assert AC.api_base() == "http://10.0.0.5:8000"
    monkeypatch.delenv("ORCH_API_URL")
    monkeypatch.setenv("ORCHESTRA_API_PORT", "18888")
    assert AC.api_base() == "http://127.0.0.1:18888"


def test_reaction_note_is_background_not_speech():
    n = AC.reaction_note("Demo", "👍", "is the  build\ngreen?")
    assert n.startswith("Demo reacted 👍 to 'is the build green?'")
    assert "Do not read it out" in n
    assert AC.reaction_note("Demo", "🎉", "").startswith("Demo reacted 🎉 in the chat")
    assert len(AC.reaction_note("Demo", "👍", "x" * 500)) < 400


def test_mirror_notes_only_its_agents_own_new_reaction():
    m = AC.ChatMirror("Demo", agent="dev-demo")
    out = m.reaction({**AGENT_EV, "on": "is the build green?"})
    assert out == [("note", AC.reaction_note("Demo", "👍", "is the build green?"))]
    assert m.reaction(USER_EV) == []                                         # the operator's: the wake tells the agent
    assert m.reaction({**AGENT_EV, "emoji": None}) == []                     # a removal
    assert m.reaction({**AGENT_EV, "reactor": {"kind": "agent", "id": "other"}}) == []
    assert m.reaction("junk") == []


def test_mirror_without_an_agent_id_still_works():
    assert AC.ChatMirror("Demo").reaction({**AGENT_EV, "on": ""})[0][0] == "note"
from services.arturo import stream_relay as sr                                       # noqa: E402
from services.arturo.test_agent_call_voice import _VoiceAC                          # noqa: E402
from services.arturo.test_stream_relay_openai import FakeOpenAISocket, _wait         # noqa: E402


class _Q:
    def __init__(self):
        self.recs = []
        self.closed = False

    def read(self):
        r, self.recs = self.recs, []
        return r

    def close(self):
        self.closed = True


def test_relay_passes_the_agents_reaction_to_the_voice_silently(monkeypatch):
    monkeypatch.setattr(sr, "AGENT_CHAT_POLL_S", 0.05)
    tail, rtail, made = _Q(), _Q(), []

    class _ReactAC(_VoiceAC):
        EventTail = staticmethod(lambda p: tail)
        ChatMirror = AC.ChatMirror

        @staticmethod
        def ReactionTail(agent):
            made.append(agent)
            return rtail

        def display_name(self, a):
            return "Demo"
    socks = []

    def factory(cid, **kw):
        socks.append(FakeOpenAISocket())
        return socks[-1]
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150)
    m.scrub = lambda t: t
    m.agent_calls = _ReactAC()
    try:
        m.feed_audio("v1", b"\x00" * 3200, agent="dev-demo")
        assert _wait(lambda: socks)
        s = socks[0]
        assert _wait(lambda: made == ["dev-demo"])
        rtail.recs = [USER_EV, {**AGENT_EV, "on": "is the build green?"},
                      {**AGENT_EV, "reactor": {"kind": "agent", "id": "someone-else"}, "on": "x"}]
        notes = lambda: [f for f in s.sent if f.get("type") == "session.thinking.append" and "reacted" in f["content"]]
        assert _wait(lambda: len(notes()) == 1)
        assert notes()[0]["content"].startswith("Demo reacted 👍 to 'is the build green?'")
        time.sleep(0.3)
        assert len(notes()) == 1
        assert not any(f.get("type") == "session.commentary.append" and "reacted" in str(f.get("content"))
                       for f in s.sent)                                        # never spoken
    finally:
        m.shutdown()
    assert _wait(lambda: rtail.closed)


def test_relay_without_a_reaction_tail_is_unchanged(monkeypatch):
    """An agent_calls without ReactionTail (older fakes, a module that lacks it): the mirror runs as before."""
    monkeypatch.setattr(sr, "AGENT_CHAT_POLL_S", 0.05)
    tail = _Q()

    class _OldAC(_VoiceAC):
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
        tail.recs = [{"kind": "out", "ev": {"type": "user", "isReplay": True, "message": {"content": "ping"}}}]
        assert _wait(lambda: any(f.get("type") == "session.thinking.append" and '"ping"' in f["content"]
                                 for f in socks[0].sent))
    finally:
        m.shutdown()


def test_a_malformed_reactor_is_ignored_not_a_crash():
    m = AC.ChatMirror("Demo", agent="dev-demo")
    for bad in (None, "agent", ["agent"], 7):
        assert m.reaction({**AGENT_EV, "reactor": bad}) == []
    tail = AC.ReactionTail("dev-demo", base="http://127.0.0.1:1", retry_s=0.05)
    try:
        tail._take(json.dumps({**AGENT_EV, "reactor": "agent"}))
        tail._take(json.dumps({**AGENT_EV, "reactor": None}))
        assert len(tail.read()) == 2
    finally:
        tail.close() if hasattr(tail, "close") else None
