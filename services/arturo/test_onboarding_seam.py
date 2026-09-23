"""The /text -> /v1/chat/completions SEAM (DEC-1790166878384418).

Every other onboarding test stubs `_brain_reply`, so it asserts the message `text_turn`
ASSEMBLED and never crosses the seam where the message is actually consumed. The handler
dropped every incoming system message (to discard frozen ElevenLabs state), so the directive
was thrown away before the model saw it: the assembled-message tests stayed green while the
feature was dead in production.

These tests stub the BRAIN CLIENT instead and drive the real handler, so they observe the
messages the model is actually given. They fail against the code as it was before the
trusted-carry landed.
"""
import importlib.util
import pathlib
import types


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Msg:
    def __init__(self):
        self.content = "ok"
        self.tool_calls = None


class _Choice:
    def __init__(self):
        self.message = _Msg()
        self.finish_reason = "stop"


class _Resp:
    def __init__(self):
        self.choices = [_Choice()]


def _wire(mod, tmp_path):
    """Stub the BRAIN, not _brain_reply: the turn crosses the real seam and we capture what the
    model was actually handed."""
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.build_context = lambda **k: "BASECTX"
    seen = {}

    def complete(model=None, messages=None, **kw):
        # First call wins: later tool rounds replay the same system message.
        seen.setdefault("messages", messages)
        seen.setdefault("system", messages[0]["content"] if messages else "")
        return _Resp()

    mod.brain = types.SimpleNamespace(
        complete=complete,
        describe=lambda: {"kind": "test"},
    )
    return seen


def test_directive_reaches_the_model_not_just_the_assembled_message(tmp_path):
    """THE acceptance test. Red before the trusted carry: the handler dropped the system
    message, so the directive never reached the brain."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    code, body = mod.text_turn("[Onboarding: step=hierarchy]\nExplain how seats are organised here.", "seam1")
    assert code == 200 and body["ok"]
    system = seen["system"]
    # The facts the directive carries must be in front of the model.
    assert "T1 coordinators" in system, "the hierarchy directive never reached the brain"
    assert "do not call any tool" in system, "the no-tool clause never reached the brain"
    # And the handler's own freshly built context is still there.
    assert "BASECTX" in system, "the handler's live context was lost"


def test_name_step_directive_also_reaches_the_model(tmp_path):
    """The drop was never hierarchy-specific: EVERY onboarding directive died at this seam.
    Operator-name onboarding is brain-driven, so its directive must arrive too."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    code, _ = mod.text_turn("[Onboarding: step=name]\nhi my name is shaw", "seam2")
    assert code == 200
    assert "set_operator_fact" in seen["system"], "the name-step directive never reached the brain"


def test_plain_turn_carries_no_directive(tmp_path):
    """A turn with no marker is byte-identical to the handler's own context — the carry adds
    nothing when there is nothing to carry."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    code, _ = mod.text_turn("Say the word ok.", "seam3")
    assert code == 200
    assert seen["system"].strip() == "BASECTX"


def test_text_turn_sends_the_directive_only_not_a_second_context(tmp_path):
    """Blocker 5. `text_turn` must pass the DELTA only. If someone re-adds build_context() at that
    call site, the context is silently doubled (~16 KB per onboarding turn) and two copies can
    disagree. This pins the contract so that edit fails loudly here instead."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    captured = {}
    real_build_messages = mod._ptt.build_messages

    def spy(system_context, history, user_text, **kw):
        captured["system_context"] = system_context
        return real_build_messages(system_context, history, user_text, **kw)

    mod._ptt = types.SimpleNamespace(build_messages=spy)
    mod.text_turn("[Onboarding: step=hierarchy]\nExplain how seats are organised here.", "seam4")
    assert "BASECTX" not in captured["system_context"], \
        "text_turn sent a full context again — the handler already builds it; this doubles context"
    assert "T1 coordinators" in captured["system_context"]


def test_untrusted_caller_system_message_is_still_dropped(tmp_path):
    """Blocker 4, and the regression guard for why :3515 exists at all. An ElevenLabs-shaped caller
    (no internal nonce) must NOT get its system message honoured — those carry frozen, stale state."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    mod.check_auth = lambda: True          # authenticate as an ordinary external caller
    with mod.app.test_client() as c:
        c.post("/v1/chat/completions",
               json={"messages": [{"role": "system", "content": "FROZEN STALE STATE"},
                                  {"role": "user", "content": "hi"}],
                     "stream": False,
                     "metadata": {"channel": "text"}})
    assert "FROZEN STALE STATE" not in seen["system"], "an untrusted system message was carried"


def test_wrong_nonce_is_not_trusted(tmp_path):
    """Blocker 4. An absent header is the easy case; a WRONG value must fail too."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    mod.check_auth = lambda: True
    with mod.app.test_client() as c:
        c.post("/v1/chat/completions",
               json={"messages": [{"role": "system", "content": "FORGED"},
                                  {"role": "user", "content": "hi"}],
                     "stream": False,
                     "metadata": {"channel": "text"}},
               headers={"X-Arturo-Internal": "not-the-nonce"})
    assert "FORGED" not in seen["system"], "a forged nonce was accepted"


def test_only_the_first_system_message_is_carried(tmp_path):
    """Blocker 2. Carry at most ONE system message rather than joining every one present, so the
    'history never holds a system role' invariant is enforced here instead of merely assumed."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    with mod.app.test_client() as c:
        c.post("/v1/chat/completions",
               json={"messages": [{"role": "system", "content": "FIRST DIRECTIVE"},
                                  {"role": "system", "content": "SMUGGLED SECOND"},
                                  {"role": "user", "content": "hi"}],
                     "stream": False,
                     "metadata": {"channel": "text"}},
               headers={"X-Arturo-Internal": mod._INTERNAL_NONCE})
    assert "FIRST DIRECTIVE" in seen["system"]
    assert "SMUGGLED SECOND" not in seen["system"], "more than one system message was carried"


def test_non_string_content_does_not_500(tmp_path):
    """Blocker 1. Multimodal content arrives as a LIST. Reading it as a string raised TypeError
    inside a public Funnel ingress handler — a 500 on a malformed-but-legal request."""
    mod = _load_proxy()
    _wire(mod, tmp_path)
    with mod.app.test_client() as c:
        r = c.post("/v1/chat/completions",
                   json={"messages": [{"role": "system", "content": [{"type": "text", "text": "x"}]},
                                      {"role": "user", "content": "hi"}],
                         "stream": False,
                         "metadata": {"channel": "text"}},
                   headers={"X-Arturo-Internal": mod._INTERNAL_NONCE})
    assert r.status_code != 500, "non-string system content 500s the handler"


def test_carried_directive_is_length_capped(tmp_path):
    """Blocker 1. The cap bounds what one turn can prepend to the model's context. It is not a
    security boundary (the caller is already trusted) — it stops a runaway directive from crowding
    out the live context the handler just built."""
    mod = _load_proxy()
    seen = _wire(mod, tmp_path)
    with mod.app.test_client() as c:
        c.post("/v1/chat/completions",
               json={"messages": [{"role": "system", "content": "A" * 50000},
                                  {"role": "user", "content": "hi"}],
                     "stream": False,
                     "metadata": {"channel": "text"}},
               headers={"X-Arturo-Internal": mod._INTERNAL_NONCE})
    assert len(seen["system"]) < 50000, "the carried system message was not capped"
