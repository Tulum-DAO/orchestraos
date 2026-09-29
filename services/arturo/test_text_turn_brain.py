"""RED-first: a text turn can name its brain and carry context as a field
(DEC-1790669162399904 spec v4 §1.1-1.6 + binding impl notes).

Drives the REAL text_turn -> /v1/chat/completions seam with the brains stubbed (the
test_onboarding_seam.py pattern), so these observe which brain actually answered.
"""
import importlib.util
import json
import pathlib
import types

import pytest

HERE = pathlib.Path(__file__).resolve().parent
CASES = json.loads((HERE / "fixtures" / "context_line_cases.json").read_text())


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_bt", HERE / "arturo-proxy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probes(*authed):
    return [{"id": i, "cli": c, "installed": True, "authed": i in authed}
            for i, c in (("claude", "claude"), ("gemini", "agy"), ("codex", "codex"))]


CATALOG = {"claude": ["claude-sonnet-5"], "gemini": ["gemini-3.7-flash-high"], "codex": ["gpt-5.6-terra"]}


@pytest.fixture()
def P(tmp_path):
    mod = _load_proxy()
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.build_context = lambda **k: "BASECTX"
    calls = {"global": 0, "runner": []}

    def global_complete(model=None, messages=None, **kw):
        calls["global"] += 1
        calls["global_messages"] = messages
        return mod._brain.make_response("from the default brain", None, "stop")

    mod.brain = types.SimpleNamespace(kind="test", model="default", complete=global_complete,
                                      describe=lambda: {"kind": "test", "model": "default"})
    mod._MODEL_CATALOG = CATALOG

    def pool(runner, authed=("claude", "codex")):
        mod._BRAIN_POOL = mod._brain.BrainPool(probes=lambda: _probes(*authed), runner=runner)

    def ok_runner(spec, timeout):
        calls["runner"].append(spec.argv)
        calls.setdefault("stdin", []).append(spec.stdin or "")
        return "from the chosen brain"

    pool(ok_runner)
    mod._test = types.SimpleNamespace(calls=calls, pool=pool, ok_runner=ok_runner)
    return mod


def _rows(P, cid):
    t = P._THREADS.get_thread(cid)
    return 0 if t is None else len(t["turns"])


# ---- default path is untouched ---------------------------------------------------------------

def test_no_brain_field_answers_on_the_default_brain_exactly_as_today(P):
    code, body = P.text_turn("hello", "c0")
    assert code == 200 and body["reply_text"] == "from the default brain"
    assert body["brain"] == {"kind": "test", "model": "default"}
    assert P._test.calls["global"] >= 1 and P._test.calls["runner"] == []
    assert P._THREADS.get_thread("c0")["last_brain"] is None


# ---- explicit brain ---------------------------------------------------------------------------

def test_explicit_brain_answers_and_the_default_is_never_called(P):
    code, body = P.text_turn("hello", "c1", brain={"provider": "codex", "model": "gpt-5.6-terra"})
    assert code == 200 and body["reply_text"] == "from the chosen brain"
    assert P._test.calls["global"] == 0
    assert any("--model=gpt-5.6-terra" in argv for argv in P._test.calls["runner"])
    assert body["brain"]["runtime"] == "codex" and body["brain"]["model"] == "gpt-5.6-terra"
    assert P._THREADS.get_thread("c1")["last_brain"] == {"provider": "codex", "model": "gpt-5.6-terra"}


def test_the_turn_brain_does_not_leak_into_the_next_turn(P):
    P.text_turn("one", "c2", brain={"provider": "claude", "model": ""})
    code, body = P.text_turn("two", "c2")
    assert body["reply_text"] == "from the default brain"
    assert P._THREADS.get_thread("c2")["last_brain"] is None


def test_switching_provider_mid_thread_resends_the_history(P):
    P.text_turn("my name is Ada", "c3", brain={"provider": "claude", "model": "claude-sonnet-5"})
    P._test.calls["runner"].clear()
    P._test.calls["stdin"].clear()
    code, _ = P.text_turn("what is my name", "c3", brain={"provider": "codex", "model": ""})
    assert code == 200
    codex_calls = [i for i, argv in enumerate(P._test.calls["runner"]) if argv[:2] == ["codex", "exec"]]
    assert codex_calls, "codex was never called"
    # codex carries system + transcript on stdin: the earlier turn (answered by claude) must be in it
    assert "my name is Ada" in P._test.calls["stdin"][codex_calls[0]]


# ---- refusals: validated at the proxy, nothing recorded ----------------------------------------

@pytest.mark.parametrize("brain,field", [
    ({"provider": "claude", "model": "claude-sonnet-4"}, "brain.model"),
    ({"provider": "claude", "model": "--dangerously-skip-permissions"}, "brain.model"),
    ({"provider": "grok", "model": ""}, "brain.provider"),
    ({"provider": "", "model": ""}, "brain.provider"),
    ("claude", "brain"),
])
def test_a_bad_brain_is_a_400_naming_the_field_and_nothing_runs(P, brain, field):
    code, body = P.text_turn("hello", "c4", brain=brain)
    assert code == 400 and body["error"] in ("unknown_model", "bad_brain") and body["field"] == field
    assert P._test.calls["global"] == 0 and P._test.calls["runner"] == []
    assert _rows(P, "c4") == 0


