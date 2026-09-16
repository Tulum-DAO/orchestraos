from services.arturo import record


def test_merge_streams_orders_by_ts():
    client_turns = [
        {"role": "user", "text": "what's this agent stuck on?", "ts": 5.0},
        {"role": "arturo", "text": "waiting at OAuth", "ts": 8.0},
    ]
    server_tools = [{"role": "tool", "tool": "read_screen_context", "ts": 7.0}]
    surface_events = [
        {"ts": 3.0, "focused": {"kind": "agent", "id": "acme-dev"}},
        {"ts": 20.0, "focused": {"kind": "project", "id": "globex"}},
    ]
    merged = record.merge_streams(client_turns, server_tools, surface_events)
    kinds = [e["_stream"] for e in merged]
    tss = [e["ts"] for e in merged]
    assert tss == sorted(tss)
    assert kinds[0] == "surface"      # ts 3.0 first
    assert "tool" in kinds
    assert "turn" in kinds


def test_diarize_renders_interleaved_lines():
    merged = [
        {"_stream": "surface", "ts": 3.0, "focused": {"kind": "agent", "id": "acme-dev"}},
        {"_stream": "turn", "ts": 5.0, "role": "user", "text": "stuck on?"},
        {"_stream": "tool", "ts": 7.0, "tool": "read_screen_context"},
        {"_stream": "turn", "ts": 8.0, "role": "arturo", "text": "OAuth"},
    ]
    text = record.diarize(merged)
    lines = text.strip().splitlines()
    assert "SCREEN" in lines[0] and "acme-dev" in lines[0]
    assert "the operator:" in lines[1]
    assert "[tool: read_screen_context]" in lines[2]
    assert "Arturo:" in lines[3]


def test_extract_buckets_user_intent():
    merged = [
        {"_stream": "turn", "ts": 5.0, "role": "user", "text": "can you send the report to Jodie?"},
        {"_stream": "turn", "ts": 9.0, "role": "user", "text": "let's go with the orange dot"},
        {"_stream": "turn", "ts": 12.0, "role": "user", "text": "stop using filler words"},
        {"_stream": "turn", "ts": 15.0, "role": "arturo", "text": "done"},
    ]
    ex = record.extract_asks_decisions_feedback(merged)
    assert any("Jodie" in a["text"] and a["ts"] == 5.0 for a in ex["asks"])
    assert any("orange dot" in d["text"] for d in ex["decisions"])
    assert any("filler" in f["text"] for f in ex["feedback"])


def test_build_injection_payload_is_bounded_and_summary_only():
    merged = [
        {"_stream": "turn", "ts": 5.0, "role": "user", "text": "send it to Jodie"},
        {"_stream": "turn", "ts": 6.0, "role": "arturo", "text": "on it"},
    ]
    ex = record.extract_asks_decisions_feedback(merged)
    payload = record.build_injection_payload(
        call_id="vc_client_abc", abs_path="/x/vc_client_abc.json",
        counts={"turns": 2, "tools": 1, "surfaces": 3}, merged=merged, extraction=ex)
    assert "[voice-call: vc_client_abc /x/vc_client_abc.json]" in payload
    assert "send it to Jodie" in payload   # ask survives (not collapsed to 2 lines)
    assert len(payload) < 2000             # bounded: never the full timeline
    assert "SCREEN →" not in payload       # no diarized timeline inlined


def test_build_injection_payload_strips_control_chars():
    merged = [{"_stream": "turn", "ts": 5.0, "role": "user",
               "text": "send \x1b[31mred\x07 to Jodie\u202e"}]
    ex = record.extract_asks_decisions_feedback(merged)
    payload = record.build_injection_payload(
        "vc_client_abc", "/x/vc_client_abc.json",
        {"turns": 1, "tools": 0, "surfaces": 0}, merged, ex)
    assert "\x1b" not in payload and "\x07" not in payload and "\u202e" not in payload


def test_is_genuine_shaw_gate():
    assert record.is_genuine_shaw({"origin": "funnel"}) is True
    assert record.is_genuine_shaw({"source": "client"}) is True
    assert record.is_genuine_shaw({"origin": "local"}) is False
    assert record.is_genuine_shaw({"origin": "test"}) is False
    assert record.is_genuine_shaw({}) is False
