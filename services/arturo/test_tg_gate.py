# Q0 commission (gm msg_6f4f2050 + addendum msg_599e3219 items 1+3, the operator-ordered):
# mechanical send_telegram appropriateness gate + 'On it.'/'Got it.' pool additions
# + [Arturo Voice]: pane-inject attribution.
#
# SAFETY: every wiring test stubs requests.post with a loud recorder BEFORE calling
# execute_tool — in a broken/RED state the real branch would otherwise text the operator.
import importlib.util
import pathlib

import pytest

from services.arturo import voice_guards as vg


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The two verbatim field misroutes from the operator's 2026-08-18 calls (logs/arturo-proxy.log
# 05:02:55 + 05:21:16) — the acceptance spec names these as DENY fixtures.
FIELD_MSG_1 = "Are you ready for my answers?"
FIELD_MSG_2 = "I said \u201chow\u2019s it going?\u201d And you replied with \u201chey\u201d"


# ---------------- pure gate: tg_send_appropriate ----------------

def test_deny_internal_chatter_no_turns():
    for msg in (FIELD_MSG_1, FIELD_MSG_2):
        ok, reason = vg.tg_send_appropriate(msg, [])
        assert ok is False
        # the deny result must TEACH: not-sent + speak instead, and never claim it was sent
        assert "Not sent" in reason
        assert "SPEAK" in reason
        assert "sent to the operator" not in reason


def test_deny_internal_chatter_with_unrelated_turns():
    turns = ["how's it going", "what are the agents up to", "okay continue"]
    ok, _ = vg.tg_send_appropriate(FIELD_MSG_1, turns)
    assert ok is False


def test_allow_explicit_text_request():
    ok, reason = vg.tg_send_appropriate(
        "Here are the three answers you asked for", ["can you text me the answer"])
    assert ok is True and reason == "explicit_request"


def test_allow_word_telegram_errs_toward_allow():
    ok, _ = vg.tg_send_appropriate(
        "summary of the build", ["put that on telegram for me"])
    assert ok is True


def test_allow_message_me_variant():
    ok, _ = vg.tg_send_appropriate("done", ["message me when it finishes"])
    assert ok is True


def test_send_to_other_agent_is_not_texting_intent():
    # "send it to the gm" is inject_message intent, not a the operator-text request
    ok, _ = vg.tg_send_appropriate("status update", ["send it to the gm"])
    assert ok is False


def test_allow_deliverable_link_without_request():
    ok, reason = vg.tg_send_appropriate(
        "The one-pager is live: https://acme.io/onepager", [])
    assert ok is True and reason == "deliverable_link"


def test_lookback_window_is_five_turns():
    old_intent_then_six_unrelated = ["text me the result"] + [f"turn {i}" for i in range(6)]
    ok, _ = vg.tg_send_appropriate("result: 42", old_intent_then_six_unrelated)
    assert ok is False
    within = [f"turn {i}" for i in range(3)] + ["text me the result", "ok"]
    ok, _ = vg.tg_send_appropriate("result: 42", within)
    assert ok is True


def test_gate_tolerates_none_and_nonstr_turns():
    ok, _ = vg.tg_send_appropriate("hello", None)
    assert ok is False
    ok, _ = vg.tg_send_appropriate("hello", [None, 42, {"role": "user"}])
    assert ok is False


# ---------------- addendum item 1: 'On it.' / 'Got it.' join the pool ----------------

def test_on_it_got_it_stripped_from_content_start():
    out, n = vg.strip_leading_fillers("On it. Got it. The deploy finished clean.")
    assert out == "The deploy finished clean."
    assert n == 2


def test_trailing_variant_forms_strip():
    out, n = vg.strip_leading_fillers("On it! Here's the summary.")
    assert out == "Here's the summary." and n == 1
    out, n = vg.strip_leading_fillers("Got it! Running now.")
    assert out == "Running now." and n == 1