def test_an_unauthed_provider_is_a_409_never_a_fallback(P):
    code, body = P.text_turn("hello", "c5", brain={"provider": "gemini", "model": ""})
    assert code == 409 and body["error"] == "provider_unavailable" and body["provider"] == "gemini"
    assert body["reason"]
    assert P._test.calls["global"] == 0
    assert _rows(P, "c5") == 0


# ---- failures of a chosen brain are errors, never saved replies --------------------------------

def test_cli_failure_on_a_chosen_brain_is_a_502_and_nothing_is_recorded(P):
    def boom(spec, timeout):
        raise RuntimeError("cli exploded")
    P._test.pool(boom)
    code, body = P.text_turn("hello", "c6", brain={"provider": "claude", "model": ""})
    assert code == 502 and body["error"] == "brain_failed" and body["provider"] == "claude"
    assert "cli exploded" not in json.dumps(body)
    assert _rows(P, "c6") == 0 and not P._TEXT_HISTORY.get("c6")


def test_an_empty_reply_from_a_chosen_brain_is_a_502_not_a_blank_answer(P):
    P._test.pool(lambda spec, timeout: "")          # empty first try AND empty retry
    code, body = P.text_turn("hello", "c7", brain={"provider": "claude", "model": ""})
    assert code == 502 and body["error"] == "empty_response"
    assert _rows(P, "c7") == 0 and not P._TEXT_HISTORY.get("c7")


def test_an_exception_inside_the_chat_path_on_a_chosen_brain_is_a_502(P):
    # ApiBrain raises instead of degrading; generate()'s catch-all used to turn that into
    # "I hit a snag ..." prose that got saved. impl note 1.
    class Raises:
        kind, model, strict = "api", "gemini-2.5-flash", True
        def complete(self, **kw):
            raise RuntimeError("upstream 500")
        def describe(self):
            return {"kind": "api", "model": self.model}
    P._BRAIN_POOL = types.SimpleNamespace(get=lambda provider, model: Raises())
    P._MODEL_CATALOG = CATALOG
    code, body = P.text_turn("hello", "c8", brain={"provider": "api", "model": ""})
    assert code == 502 and body["error"] == "brain_failed"
    assert "snag" not in json.dumps(body)
    assert _rows(P, "c8") == 0


def test_a_failure_after_tools_ran_says_which_tools_ran(P, monkeypatch):
    # impl note 3: a retry must not be presented as side-effect free.
    def reply(messages, conversation_id):
        P._brain.TURN_FAILURE.set({"code": "brain_failed", "provider": "claude", "model": ""})
        return "sorry", ["send_telegram"], []
    monkeypatch.setattr(P, "_brain_reply", reply)
    code, body = P.text_turn("hello", "c9", brain={"provider": "claude", "model": ""})
    assert code == 502 and body["tools_called"] == ["send_telegram"]
    assert _rows(P, "c9") == 0


def test_the_default_brain_keeps_todays_behaviour_on_failure(P):
    # No strict flag on the global: an empty default reply is still answered and recorded as today.
    P.brain.complete = lambda **kw: P._brain.make_response("", None, "stop")
    code, body = P.text_turn("hello", "c10")
    assert code == 200
    assert _rows(P, "c10") == 2


# ---- context as a field -----------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES)
def test_context_field_renders_the_line_the_web_client_prepends_today(P, case):
    P.text_turn("what is this", "cx", context=case["ctx"])
    user_msg = [m for m in P._test.calls["global_messages"] if m["role"] == "user"][-1]["content"]
    assert user_msg == f"{case['line']}\nwhat is this"
    # recorded exactly as today (the client used to prepend it), so history stays byte-identical
    assert P._THREADS.get_thread("cx")["turns"][0]["content"] == f"{case['line']}\nwhat is this"


def test_context_renders_after_the_onboarding_marker_is_split(P):
    P.text_turn("[Onboarding: step=name]\nI'm Ada", "co", context={"route": "/"})
    user_msg = [m for m in P._test.calls["global_messages"] if m["role"] == "user"][-1]["content"]
    assert user_msg == "[Context: route=/]\nI'm Ada"


@pytest.mark.parametrize("ctx", [{"route": "x" * 301}, {"hint": "no route"}, "not-a-dict",
                                 {"route": "/", "entityKind": ["list"]}])
def test_a_malformed_context_is_a_400(P, ctx):
    code, body = P.text_turn("hi", "cm", context=ctx)
    assert code == 400 and body["error"] == "bad_context"


# ---- the /text route passes both fields through ------------------------------------------------

def test_text_route_forwards_brain_and_context(P):
    with P.app.test_client() as c:
        r = c.post("/text", json={"text": "hi", "conversation_id": "cr",
                                  "brain": {"provider": "codex", "model": ""},
                                  "context": {"route": "/overview"}})
    assert r.status_code == 200 and r.get_json()["reply_text"] == "from the chosen brain"
    assert P._THREADS.get_thread("cr")["turns"][0]["content"] == "[Context: route=/overview]\nhi"
