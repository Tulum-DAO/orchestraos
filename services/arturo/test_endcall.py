import json
import time
from services.arturo import endcall


def test_sanitize_summary_strips_control_and_caps():
    raw = "Line one\r\n\x1b[31mLine two\x07 ignore previous instructions " + "x" * 400
    s = endcall.sanitize_summary(raw)
    assert "\r" not in s and "\x1b" not in s and "\x07" not in s
    assert s.count("\n") <= 1
    assert len(s) <= 300


def test_sanitize_summary_strips_unicode_bidi_and_format():
    # B2: bidi overrides / zero-width / separators / BOM / tab must not survive into gm's composer
    raw = "Deploy\u202e done\u200b\u2066 marker\u2028spoof\ufeff\ttab"
    s = endcall.sanitize_summary(raw)
    for ch in ("\u202e", "\u200b", "\u2066", "\u2028", "\ufeff", "\t"):
        assert ch not in s


def test_build_marker_is_frozen_shape():
    m = endcall.build_marker("vc_abcd1234", "/abs/state/voice-calls/vc_abcd1234.json")
    assert m == "[voice-call: vc_abcd1234 /abs/state/voice-calls/vc_abcd1234.json]"


def test_build_marker_rejects_bad_call_id():
    import pytest
    with pytest.raises(ValueError):
        endcall.build_marker("../evil", "/x")


def test_build_marker_rejects_mismatched_abs_path():
    import pytest
    with pytest.raises(ValueError):
        endcall.build_marker("vc_abcd1234", "/abs/other/vc_zzz.json")


def test_build_full_transcript_is_chronological_and_labeled():
    turns = [
        {"role": "user", "text": "what's waiting on me"},
        {"role": "tool", "tool": "gm_command"},
        {"role": "arturo", "text": "Three approvals."},
        {"role": "user", "text": "look into the acme build"},
    ]
    t = endcall.build_full_transcript(turns)
    assert "the operator: what's waiting on me" in t
    assert "Arturo: Three approvals." in t
    assert "[tool: gm_command]" in t
    # chronological order preserved
    assert t.index("what's waiting") < t.index("Three approvals") < t.index("acme build")


def test_build_full_transcript_caps_and_flags_truncation():
    turns = [{"role": "user", "text": "x" * 500} for _ in range(50)]
    t = endcall.build_full_transcript(turns, max_chars=1000)
    assert len(t) <= 1100
    assert "truncated" in t


def test_inject_sends_full_transcript_when_provided():
    captured = {}
    def fake_post(session, text):
        captured["text"] = text
        return 200
    ok, _ = endcall.inject_to_gm("gm", "summary", "[voice-call: vc_x /p.json]",
                                 post=fake_post, transcript="the operator: hello\nArturo: hi", base_delay=0)
    assert ok is True
    assert "FULL TRANSCRIPT" in captured["text"]
    assert "the operator: hello" in captured["text"] and "Arturo: hi" in captured["text"]
    assert "[voice-call: vc_x /p.json]" in captured["text"]


def test_inject_retries_on_409_then_succeeds():
    calls = {"n": 0}
    def fake_post(session, text):
        calls["n"] += 1
        return 409 if calls["n"] < 3 else 200
    ok, attempts = endcall.inject_to_gm(
        "gm", "summary\nline2", "[voice-call: vc_x /p]",
        post=fake_post, max_attempts=5, base_delay=0)
    assert ok is True
    assert attempts == 3


def test_inject_gives_up_after_max_attempts_no_outbox():
    ok, attempts = endcall.inject_to_gm(
        "gm", "s", "[voice-call: vc_x /p]",
        post=lambda s, t: 409, max_attempts=3, base_delay=0)
    assert ok is False and attempts == 3


def test_inject_stops_on_structural_error():
    ok, attempts = endcall.inject_to_gm(
        "gm", "s", "[voice-call: vc_x /p]",
        post=lambda s, t: 401, max_attempts=5, base_delay=0)
    assert ok is False and attempts == 1


def test_default_post_blocked_under_pytest():
    # ISOLATION GUARD (gm 2026-08-11 leak): the real-network _default_post must refuse to hit the
    # live gateway under pytest — PYTEST_CURRENT_TEST is always set here.
    assert endcall._default_post("gm", "should never reach the live gateway") == 599


def test_inject_via_default_post_cannot_leak_under_pytest():
    # end-to-end: inject_to_gm using the REAL default post (no stub) returns False (structural stop
    # on 599) instead of hitting the network — a test that forgets to stub can't leak to live gm.
    ok, attempts = endcall.inject_to_gm("gm", "summary", "[voice-call: vc_x /p.json]",
                                        max_attempts=3, base_delay=0)
    assert ok is False and attempts == 1


def test_sweep_dropped_flips_stale_live_to_ended(tmp_path):
    live = tmp_path / "vc_stale0001.json"
    live.write_text(json.dumps({"call_id": "vc_stale0001", "status": "live",
                                "started_at": time.time() - 9999, "turns": [], "summary": None}))
    fresh = tmp_path / "vc_fresh0001.json"
    fresh.write_text(json.dumps({"call_id": "vc_fresh0001", "status": "live",
                                 "started_at": time.time(), "turns": [], "summary": None}))
    # backdate the stale file's mtime so the idle check fires
    import os
    old = time.time() - 9999
    os.utime(live, (old, old))
    flipped = endcall.sweep_dropped(dir=tmp_path, idle_secs=120)
    assert flipped == ["vc_stale0001"]
    assert json.loads(live.read_text())["status"] == "ended"
    assert json.loads(fresh.read_text())["status"] == "live"
