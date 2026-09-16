"""RED-first — events-poll page cap (ios GO msg_2f3e8d3c, downlink-amplification incident
vc_2b106dfe06dbec74: same-cursor long-polls re-transferred 543KB then 1.1MB of audio-event
backlog over a degraded watch link, starving the uplink until the app died). Contract:
GET /ptt/stream/events?cursor=N&wait=S&max_bytes=B&max_events=E — server defaults apply
even without params: <=128KB of event payload or <=8 AUDIO events per response, whichever
first; cursor-ordered slice that stops BEFORE the event that would exceed a cap (events
never split); the response cursor is that of the LAST INCLUDED event (client-authoritative);
'more': true is present when truncated so the client re-polls immediately with wait=0.
Progress guarantee: the first event is always included even if alone it exceeds max_bytes."""
import importlib.util
import json
import pathlib

from services.arturo import ptt_stream


def _buf_with(events):
    b = ptt_stream.EventBuffer()
    for e in events:
        b.put("c1", e)
    return b


def _audio(n_bytes=20000):
    return {"type": "audio", "audio": "A" * n_bytes}


def test_audio_event_count_cap():
    b = _buf_with([_audio(10)] * 12 + [{"type": "assistant_end"}])
    events, cursor, more = b.since_page("c1", 0, max_bytes=10**9, max_audio=8)
    assert len([e for e in events if e["type"] == "audio"]) == 8
    assert more is True
    assert cursor == 8                       # last included event's own cursor
    rest, cursor2, more2 = b.since_page("c1", cursor, max_bytes=10**9, max_audio=8)
    assert [e["type"] for e in rest] == ["audio"] * 4 + ["assistant_end"]
    assert more2 is False and cursor2 == 13


def test_byte_cap_stops_before_exceeding():
    b = _buf_with([_audio(50000), _audio(50000), _audio(50000)])
    events, cursor, more = b.since_page("c1", 0, max_bytes=120000, max_audio=100)
    assert len(events) == 2 and more is True and cursor == 2
    # non-audio events count toward bytes too but never toward the audio cap
    b2 = _buf_with([{"type": "agent_response", "text": "x" * 60000},
                    _audio(50000), _audio(50000)])
    ev2, cur2, more2 = b2.since_page("c1", 0, max_bytes=120000, max_audio=100)
    assert len(ev2) == 2 and more2 is True


def test_progress_guarantee_single_oversized_event():
    b = _buf_with([_audio(500000), _audio(10)])
    events, cursor, more = b.since_page("c1", 0, max_bytes=1000, max_audio=8)
    assert len(events) == 1 and cursor == 1 and more is True


def test_empty_and_uncapped_paths_match_since():
    b = _buf_with([{"type": "barge_in"}, _audio(10)])
    assert b.since_page("cX", 0, max_bytes=131072, max_audio=8) == ([], 0, False)
    events, cursor, more = b.since_page("c1", 0, max_bytes=131072, max_audio=8)
    legacy_events, legacy_cursor = b.since("c1", 0)
    assert (events, cursor, more) == (legacy_events, legacy_cursor, False)


def test_route_defaults_cap_and_more_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_evcap", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        for _ in range(12):
            mod._STREAM_RELAY.buffer.put("cPage", _audio(10))
        mod._STREAM_RELAY.buffer.put("cPage", {"type": "assistant_end"})
        c = mod.app.test_client()
        r = c.get("/ptt/stream/events?cursor=0", headers={"X-Conversation-Id": "cPage"},
                  environ_base={"REMOTE_ADDR": "127.0.0.1"})
        d = r.get_json()
        assert r.status_code == 200 and d["ok"]
        assert len([e for e in d["events"] if e["type"] == "audio"]) == 8, \
            "server default must cap audio events at 8 even without params"
        assert d.get("more") is True and d["cursor"] == 8
        r2 = c.get(f"/ptt/stream/events?cursor={d['cursor']}",
                   headers={"X-Conversation-Id": "cPage"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
        d2 = r2.get_json()
        assert [e["type"] for e in d2["events"]] == ["audio"] * 4 + ["assistant_end"]
        assert "more" not in d2, "'more' is present only when truncated"
        # explicit tiny max_bytes honored (each event serializes to ~40B; 50B fits one)
        r3 = c.get("/ptt/stream/events?cursor=0&max_bytes=50&max_events=8",
                   headers={"X-Conversation-Id": "cPage"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
        d3 = r3.get_json()
        assert len(d3["events"]) == 1 and d3.get("more") is True
    finally:
        mod._STREAM_RELAY.shutdown()
