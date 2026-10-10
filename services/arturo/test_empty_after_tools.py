"""RED-first (arturo-voice, real call vc_client_f18b68944d8d / server twin vc_0ee7a77dbbf2a3f3,
2026-10-07 19:42 ET): the operator asked "what page am I on"; read_screen_context ran and the model's
follow-up came back EMPTY -> "Streamed 0 chars after 1 tool round". Hume's chat history then
shows it never committed another reply all call (dead air, the operator hung up). (A) a tool round
must never end in silence. (B) Hume prosody {...} blocks appear MID-text once Hume merges
speech into one turn; they reached the model and it filed the operator's tone as a remember_note."""
import importlib.util
import json
import pathlib

from services.arturo.test_answered_final_extension import _deltas

GREET = {"role": "assistant", "content": "Hey the operator, Arturo here, what do you need?"}


def _load(monkeypatch, tmp_path, followups):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_eat", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    monkeypatch.setattr(mod, "execute_tool",
                        lambda name, args, user_turns=None: "the operator is on /approvals (40 pending).")
    seen = []
    outs = iter([None] + list(followups))   # first call = the tool call

    class _Fn:
        name = "read_screen_context"
        arguments = "{}"

    class _TC:
        id = "tc1"
        type = "function"
        function = _Fn()

    class _Completions:
        def create(self, **kw):
            seen.append(kw)
            first = len(seen) == 1
            text = next(outs, "")

            class _Msg:
                tool_calls = [_TC()] if first else None
                content = text

            class _Choice:
                finish_reason = "tool_calls" if first else "stop"
                message = _Msg()

            class _Resp:
                choices = [_Choice()]
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _Completions()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    return mod, seen


def _ask(mod, text="Hey, what page am I on right now?"):
    c = mod.app.test_client()
    r = c.post("/v1/chat/completions?custom_session_id=CIDEAT",
               json={"messages": [GREET, {"role": "user", "content": text}]},
               headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return "".join(_deltas(r.get_data(as_text=True)))


def test_empty_follow_up_after_tools_is_retried_not_silent(monkeypatch, tmp_path):
    mod, seen = _load(monkeypatch, tmp_path, ["", "You're on the approvals page."])
    spoken = _ask(mod)
    assert spoken.strip() == "You're on the approvals page.", spoken
    assert seen[-1].get("tools") in (None, []), "the retry must not offer tools again"


def test_empty_twice_falls_back_to_a_spoken_line(monkeypatch, tmp_path):
    mod, seen = _load(monkeypatch, tmp_path, ["", ""])
    spoken = _ask(mod)
    assert spoken.strip(), "a tool round must never stream zero words to Hume"


def test_mid_text_prosody_blocks_are_stripped(monkeypatch, tmp_path):
    mod, seen = _load(monkeypatch, tmp_path, ["You're on approvals."])
    _ask(mod, "Hey, what page am I on right now? {slightly angry, very slightly contemptuous, "
              "very slightly determined} Still working on the latency. {calm}")
    sent_user = [m["content"] for m in seen[0]["messages"] if m.get("role") == "user"][-1]
    assert "{" not in sent_user and "angry" not in sent_user, sent_user
    assert sent_user == "Hey, what page am I on right now? Still working on the latency.", sent_user
