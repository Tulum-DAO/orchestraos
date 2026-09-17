"""RED-first — server-held answered-final memory (ios 221 grade msg_3a5b4d4b, call
vc_13d96b7a5d1f6c6b): Hume double-POSTed the CLM request, F2 answered the dup with an
EMPTY SSE, and ~57s later Hume RETRIED with the IDENTICAL 21-message history — so the
earlier answered pair never appears in the history and every history-scanning guard
(BUG-3, superset) is structurally blind. The fix keys on OUR memory, not Hume's window:
if the latest user final normalizes identical to the final we LAST ANSWERED on this
conversation, the re-answer is suppressed regardless of age, until a DIFFERENT final is
answered. Short confirmations (min_tokens) are never deduped — the operator repeats those."""
import importlib.util
import pathlib

from services.arturo import voice_guards as vg


def test_memory_repeat_detection_and_overwrite():
    m = vg.AnsweredFinalMemory()
    long_q = "is it possible to use the transcript players methodology for highlighting words"
    assert not m.is_answered_repeat("c1", long_q)          # nothing answered yet
    m.record_answered("c1", long_q)
    assert m.is_answered_repeat("c1", long_q)              # identical => repeat, any age
    assert m.is_answered_repeat("c1", long_q.upper() + ".")  # normalized match
    assert not m.is_answered_repeat("c2", long_q)          # per-conversation key
    m.record_answered("c1", "have an update about the final turn style question")
    assert not m.is_answered_repeat("c1", long_q)          # different final answered => forgotten


def test_short_confirmations_never_deduped():
    m = vg.AnsweredFinalMemory()
    m.record_answered("c1", "yes do it")                   # < min_tokens: never recorded
    assert not m.is_answered_repeat("c1", "yes do it")


def _deltas(sse_text):
    """Extract non-empty content deltas from an SSE body."""
    import json as _json
    out = []
    for line in sse_text.splitlines():
        if line.startswith("data: ") and "[DONE]" not in line:
            try:
                c = _json.loads(line[6:]).get("choices", [{}])[0].get("delta", {}).get("content", "")
                if c:
                    out.append(c)
            except Exception:
                pass
    return out


def test_clm_seam_suppresses_identical_history_retry(monkeypatch, tmp_path):
    # The incident shape end-to-end: the same hume CLM request POSTed again after the
    # first was fully answered — beyond every TTL (F2 defeated below), with the identical
    # history — must stream an EMPTY SSE (done-only), not re-answer.
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_afm", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Msg:
        tool_calls = None
        content = "first answer"

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            return _Resp()

    # the brain seam (services/arturo/brain.py) replaced the bare openai client
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _Completions().create(**kw))
    # defeat F2's 15s TTL so this models the 57s retry, not the immediate double-POST
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)

    c = mod.app.test_client()
    body = {"messages": [
        {"role": "user", "content": "hello there arturo my friend"},
        {"role": "assistant", "content": "hey shaw, what do you need"},
        {"role": "user", "content": "is it possible to use the transcript players methodology for highlighting words"},
    ]}
    q = "/v1/chat/completions?custom_session_id=CIDAFM1"
    # Hermetic bearer: the proxy reads CUSTOM_LLM_BEARER from <ORCHESTRA_DIR>/.env.secrets;
    # a clean checkout has none (this test used to pass only on a host with a live file).
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    hdrs = {"Authorization": f"Bearer {mod.BEARER_TOKEN}"}
    r1 = c.post(q, json=body, headers=hdrs, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r1.status_code == 200
    assert _deltas(r1.get_data(as_text=True)), "first request must answer"
    r2 = c.post(q, json=body, headers=hdrs, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r2.status_code == 200
    assert _deltas(r2.get_data(as_text=True)) == [], (
        "identical answered final re-answered — the retry must stream done-only")
