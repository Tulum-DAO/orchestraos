"""RED-first tests — v2/(b) socket-holder relay wiring (services/arturo/stream_relay.py +
arturo-proxy.py seams). Frozen target ARCHITECTURE-B.md @ 85c97fdee64a.

gm's locked by-effect criteria covered at this layer: exactly-one-journal per relayed
conv_id (callback-created, relay creates none), watch-stamp carve-out registry-driven,
server-driven finalize on relay close, our-side replay after a stubbed drop, cap=REJECT-NEW
at the route, flag-off byte-identical (routes absent), partials module behind its own flag.
"""
import importlib.util
import json
import pathlib
import threading
import time

import pytest

from services.arturo import stream_relay as sr


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_relay", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # defense against cross-file env bleed (gm gate must-fix msg_26a41e81): each test starts
    # with the stream flags UNSET; tests that need them set them explicitly via monkeypatch.
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    monkeypatch.delenv("ARTURO_STREAM_PARTIALS", raising=False)


class FakeELSocket:
    """Injected EL Conv-AI socket: records sends; test pushes server events; recv blocks."""
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False
        self.fail_sends = False

    def send(self, payload):
        if self.fail_sends or self.closed:
            raise RuntimeError("socket down")
        self.sent.append(json.loads(payload))

    def push(self, event):
        with self._cv:
            self._incoming.append(json.dumps(event))
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


@pytest.fixture()
def manager():
    sockets = []
    def factory(conversation_id):
        s = FakeELSocket()
        sockets.append(s)
        return s
    finalized = []
    m = sr.RelayManager(socket_factory=factory, on_finalize=lambda cid: finalized.append(cid),
                        cap=3, idle_ttl_s=600)
    m._test_sockets = sockets
    m._test_finalized = finalized
    yield m
    m.shutdown()


