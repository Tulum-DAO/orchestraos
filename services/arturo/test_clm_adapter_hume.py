"""RED-first — Hume Task 8: CLM adapter hygiene in arturo-proxy.py (spec §4.3).
Hume's OpenAI-compat CLM client differs from EL's: it keys the session by a
?custom_session_id= query param, appends a trailing {prosody} block to user content,
and is strict about SSE chunk shape (created/model ints+strings, assistant role in the
delta, and NO tool_calls deltas — tools run server-side, text-only streams out).
Kept out of test_stream_relay.py so the EL baseline (25) is untouched."""
import importlib.util
import json
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_clm", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_custom_session_id_query_fallback():
    mod = _load_proxy()
    # body ids win; the Hume query param is the FALLBACK when the body carries nothing
    assert mod._clm_conversation_id({"system__conversation_id": "el1"}, {},
                                    {"custom_session_id": "hu1"}) == "el1"
    assert mod._clm_conversation_id({}, {"conversation_id": "meta1"},
                                    {"custom_session_id": "hu1"}) == "meta1"
    assert mod._clm_conversation_id({}, {}, {"custom_session_id": "hu1"}) == "hu1"
    assert mod._clm_conversation_id({}, {}, {}) == ""
    # bounded like the body ids (defensive cap at 200)
    assert len(mod._clm_conversation_id({}, {}, {"custom_session_id": "x" * 500})) == 200


def test_strip_hume_prosody_suffix_user_only_hume_only():
    mod = _load_proxy()
    msgs = [
        {"role": "user", "content": "what's on my plate today {calm, slightly curious}"},
        {"role": "assistant", "content": "Two decisions {not prosody, keep me}"},
        {"role": "user", "content": "no suffix here"},
    ]
    out = mod._strip_hume_prosody(msgs, is_hume=True)
    assert out[0]["content"] == "what's on my plate today"
    assert out[1]["content"] == "Two decisions {not prosody, keep me}"  # assistant untouched
    assert out[2]["content"] == "no suffix here"
    # NON-hume requests pass through byte-identical (EL user might legitimately end with braces)
    same = mod._strip_hume_prosody(msgs, is_hume=False)
    assert same[0]["content"].endswith("{calm, slightly curious}")
    # the original list is never mutated (journal seams may hold a reference)
    assert msgs[0]["content"].endswith("{calm, slightly curious}")


def test_sse_chunk_hygiene_created_model_role_no_tool_calls():
    mod = _load_proxy()
    chunk = mod.make_sse_chunk("hello there")
    body = json.loads(chunk.split("data: ", 1)[1].strip())
    assert isinstance(body["created"], int) and body["created"] > 0
    assert isinstance(body["model"], str) and body["model"]
    delta = body["choices"][0]["delta"]
    assert delta["role"] == "assistant" and delta["content"] == "hello there"
    assert "tool_calls" not in delta
    done_first, done_marker = mod.make_sse_done().split("\n\n")[:2]
    done_body = json.loads(done_first.split("data: ", 1)[1])
    assert isinstance(done_body["created"], int) and isinstance(done_body["model"], str)
    assert "tool_calls" not in done_body["choices"][0]["delta"]
    assert done_body["choices"][0]["finish_reason"] == "stop"
    assert done_marker == "data: [DONE]"
