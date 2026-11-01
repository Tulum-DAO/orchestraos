"""Live calls follow the server-wide voice provider only, speak in the called agent's own voice, and say which
voice and provider they are on. Stand-ins only: no sockets, no keys, no network."""
import importlib.util
import json
import pathlib

import pytest

from services.arturo import live_engine as le
from services.arturo import openai_live as ol
from services.arturo import stream_relay as sr
from services.arturo import voice_choice as vc
from services.arturo import voice_vendor as vv


# --- the engine follows state/voice-vendor.json only ---------------------------------------------------
def test_openai_with_the_relay_up_is_gpt_live():
    assert le.choose("openai", True, lambda v: "") == ("relay", None)


def test_openai_unusable_gives_its_own_reason():
    engine, why = le.choose("openai", True, lambda v: "OPENAI_API_KEY is not set")
    assert engine is None and why == "OPENAI_API_KEY is not set"


def test_openai_without_the_relay_is_refused_plainly():
    engine, why = le.choose("openai", False, lambda v: "")
    assert engine is None and "relay" in why


@pytest.mark.parametrize("vendor", ["hume", "elevenlabs", "", None, "nope"])
def test_other_providers_have_no_live_engine_yet(vendor):
    assert le.choose(vendor, True, lambda v: "", gemini_key=True) == (
        None, "Live calls aren't available with this voice provider yet")


@pytest.mark.parametrize("vendor", ["hume", "elevenlabs", "openai"])
def test_a_gemini_key_alone_never_picks_gemini(vendor):
    engine, _ = le.choose(vendor, True, lambda v: "", gemini_key=True)
    assert engine != "gemini"


def test_gemini_only_for_a_provider_value_of_that_name():
    assert "gemini" not in vv.REQUIRED and "gemini" not in vv.VENDOR_COPY   # not a provider value today
    assert le.choose("gemini", True, None, gemini_key=True) == ("gemini", None)
    assert le.choose("gemini", True, None, gemini_key=False)[0] is None


def _load_proxy(monkeypatch, tmp_path, vendor, gemini=True):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    if gemini:
        monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    spec = importlib.util.spec_from_file_location("arturo_proxy_live_engine",
                                                  pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=lambda k: "x")
    (tmp_path / "v.json").write_text(json.dumps({"vendor": vendor}))
    monkeypatch.setattr(vv, "_default", store)
    return mod


@pytest.mark.parametrize("vendor,engine,reason", [
    ("openai", "relay", None),
    ("hume", None, "Live calls aren't available with this voice provider yet"),
    ("elevenlabs", None, "Live calls aren't available with this voice provider yet")])
def test_health_reports_the_engine_from_the_provider_even_with_a_gemini_key(monkeypatch, tmp_path, vendor,
                                                                           engine, reason):
    mod = _load_proxy(monkeypatch, tmp_path, vendor)
    assert "GEMINI_API_KEY" in mod.VOICE_VENDORS_PRESENT          # a Gemini key is present, and ignored
    try:
        j = mod.app.test_client().get("/health").get_json()
        assert j["live_engine"] == engine and j["live"] is (engine is not None) and j["live_reason"] == reason
    finally:
        mod._STREAM_RELAY.shutdown()


# --- the agent rides the call: route -> relay holder -> socket factory ----------------------------------
def test_the_audio_route_hands_the_agent_to_the_relay(monkeypatch, tmp_path):
    mod = _load_proxy(monkeypatch, tmp_path, "openai", gemini=False)
    seen = []
    monkeypatch.setattr(mod._STREAM_RELAY, "feed_audio", lambda cid, pcm, **k: seen.append(k) or {"ok": True})
    c = mod.app.test_client()
    loop = {"REMOTE_ADDR": "127.0.0.1"}
    try:
        for cid, extra in (("cA", {"X-Arturo-Agent": "dev-demo"}), ("cB", {})):
            r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50, environ_base=loop,
                       headers={"X-Conversation-Id": cid, "X-Arturo-Principal": "fleet", **extra},
                       content_type="application/octet-stream")
            assert r.status_code == 200, r.get_json()
        assert seen[0]["agent"] == "dev-demo" and seen[1]["agent"] is None
        r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50, environ_base=loop,
                   headers={"X-Conversation-Id": "cC", "X-Arturo-Agent": "../etc"},
                   content_type="application/octet-stream")
        assert r.status_code == 400 and len(seen) == 2          # refused, never guessed at
    finally:
        mod._STREAM_RELAY.shutdown()


class _Sock:
    def send(self, p):
        pass

    def recv(self):
        raise RuntimeError("closed")

    def close(self):
        pass