def _wait(pred, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------- audio forward + event relay ----------

def test_audio_forwarded_as_b64_chunks(manager):
    assert manager.feed_audio("c1", b"\x01\x02" * 100)["ok"]
    assert _wait(lambda: manager._test_sockets and manager._test_sockets[0].sent)
    msg = manager._test_sockets[0].sent[0]
    assert "user_audio_chunk" in msg
    import base64
    assert base64.b64decode(msg["user_audio_chunk"]) == b"\x01\x02" * 100


def test_el_events_relayed_with_cursor(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    s = manager._test_sockets[0]
    s.push({"type": "user_transcript", "user_transcription_event": {"user_transcript": "hi arturo"}})
    s.push({"type": "agent_response", "agent_response_event": {"agent_response": "hey boss"}})
    assert _wait(lambda: manager.events("c1", 0)[0])
    events, cur = manager.events("c1", 0)
    kinds = [e["type"] for e in events]
    assert "user_transcript" in kinds and "agent_response" in kinds
    more, cur2 = manager.events("c1", cur)
    assert more == [] and cur2 == cur


def test_interruption_becomes_barge_in_event(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    manager._test_sockets[0].push({"type": "interruption", "interruption_event": {"event_id": 5}})
    assert _wait(lambda: any(e["type"] == "barge_in" for e in manager.events("c1", 0)[0]))


def test_ping_gets_pong(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    s = manager._test_sockets[0]
    s.push({"type": "ping", "ping_event": {"event_id": 42}})
    assert _wait(lambda: any(m.get("type") == "pong" and m.get("event_id") == 42 for m in s.sent))


# ---------- cap + registry semantics at the manager ----------

def test_cap_rejects_new_conversation(manager):
    for cid in ("c1", "c2", "c3"):
        assert manager.feed_audio(cid, b"\x00")["ok"]
    r = manager.feed_audio("c4", b"\x00")
    assert not r["ok"] and r["error"] == "capacity"
    assert manager.feed_audio("c2", b"\x00")["ok"], "existing conversation still accepted at cap"


# ---------- surface carve-out + finalize ----------

def test_surface_registry_watch(manager):
    manager.feed_audio("c1", b"\x00")
    assert manager.surface("c1") == "watch"
    assert manager.surface("other") is None


def test_end_closes_socket_and_drives_finalize(manager):
    manager.feed_audio("c1", b"\x00")
    _wait(lambda: manager._test_sockets)
    assert manager.end("c1")
    assert _wait(lambda: manager._test_sockets[0].closed)
    assert manager._test_finalized == ["c1"], "server-driven finalize fires on relay close"
    assert manager.surface("c1") is None, "registry slot released"


# ---------- reconnect + our-side replay ----------

def test_socket_drop_reconnects_and_replay_block_pending(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    s1 = manager._test_sockets[0]
    s1.push({"type": "user_transcript", "user_transcription_event": {"user_transcript": "check the telemetry"}})
    s1.push({"type": "agent_response", "agent_response_event": {"agent_response": "on it"}})
    _wait(lambda: len(manager.events("c1", 0)[0]) >= 2)
    s1.fail_sends = True                       # EL socket dies
    manager.feed_audio("c1", b"\x01" * 10)     # triggers outage handling
    assert _wait(lambda: len(manager._test_sockets) >= 2, timeout=3), "reconnect spawns a fresh socket"
    assert _wait(lambda: any(e["type"] == "reconnecting" for e in manager.events("c1", 0)[0])), \
        "outage surfaced via reconnecting event (never a silent drop)"
    block = manager.replay_block("c1")
    assert "check the telemetry" in block and "on it" in block, "our-side replay carries pre-drop turns"
    assert manager.replay_block("c1") == "", "replay block is consume-once"


# ---------- proxy seams ----------

def test_flag_off_routes_absent_and_inert(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    mod = _load_proxy()
    c = mod.app.test_client()
    assert c.post("/ptt/stream/audio").status_code == 404
    assert c.get("/ptt/stream/events").status_code == 404
    assert c.post("/ptt/stream/end").status_code == 404
    assert not hasattr(mod, "_STREAM_RELAY") or mod._STREAM_RELAY is None


def test_flag_on_routes_present_loopback_only(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    c = mod.app.test_client()
    r = c.post("/ptt/stream/audio", data=b"\x00\x01", headers={"X-Conversation-Id": "cX"},
               environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert r.status_code == 403, "loopback-only trust boundary"
    r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50, headers={"X-Conversation-Id": "cX"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"},
               content_type="application/octet-stream")
    # fake EL absent in the test env is fine; a bare CI runner has NO brain/vendor at all -> 503 vendor_unavailable
    assert r.status_code == 200 or r.get_json().get("error") in ("socket", "vendor_unavailable")
    mod._STREAM_RELAY.shutdown()


def test_journal_stamp_prefers_relay_registry(monkeypatch, tmp_path):
    """Watch-stamp carve-out: relay knowledge beats active-surface inference; stamped exactly once."""
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)      # no active-surface.json at all
    # register a relay conversation
    mod._STREAM_RELAY.registry.claim("convW")
    mod._STREAM_RELAY._surface["convW"] = "watch"
    non_system = [{"role": "user", "content": "hello arturo"},
                  {"role": "assistant", "content": "hey"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="convW")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert d["surface"]["device"] == "watch" and d["surface"].get("via") == "relay"
    mod._STREAM_RELAY.shutdown()


def test_replay_block_reaches_context(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    mod._STREAM_RELAY.registry.claim("convR")
    mod._STREAM_RELAY._replay.setdefault("convR", sr.ptt_stream.ReplayLog()).add("user", "pre-drop question")
    mod._STREAM_RELAY._needs_replay.add("convR")
    block = mod._STREAM_RELAY.replay_block("convR")
    assert "pre-drop question" in block
    mod._STREAM_RELAY.shutdown()


def test_partials_flag_wires_engine(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    monkeypatch.setenv("ARTURO_STREAM_PARTIALS", "1")
    calls = []
    mod_sr = sr
    monkeypatch.setattr(mod_sr, "_default_scribe", lambda pcm: (calls.append(len(pcm)) or "live words"))
    sockets = []
    m = sr.RelayManager(socket_factory=lambda cid: sockets.append(FakeELSocket()) or sockets[-1],
                        on_finalize=lambda cid: None, cap=3, idle_ttl_s=600,
                        partials_enabled=True, partials_cadence_s=0.05)
    try:
        for _ in range(4):
            m.feed_audio("c1", b"\x00" * 8000)
        assert _wait(lambda: any(e["type"] == "user_partial" for e in m.events("c1", 0)[0]), timeout=3), \
            "partials module emits user_partial events on the same stream"
        ev = [e for e in m.events("c1", 0)[0] if e["type"] == "user_partial"][0]
        assert ev["text"] == "live words" and ev["revision"] >= 1
    finally:
        m.shutdown()


def test_partials_flag_off_no_engine(manager):
    manager.feed_audio("c1", b"\x00" * 8000)
    time.sleep(0.2)
    assert not any(e["type"] == "user_partial" for e in manager.events("c1", 0)[0])


# ---------- smoke-found defect (msg_2c36c4d2): EL socket callbacks carry NO conv_id ----------
# Root cause: the phone path's extra_body conv-id stamping does not ride socket-initiated
# conversations, so the custom-LLM callback arrives with EL's own id or NOTHING. The relay
# must therefore (a) learn EL's id from conversation_initiation_metadata (criterion #5's
# EL<->ours mapping), (b) resolve callback ids at the proxy seams, (c) fall back to the SOLE
# live relay conversation when the callback is id-less (refuse when ambiguous), and (d) ADOPT
# our conv_id onto the journal so finalize/ended-once/brain all key correctly.

def test_el_metadata_maps_el_id_to_ours(manager):
    manager.feed_audio("ourC", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    manager._test_sockets[0].push({"type": "conversation_initiation_metadata",
        "conversation_initiation_metadata_event": {"conversation_id": "el_abc123"}})
    assert _wait(lambda: manager.resolve("el_abc123") == "ourC"), "EL id must map to our conv_id"
    assert manager.resolve("ourC") == "ourC", "our own id resolves to itself"
    assert manager.resolve("unknown-id") is None


def test_sole_live_fallback_unambiguous_only(manager):
    manager.feed_audio("c1", b"\x00")
    assert manager.sole_live() == "c1"
    manager.feed_audio("c2", b"\x00")
    assert manager.sole_live() is None, "two live conversations -> ambiguous, refuse"


def test_journal_adopts_conv_and_surface_when_callback_idless(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)
    mod._STREAM_RELAY.registry.claim("watchC")
    mod._STREAM_RELAY._surface["watchC"] = "watch"
    non_system = [{"role": "user", "content": "can you hear me okay"},
                  {"role": "assistant", "content": "yes clearly"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="")  # id-less callback
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert d["surface"] == {"device": "watch", "via": "relay"}
    assert d["conv_id"] == "watchC", "journal must ADOPT our conv_id (fixes finalize/ended-once keying)"
    mod._STREAM_RELAY.shutdown()


def test_journal_via_el_id_mapping(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)
    mod._STREAM_RELAY.registry.claim("watchD")
    mod._STREAM_RELAY._surface["watchD"] = "watch"
    mod._STREAM_RELAY.map_el_id("el_xyz", "watchD")
    non_system = [{"role": "user", "content": "hello there arturo"},
                  {"role": "assistant", "content": "hey"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="el_xyz")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert d["surface"] == {"device": "watch", "via": "relay"}
    assert d["conv_id"] == "watchD", "EL-id callback resolves through the mapping to OUR id"
    mod._STREAM_RELAY.shutdown()


# ---------- connect cooldown: a holder that can't open an EL socket must NOT re-handshake ----------
# Live-outage root cause (2026-09-08 03:33-03:44Z, EL 429 storm): once EL 429s the WS handshake,
# feed()->ensure_socket() re-attempted the handshake on EVERY uplink chunk (no cooldown), so the
# client's ~0.5s POST cadence hammered EL and pinned the per-key rate-limit window open. Fix: a
# minimum interval between connect attempts on the feed path (the _reconnect loop keeps its own
# exponential backoff and bypasses this).

def test_failed_connect_does_not_rehandshake_every_chunk():
    attempts = []
    def failing_factory(cid):
        attempts.append(time.time())
        raise RuntimeError("Handshake status 429 Too Many Requests")
    m = sr.RelayManager(socket_factory=failing_factory, on_finalize=lambda cid: None,
                        cap=3, idle_ttl_s=600, connect_cooldown_s=2.0)
    try:
        for _ in range(20):                     # client hammers uplink chunks
            assert m.feed_audio("c1", b"\x00" * 10)["ok"], "buffered, never a hard error on outage"
        assert len(attempts) <= 2, f"handshake attempted {len(attempts)}x in a burst — cooldown missing"
    finally:
        m.shutdown()


def test_connect_cooldown_allows_retry_after_interval():
    attempts = []
    def failing_factory(cid):
        attempts.append(time.time())
        raise RuntimeError("Handshake status 429")
    m = sr.RelayManager(socket_factory=failing_factory, on_finalize=lambda cid: None,
                        cap=3, idle_ttl_s=600, connect_cooldown_s=0.2)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        n1 = len(attempts)
        time.sleep(0.3)                          # cooldown lapses
        m.feed_audio("c1", b"\x00" * 10)
        assert len(attempts) > n1, "a new handshake is allowed once the cooldown interval passes"
    finally:
        m.shutdown()


# ---------- tombstone: late feed after end must NOT resurrect an ended conversation ----------
# Root cause (log-proven, spec msg_3608319d): build-206 kept POSTing ~43s after End; the old
# feed_audio() re-claimed the released slot and re-created a holder + re-added _surface, so a
# DEAD conversation came back live. With a second genuinely-live conversation that made
# sole_live() see 2 entries and refuse -> the callback journalled conv=None (vc_e190a19387e0...).
# Fix: end() tombstones the id (TTL); feed_audio() rejects a tombstoned id with error 'ended'
# (route -> 410, a hard stop for the client) instead of resurrecting it.

def test_end_tombstones_late_feed_rejected(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    assert manager.end("c1")
    n_sockets = len(manager._test_sockets)
    r = manager.feed_audio("c1", b"\x00" * 10)             # the late build-206-style callback
    assert not r["ok"] and r["error"] == "ended", "post-end feed rejected as ended, not re-claimed"
    assert manager.surface("c1") is None, "ended conversation NOT resurrected into the surface"
    assert manager.sole_live() is None, "no live conversation was recreated"
    assert len(manager._test_sockets) == n_sockets, "no fresh EL socket opened for a dead conversation"


def test_fresh_conversation_after_end_still_accepted(manager):
    manager.feed_audio("c1", b"\x00" * 10)
    _wait(lambda: manager._test_sockets)
    assert manager.end("c1")
    assert manager.feed_audio("c2", b"\x00" * 10)["ok"], "a genuinely new conversation is unaffected"
    assert manager.surface("c2") == "watch"
    assert manager.sole_live() == "c2", "the fresh conversation is the sole live one (no zombie c1)"


def test_tombstone_expires_after_ttl_id_reusable():
    sockets = []
    m = sr.RelayManager(socket_factory=lambda cid: sockets.append(FakeELSocket()) or sockets[-1],
                        on_finalize=lambda cid: None, cap=3, idle_ttl_s=600, tombstone_ttl_s=0.2)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert m.end("c1")
        assert m.feed_audio("c1", b"\x00" * 10)["error"] == "ended", "tombstoned inside TTL"
        time.sleep(0.3)
        assert m.feed_audio("c1", b"\x00" * 10)["ok"], "same id is reusable after the tombstone TTL lapses"
    finally:
        m.shutdown()


def test_route_tombstoned_feed_returns_410(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    mod._STREAM_RELAY.registry.claim("cZ")
    mod._STREAM_RELAY._surface["cZ"] = "watch"
    mod._STREAM_RELAY.end("cZ")                              # tombstone it
    c = mod.app.test_client()
    r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50, headers={"X-Conversation-Id": "cZ"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"}, content_type="application/octet-stream")
    assert r.status_code == 410, "tombstoned conversation -> 410 Gone (client hard-stops)"
    assert r.get_json().get("error") == "ended"
    mod._STREAM_RELAY.shutdown()


def test_journal_refuses_ambiguous_idless(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    monkeypatch.setattr(mod, "VOICE_CALLS_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARTURO_STATE", tmp_path)
    for c in ("wa", "wb"):
        mod._STREAM_RELAY.registry.claim(c)
        mod._STREAM_RELAY._surface[c] = "watch"
    non_system = [{"role": "user", "content": "hello hello hello"},
                  {"role": "assistant", "content": "hi"}]
    cid = mod._resolve_or_create(non_system, page="voice", origin="funnel", conv_id="")
    d = json.loads((tmp_path / f"{cid}.json").read_text())
    assert "surface" not in d and not d.get("conv_id"), "two live relays + id-less callback = refuse attribution"
    mod._STREAM_RELAY.shutdown()


# ---------- item(1) X-Surface surface stamping (DEC-1789341362142252, UA superseded) ----------

def test_normalize_surface_maps_phone_and_watch():
    # fixtures are the LITERAL X-Surface header values the app sends (RelayTransport.swift:108)
    assert sr.normalize_surface("phone") == "phone"
    assert sr.normalize_surface("watch") == "watch"


def test_normalize_surface_defaults_unknown_and_missing_to_watch():
    assert sr.normalize_surface(None) == "watch"
    assert sr.normalize_surface("") == "watch"
    assert sr.normalize_surface("OrchestraOS/1.86") == "watch"


def test_feed_audio_stamps_phone_surface_from_arg(manager):
    manager.feed_audio("c1", b"\x00" * 10, surface_device="phone")
    assert manager.surface("c1") == "phone"


def test_feed_audio_defaults_surface_to_watch(manager):
    # backward-compat: no header (watch sends none) -> watch, same as before this change
    manager.feed_audio("c1", b"\x00" * 10)
    assert manager.surface("c1") == "watch"


def test_feed_audio_unknown_surface_defaults_to_watch(manager):
    manager.feed_audio("c1", b"\x00" * 10, surface_device="bogus")
    assert manager.surface("c1") == "watch"


def test_feed_audio_surface_stamped_once_at_creation(manager):
    manager.feed_audio("c1", b"\x00" * 10, surface_device="phone")
    manager.feed_audio("c1", b"\x00" * 10, surface_device="watch")   # a later chunk must NOT re-stamp
    assert manager.surface("c1") == "phone"


def test_journal_surface_phone_is_relay_and_skips_active_surface_fallback():
    # addition (2): a phone relay call attributes from the relay registry and does NOT consult
    # active-surface.json (which stays the WATCH-ONLY writer).
    called = []
    fallback = lambda: (called.append(1), {"device": "watch", "via": "inference"})[1]
    assert sr.journal_surface("phone", fallback) == {"device": "phone", "via": "relay"}
    assert called == []


def test_journal_surface_watch_is_relay():
    assert sr.journal_surface("watch", lambda: {"device": "x", "via": "y"}) == {"device": "watch", "via": "relay"}


def test_journal_surface_non_relay_consults_fallback():
    sentinel = {"device": "web", "via": "inference"}
    assert sr.journal_surface(None, lambda: sentinel) is sentinel
