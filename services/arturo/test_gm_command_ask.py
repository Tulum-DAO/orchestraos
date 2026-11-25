"""gm_command asks gm and gives the call an answer or a promise, never silence (Shaw 2026-11-25; gm D2 msg_09a35684).

On 2026-11-25 a question went to codex-gm by pane scrape and the call got Codex's preamble as "the answer"; in this
tree the scrape script does not ship, so the tool said "relay not installed". Now: ONE msg_store message to
VOICE_BRAIN_SESSION (gm) per question, the call/thread id in its subject so gm's reply lands in that thread, a wait
bounded at 25 s for gm's reply, and on timeout "gm has it; the answer comes to your chat". The same question again in
the same call is not re-sent (no retry storm).
"""
import sys
import types

import pytest

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture (ARTURO_STATE in tmp)


class _Store:
    def __init__(self, reply_after=None, reply="Yes, the dream fix merged at 06:09Z."):
        self.sent, self.polls = [], 0
        self.reply_after, self.reply = reply_after, reply

    def send(self, **kw):
        self.sent.append(kw)
        return f"msg_q{len(self.sent)}"

    def get(self, mid):
        return {"id": mid, "conversation_id": f"conv_{mid}"}

    def thread(self, conv, limit=None):
        self.polls += 1
        rows = [{"id": "msg_q1", "from_agent": "arturo", "body": "q"}]
        if self.reply_after is not None and self.polls >= self.reply_after:
            rows.append({"id": "msg_r1", "from_agent": "gm", "type": "reply", "body": self.reply})
        return rows


@pytest.fixture()
def ask(P, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(P, "_gm_ask_now", lambda: clock["t"])
    monkeypatch.setattr(P, "_gm_ask_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    P._GM_ASKED.clear()

    def run(store, prompt="is the dream fix in?", turn=None):
        monkeypatch.setitem(sys.modules, "msg_store", types.SimpleNamespace(MessageStore=lambda: store))
        t = turn or {"principal": "fleet", "voice": True, "conversation_id": "native-abc", "thread_id": "web_ypc"}
        return P._as_turn(t, P.execute_tool, "gm_command", {"prompt": prompt, "timeout": 120}), clock
    return run


def test_the_question_goes_to_gm_once_with_the_thread_in_the_subject(P, ask):
    store = _Store(reply_after=3)
    out, _ = ask(store)
    assert len(store.sent) == 1
    m = store.sent[0]
    assert m["to_agent"] == P.VOICE_BRAIN_SESSION == "gm"
    assert m["from_agent"] == "arturo" and m["priority"] == "high"
    assert "web_ypc" in m["subject"] and "native-abc" in m["subject"]
    assert "is the dream fix in?" in m["body"]
    assert "Yes, the dream fix merged at 06:09Z." in out


def test_no_reply_within_the_cap_is_a_promise_not_silence(P, ask):
    store = _Store(reply_after=None)
    out, clock = ask(store)
    assert clock["t"] - 1000.0 <= P.GM_ASK_WAIT_S + 2          # bounded, whatever timeout the model asked for
    assert "gm has it" in out and "chat" in out
    assert not out.startswith("GM error") and "not installed" not in out


def test_the_same_question_in_the_same_call_is_not_sent_again(P, ask):
    store = _Store(reply_after=None)
    ask(store)
    out, _ = ask(store, prompt="Is the dream fix in?")
    assert len(store.sent) == 1
    assert "already" in out and "gm has it" in out


def test_a_store_failure_says_so_plainly(P, ask):
    class _Broken(_Store):
        def send(self, **kw):
            raise OSError("db locked")
    out, _ = ask(_Broken())
    assert out.startswith("Could not reach gm") and "db locked" not in out


def test_the_default_brain_is_gm(P):
    assert P.VOICE_BRAIN_SESSION == "gm"