def test_all_filler_on_it_turn_keeps_one_ack():
    out, n = vg.strip_leading_fillers("On it.")
    assert out == "On it." and n == 0


def test_scrub_history_drops_emptied_on_it_turn():
    msgs = [{"role": "assistant", "content": "On it."},
            {"role": "user", "content": "thanks"}]
    out = vg.scrub_history(msgs)
    assert [m["role"] for m in out] == ["user"]


# ---------------- wiring: gate sits BEFORE _TG_OUTBOX / the POST ----------------

class _PostRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        class R:
            status_code = 200
            text = "ok"
        return R()


@pytest.fixture()
def proxy(monkeypatch):
    mod = _load_proxy()
    rec = _PostRecorder()
    import requests
    monkeypatch.setattr(requests, "post", rec)
    return mod, rec


def test_execute_tool_denies_internal_send_and_never_posts(proxy):
    mod, rec = proxy
    result = mod.execute_tool("send_telegram", {"message": FIELD_MSG_2}, user_turns=[])
    assert "Not sent" in result and "SPEAK" in result
    assert rec.calls == []


def test_execute_tool_default_user_turns_none_denies(proxy):
    # nested/legacy callers that pass no turns must fail CLOSED (policy: internal
    # messages never reach the operator's TG) — except a deliverable link
    mod, rec = proxy
    result = mod.execute_tool("send_telegram", {"message": FIELD_MSG_1})
    assert "Not sent" in result
    assert rec.calls == []


def test_execute_tool_allows_explicit_request_and_posts(proxy):
    mod, rec = proxy
    result = mod.execute_tool("send_telegram",
                              {"message": "Answers: 1) yes 2) no 3) shipped"},
                              user_turns=["text me your answers"])
    assert len(rec.calls) == 1
    assert "sent to the operator" in result


def test_execute_tool_allows_url_and_posts(proxy):
    mod, rec = proxy
    result = mod.execute_tool("send_telegram",
                              {"message": "Live here: https://example.com/x"},
                              user_turns=[])
    assert len(rec.calls) == 1
    assert "sent to the operator" in result


# ---------------- addendum item 3: [Arturo Voice]: inject attribution ----------------

def test_inject_message_prefixes_arturo_voice(monkeypatch):
    mod = _load_proxy()
    sent_cmds = []

    def fake_run(mac_cmd, vps_cmd, **kw):
        sent_cmds.append(vps_cmd)
        # capture-pane verification path: return a CLEARED composer (the message submitted, prompt
        # empty) so the composer-state landed check reads it as landed (Bug 1 invariant).
        if "capture-pane" in vps_cmd:
            return True, "  Done (1 tool use . 3s)\n\n---\n> \n---\n  model . 10%\n", "vps"
        return True, "", "vps"

    monkeypatch.setattr(mod, "_run_on_machine", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None) if hasattr(mod, "time") else None
    result = mod.execute_tool("inject_message",
                              {"session_name": "globex-dev",
                               "message": "check the deploy logs for globex please"})
    send_keys = [c for c in sent_cmds if "send-keys" in c]
    assert send_keys, f"no send-keys command captured: {sent_cmds}"
    # the literal paste (phase 1) carries the attributed body
    assert "[Arturo Voice]: " in send_keys[0]
    assert "injected" in result


def test_inject_message_prefix_is_idempotent(monkeypatch):
    mod = _load_proxy()
    sent_cmds = []

    def fake_run(mac_cmd, vps_cmd, **kw):
        sent_cmds.append(vps_cmd)
        return True, "[Arturo Voice]: already prefixed once", "vps"

    monkeypatch.setattr(mod, "_run_on_machine", fake_run)
    mod.execute_tool("inject_message",
                     {"session_name": "globex-dev",
                      "message": "[Arturo Voice]: already prefixed once"})
    send_keys = [c for c in sent_cmds if "send-keys" in c]
    assert send_keys
    assert send_keys[0].count("[Arturo Voice]:") == 1