@pytest.mark.parametrize("vendor", ["openai", "hume"])
def test_the_holder_passes_the_agent_to_the_factory_only_when_there_is_one(vendor):
    calls = []

    def factory(cid, **kw):
        calls.append(kw)
        return _Sock()
    m = sr.RelayManager(factories={vendor: factory}, vendor_fn=lambda: vendor, vendor_check=lambda v: "")
    try:
        sr._Holder("c-agent", m, vendor, agent="pm-demo").ensure_socket()
        sr._Holder("c-own", m, vendor).ensure_socket()
        assert calls[0].get("agent") == "pm-demo" and "agent" not in calls[1]
    finally:
        m.shutdown()


def test_feed_audio_stamps_the_agent_on_the_holder_once():
    m = sr.RelayManager(factories={"openai": lambda cid, **k: _Sock()}, vendor_fn=lambda: "openai",
                        vendor_check=lambda v: "")
    try:
        m.feed_audio("c1", b"\x00\x01" * 160, agent="qa-demo")
        m.feed_audio("c1", b"\x00\x01" * 160, agent="other-demo")
        assert m._holders["c1"].agent == "qa-demo"
    finally:
        m.shutdown()


class _FakeOpenAI:
    sent = []

    def __init__(self, url, headers):
        pass

    def send(self, p):
        _FakeOpenAI.sent.append(json.loads(p))


@pytest.fixture
def voices(tmp_path, monkeypatch):
    store = vc.VoiceChoiceStore(path=tmp_path / "voice.json", log_path=tmp_path / "voice.log")
    monkeypatch.setattr(vc, "_default", store)
    monkeypatch.setattr(ol, "AsyncWsSocket", _FakeOpenAI)
    monkeypatch.setattr(ol, "api_key", lambda: "test-not-a-key")
    _FakeOpenAI.sent = []
    return store


def test_openai_call_uses_the_agents_own_voice_else_the_server_voice(voices):
    vc.set_voice("openai", "cedar")                                  # the install's voice
    vc.set_voice("openai", "ash", agent="dev-demo")                  # dev-demo's own
    ol.openai_socket_factory("c1", agent="dev-demo")
    ol.openai_socket_factory("c2", agent="pm-demo")                  # no pick of its own: the server voice
    ol.openai_socket_factory("c3")                                   # Arturo's own call: the server voice
    got = [f["session"]["audio"]["output"]["voice"] for f in _FakeOpenAI.sent]
    assert got == ["ash", "cedar", "cedar"]


def test_openai_call_with_no_pick_anywhere_is_marin(voices):
    ol.openai_socket_factory("c1", agent="dev-demo")
    assert _FakeOpenAI.sent[-1]["session"]["audio"]["output"]["voice"] == vc.OPENAI_DEFAULT


def test_hume_call_uses_the_agents_own_voice(monkeypatch, voices):
    sent = []

    class _WS:
        def __init__(self, *a, **k):
            pass

        def send(self, p):
            sent.append(json.loads(p))

        def settimeout(self, t):
            pass
    monkeypatch.setattr(vc, "get_voice", lambda vendor, agent=None: "h-agent" if agent == "dev-demo" else "h-own")
    monkeypatch.setattr(sr, "_secret", lambda k: "test-not-a-key")
    import websocket
    monkeypatch.setattr(websocket, "create_connection", lambda *a, **k: _WS())
    sr.hume_socket_factory("c1", agent="dev-demo")
    sr.hume_socket_factory("c2")
    voice_ids = [f.get("voice_id") for f in sent if f.get("type") == "session_settings"]
    assert voice_ids == ["h-agent", "h-own"]


# --- one plain line: which voice, on which provider; never a key ----------------------------------------
def test_the_session_says_which_voice_and_provider_it_is_on(voices, monkeypatch):
    monkeypatch.setattr(ol, "api_key", lambda: "sk-test-SECRET")
    vc.set_voice("openai", "marin", agent="dev-demo")
    ol.openai_socket_factory("c1", agent="dev-demo")
    instr = _FakeOpenAI.sent[-1]["session"]["instructions"]
    assert instr.rstrip().endswith("You're speaking as Marin, on GPT-Live.")
    assert instr.count("You're speaking as") == 1
    assert "sk-test-SECRET" not in json.dumps(_FakeOpenAI.sent[-1])


def test_the_line_uses_the_provider_title_and_is_empty_without_a_voice():
    assert ol.speaking_as("cedar") == "You're speaking as Cedar, on GPT-Live."
    assert ol.speaking_as("Serene", "hume") == "You're speaking as Serene, on Hume."
    assert ol.speaking_as("") == ""


def test_explicit_instructions_are_left_as_given():
    assert ol.session_start("Only this.", voice="ash")["session"]["instructions"] == "Only this."
