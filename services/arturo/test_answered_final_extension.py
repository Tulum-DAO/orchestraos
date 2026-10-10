"""RED-first — answered-final EXTENSION (arturo-voice, gm brief 2026-10-07; replay of watch call
vc_1114f6e34c65c245). Hume ended the operator's turn on a mid-thought pause, Arturo answered the
390-char fragment, and 25 s later Hume sent the 792-char full sentence with a 2-message
history: the fragment and its answer were GONE from the window. latest_is_answered_superset
scans that history, so it was blind, and AnsweredFinalMemory only matched identical text.
Arturo answered the whole thing again from scratch (and re-fired remember_note).

Wanted: the full final is answered ONCE, as a continuation. It is NOT suppressed (that drops
the operator's 81 extra words); the model is told what it already said to the fragment so it does not
repeat itself or redo an action."""
import importlib.util
import pathlib

from services.arturo import voice_guards as vg

FRAG = ("First of all, we still haven't fixed the fact that I want you to speak faster. "
        "Not only that, i'm still unsure of the uh.")
FULL = (FRAG + " Actual latency that we have right now, for opening pages in the Quest app, "
        "it should be sub 300 milliseconds and open in a new window by default.")
GREET = {"role": "assistant", "content": "Hey the operator, Arturo here, what do you need?"}
REPLY1 = "I'm saving that you want me to speak faster and that you're aiming for sub-300ms latency."


def test_memory_reports_extension_with_prior_reply():
    m = vg.AnsweredFinalMemory()
    assert m.answered_extension("c1", FULL) is None            # nothing answered yet
    m.record_answered("c1", FRAG, reply=REPLY1)
    ext = m.answered_extension("c1", FULL)
    assert ext is not None
    assert ext["reply"] == REPLY1
    assert ext["text"] == vg.AnsweredFinalMemory()._key_text(FRAG)
    assert m.answered_extension("c1", FRAG) is None            # identical is a REPEAT, not an extension
    assert m.answered_extension("c2", FULL) is None            # per conversation
    assert m.answered_extension("c1", "something else entirely different here") is None


def test_extension_must_be_word_aligned_and_prior_long_enough():
    m = vg.AnsweredFinalMemory()
    m.record_answered("c1", "open the agent", reply="ok")       # 3 tokens < min_tokens: not recorded
    assert m.answered_extension("c1", "open the agent page for gm please") is None
    m.record_answered("c1", "tell me about the deploy", reply="sure")
    assert m.answered_extension("c1", "tell me about the deployment status") is None   # mid-word


def _deltas(sse_text):
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


def _load(monkeypatch, tmp_path, answers, seen):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_ext", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    it = iter(answers)

    class _Completions:
        def create(self, **kw):
            seen.append(kw["messages"])
            text = next(it)

            class _Msg:
                tool_calls = None
                content = text

            class _Choice:
                finish_reason = "stop"
                message = _Msg()

            class _Resp:
                choices = [_Choice()]
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _Completions().create(**kw))
    # hermetic bearer: a clean checkout has no CUSTOM_LLM_BEARER in <ORCHESTRA_DIR>/.env.secrets
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    return mod


def test_clm_seam_answers_full_final_once_as_continuation(monkeypatch, tmp_path):
    seen = []
    mod = _load(monkeypatch, tmp_path, [REPLY1, "And on latency: new windows by default, noted."], seen)
    c = mod.app.test_client()
    q = "/v1/chat/completions?custom_session_id=CIDEXT1"
    hdrs = {"Authorization": f"Bearer {mod.BEARER_TOKEN}"}
    env = {"REMOTE_ADDR": "127.0.0.1"}
    # Hume's real shape on vc_1114f6e34c65c245 ("Request: 2 messages" both times): the
    # on_new_chat greeting + ONLY the latest user final; the fragment and its answer are gone.
    r1 = c.post(q, json={"messages": [GREET, {"role": "user", "content": FRAG}]}, headers=hdrs, environ_base=env)
    assert _deltas(r1.get_data(as_text=True)), "fragment is answered (it looked like a full turn)"
    r2 = c.post(q, json={"messages": [GREET, {"role": "user", "content": FULL}]}, headers=hdrs, environ_base=env)
    assert _deltas(r2.get_data(as_text=True)), "the full final must be answered, not suppressed"
    assert len(seen) == 2
    system2 = seen[1][0]["content"]
    assert "SUPERSEDED" in system2, "model was not told its fragment answer was already spoken"
    assert REPLY1 in system2, "model must see what it already said, to avoid repeating it"
    assert "SUPERSEDED" not in seen[0][0]["content"], "a first answer must carry no supersede note"


def test_clm_seam_unrelated_next_turn_carries_no_note(monkeypatch, tmp_path):
    seen = []
    mod = _load(monkeypatch, tmp_path, [REPLY1, "sure"], seen)
    c = mod.app.test_client()
    q = "/v1/chat/completions?custom_session_id=CIDEXT2"
    hdrs = {"Authorization": f"Bearer {mod.BEARER_TOKEN}"}
    env = {"REMOTE_ADDR": "127.0.0.1"}
    c.post(q, json={"messages": [GREET, {"role": "user", "content": FRAG}]}, headers=hdrs, environ_base=env)
    c.post(q, json={"messages": [GREET, {"role": "user", "content": FRAG},
                                 {"role": "assistant", "content": REPLY1},
                                 {"role": "user", "content": "now open the approvals page for me please"}]},
           headers=hdrs, environ_base=env)
    assert "SUPERSEDED" not in seen[1][0]["content"]
