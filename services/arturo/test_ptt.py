# RED-first tests for the pure PTT helpers (services/arturo/ptt.py).
# Push-to-talk endpoint (SPEC_watch-ptt-endpoint.md). These cover the mechanical, I/O-free pieces:
# request validation, the bounded per-conversation_id history, turn_id dedup, and message assembly.
# Vendor calls (STT/brain/TTS) and the no-call-lifecycle guarantee are tested at the arturo-proxy
# orchestration layer (test_ptt_turn.py), not here.

from services.arturo import ptt


# ---- request validation ---------------------------------------------------------------------

def test_validate_audio_accepts_m4a_within_limits():
    ok, err = ptt.validate_audio(60_000, "audio/m4a")
    assert ok is True and err is None


def test_validate_audio_rejects_oversize():
    ok, err = ptt.validate_audio(ptt.MAX_AUDIO_BYTES + 1, "audio/m4a")
    assert ok is False and err == "too_large"


def test_validate_audio_rejects_empty():
    ok, err = ptt.validate_audio(0, "audio/m4a")
    assert ok is False and err == "empty"


def test_validate_audio_rejects_unsupported_type():
    ok, err = ptt.validate_audio(1000, "video/mp4")
    assert ok is False and err == "bad_type"


def test_validate_audio_allows_octet_stream_fallback():
    # multipart fields from the watch may arrive as octet-stream; the .m4a payload is still valid
    ok, err = ptt.validate_audio(1000, "application/octet-stream")
    assert ok is True and err is None


# ---- per-conversation_id history (bounded, isolated) ----------------------------------------

def test_history_threads_turns_in_order():
    h = ptt.PttHistory(max_turns=10)
    h.append("conv-A", "user", "hello")
    h.append("conv-A", "assistant", "hi there")
    msgs = h.get("conv-A")
    assert msgs == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_history_isolates_conversations():
    h = ptt.PttHistory(max_turns=10)
    h.append("conv-A", "user", "for A")
    h.append("conv-B", "user", "for B")
    assert h.get("conv-A") == [{"role": "user", "content": "for A"}]
    assert h.get("conv-B") == [{"role": "user", "content": "for B"}]


def test_history_is_bounded_dropping_oldest():
    h = ptt.PttHistory(max_turns=2)
    for i in range(4):
        h.append("c", "user", f"m{i}")
    kept = [m["content"] for m in h.get("c")]
    assert kept == ["m2", "m3"]          # oldest evicted, last 2 kept


def test_history_unknown_conversation_is_empty():
    assert ptt.PttHistory().get("nope") == []


def test_history_bounds_number_of_conversations():
    # I2: _by_conv must not grow unbounded in the number of distinct conversation_ids on the
    # long-lived :5071 process — the oldest conversation is evicted (SPEC §6 "evicted on new-conv").
    h = ptt.PttHistory(max_turns=10, max_conversations=2)
    h.append("c1", "user", "a")
    h.append("c2", "user", "b")
    h.append("c3", "user", "c")          # exceeds 2 conversations -> evict oldest (c1)
    assert h.get("c1") == []
    assert h.get("c2") == [{"role": "user", "content": "b"}]
    assert h.get("c3") == [{"role": "user", "content": "c"}]


def test_history_access_refreshes_conversation_recency():
    # touching a conversation (append) keeps it from being the eviction victim
    h = ptt.PttHistory(max_turns=10, max_conversations=2)
    h.append("c1", "user", "a")
    h.append("c2", "user", "b")
    h.append("c1", "user", "a2")         # c1 is now most-recent
    h.append("c3", "user", "c")          # evict the oldest -> c2
    assert h.get("c2") == []
    assert [m["content"] for m in h.get("c1")] == ["a", "a2"]


# ---- turn_id dedup cache --------------------------------------------------------------------

def test_turn_cache_returns_cached_result():
    c = ptt.TurnCache(max_entries=10)
    assert c.get("t1") is None
    c.put("t1", {"reply_text": "cached"})
    assert c.get("t1") == {"reply_text": "cached"}


def test_turn_cache_is_bounded():
    c = ptt.TurnCache(max_entries=2)
    c.put("t1", {"r": 1})
    c.put("t2", {"r": 2})
    c.put("t3", {"r": 3})            # evicts t1
    assert c.get("t1") is None
    assert c.get("t3") == {"r": 3}


# ---- message assembly -----------------------------------------------------------------------

def test_build_messages_system_then_history_then_user():
    history = [
        {"role": "user", "content": "prev q"},
        {"role": "assistant", "content": "prev a"},
    ]
    msgs = ptt.build_messages("SYSTEM CONTEXT", history, "new question")
    assert msgs[0] == {"role": "system", "content": "SYSTEM CONTEXT"}
    assert msgs[1:3] == history
    assert msgs[-1] == {"role": "user", "content": "new question"}


def test_build_messages_trims_history_to_last_n():
    history = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    msgs = ptt.build_messages("S", history, "q", max_history=20)
    # system + 20 history + final user
    assert len(msgs) == 1 + 20 + 1
    assert msgs[1]["content"] == "m10"      # first kept is the 11th (last 20)
    assert msgs[-1] == {"role": "user", "content": "q"}
