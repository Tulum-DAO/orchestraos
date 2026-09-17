"""Hermetic tests for plugins.telegram: a fake Bot API, a fake gm inbox, a fake ONE core.
No network, no token, no live data dir (ORCHESTRA_DIR -> tmp)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from plugins import telegram as tg            # noqa: E402
from plugins.telegram import router as R      # noqa: E402
from plugins.telegram import tg_send          # noqa: E402
import plugins                                 # noqa: E402


class FakeApi:
    def __init__(self, updates=None):
        self.calls = []
        self.updates = list(updates or [])

    def __call__(self, method, payload=None, timeout=None):
        self.calls.append((method, payload or {}))
        if method == "getUpdates":
            u, self.updates = self.updates, []
            return {"ok": True, "result": u}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/x.jpg"}}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 77, "chat": {"id": payload["chat_id"]}}}
        return {"ok": True, "result": {}}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(tg_send, "_api_override", None)
    return tmp_path / "data"


def _router(data_dir, api, raw=None, **kw):
    delivered = []
    answered = []
    r = R.Router(raw or {"plugins": {"telegram": {"enabled": True}}}, api=api,
                 deliver=lambda text, meta: delivered.append((text, meta)) or "msg_1",
                 answer=lambda cid, verb: answered.append((cid, verb)) or (True, "ok"),
                 pending=kw.pop("pending", lambda: []),
                 state=R.State(data_dir / "state" / "telegram"), fetch=lambda p: b"jpegbytes")
    return r, delivered, answered


def _msg(chat, text, **extra):
    m = {"message_id": 5, "chat": {"id": chat}, "from": {"username": "op"}, "text": text}
    m.update(extra)
    return m


# ---- registry / config ----

def test_registry_returns_none_when_no_plugin_enabled():
    assert plugins.configured_channel({}) is None
    assert plugins.configured_channel({"plugins": {"telegram": {"enabled": False}}}) is None


def test_registry_returns_telegram_when_enabled():
    mod = plugins.configured_channel({"plugins": {"telegram": {"enabled": True}}})
    assert mod is tg


def test_token_comes_from_env_or_data_dir_env_file_never_toml(data_dir, monkeypatch):
    assert tg.bot_token() == ""
    (data_dir).mkdir(parents=True, exist_ok=True)
    (data_dir / ".env.telegram").write_text('TELEGRAM_BOT_TOKEN="123:abc"\n')
    assert tg.bot_token() == "123:abc"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:env")
    assert tg.bot_token() == "999:env"


def test_doctor_status_levels(data_dir, monkeypatch):
    assert tg.status({})["level"] == "INFO"
    on = {"plugins": {"telegram": {"enabled": True}}}
    assert tg.status(on)["level"] == "MISSING"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    assert tg.status(on)["level"] == "OK"


# ---- inbound ----

def test_text_message_lands_in_gm_inbox_with_chat_metadata(data_dir):
    api = FakeApi([{"update_id": 10, "message": _msg(4242, "deploy the thing\nplease")}])
    r, delivered, _ = _router(data_dir, api)
    assert r.poll_once() == 1
    assert len(delivered) == 1
    text, meta = delivered[0]
    assert text.startswith("deploy the thing")
    assert meta["chat_id"] == 4242 and meta["message_id"] == 5 and meta["username"] == "op"
    assert r.state.offset == 11
    # the first chat that talks to the bot becomes the operator (remembered for tg_send)
    assert tg.remembered_chat_id() == 4242


def test_allowlist_rejects_other_chats_and_first_chat_rule_is_off(data_dir):
    raw = {"plugins": {"telegram": {"enabled": True, "allowed_chat_ids": [1]}}}
    api = FakeApi([{"update_id": 1, "message": _msg(2, "hi")}, {"update_id": 2, "message": _msg(1, "yo")}])
    r, delivered, _ = _router(data_dir, api, raw=raw)
    r.poll_once()
    assert [m["chat_id"] for _, m in delivered] == [1]
    assert tg.remembered_chat_id() is None


def test_second_chat_is_ignored_once_an_operator_is_remembered(data_dir):
    api = FakeApi([{"update_id": 1, "message": _msg(7, "first")}, {"update_id": 2, "message": _msg(8, "intruder")}])
    r, delivered, _ = _router(data_dir, api)
    r.poll_once()
    assert [m["chat_id"] for _, m in delivered] == [7]


def test_start_command_replies_and_is_not_forwarded(data_dir):
    api = FakeApi([{"update_id": 1, "message": _msg(7, "/start")}])
    r, delivered, _ = _router(data_dir, api)
    r.poll_once()
    assert delivered == []
    assert any(m == "sendMessage" and "operator channel" in p["text"] for m, p in api.calls)


def test_photo_is_downloaded_under_the_data_dir_and_listed_in_the_body(data_dir):
    api = FakeApi([{"update_id": 1, "message": _msg(7, "", photo=[{"file_id": "small"}, {"file_id": "big"}], caption="look")}])
    r, delivered, _ = _router(data_dir, api)
    r.poll_once()
    text, meta = delivered[0]
    assert text.startswith("look") and "Attachments:" in text
    p = Path(meta["attachments"][0]["path"])
    assert p.is_file() and p.read_bytes() == b"jpegbytes"
    assert str(data_dir / "state" / "uploads" / "telegram") in str(p)
    assert ("getFile", {"file_id": "big"}) in api.calls


def test_a_bad_update_is_skipped_and_offset_still_advances(data_dir):
    api = FakeApi([{"update_id": 1, "message": {"chat": {}}}, {"update_id": 2, "message": _msg(7, "ok")}])
    r, delivered, _ = _router(data_dir, api)
    r.poll_once()
    assert len(delivered) == 1 and r.state.offset == 3


# ---- buttons ----

def test_callback_lands_through_the_one_core_and_edits_the_message(data_dir):
    tg.remember_chat_id(7)
    cq = {"id": "cq1", "data": "a|apr_123|approve", "message": {"message_id": 77, "chat": {"id": 7}, "text": "APPROVAL — x"}}
    api = FakeApi([{"update_id": 1, "callback_query": cq}])
    r, _, answered = _router(data_dir, api)
    r.poll_once()
    assert answered == [("apr_123", "approve")]
    methods = [m for m, _ in api.calls]
    assert "answerCallbackQuery" in methods and "editMessageText" in methods
    edit = next(p for m, p in api.calls if m == "editMessageText")
    assert "answered: approve" in edit["text"]


def test_menu_option_callback_maps_to_option_n(data_dir):
    tg.remember_chat_id(7)
    cq = {"id": "cq1", "data": "a|apr_9|o2", "message": {"message_id": 1, "chat": {"id": 7}, "text": "DECISION"}}
    api = FakeApi([{"update_id": 1, "callback_query": cq}])
    r, _, answered = _router(data_dir, api)
    r.poll_once()
    assert answered == [("apr_9", "o2")]
    ack = next(p for m, p in api.calls if m == "answerCallbackQuery")
    assert "option 2" in ack["text"]


def test_answer_card_builds_the_approval_py_argv(monkeypatch, data_dir):
    seen = {}

    class P:  # fake CompletedProcess
        returncode = 0; stdout = "ok"; stderr = ""

    monkeypatch.setattr(R.subprocess, "run", lambda argv, **kw: (seen.__setitem__("argv", argv), P())[1])
    R.answer_card("apr_1", "o3")
    argv = seen["argv"]
    assert argv[1].endswith("scripts/approval.py") and "answer" in argv
    assert argv[argv.index("--answer") + 1] == "option" and argv[argv.index("--option-n") + 1] == "3"
    assert argv[argv.index("--surface") + 1] == "phone" and argv[argv.index("--answered-by") + 1] == "operator"


# ---- cards -> phone ----

def test_pending_cards_are_pushed_once_with_buttons_and_demo_rows_never_leave(data_dir):
    tg.remember_chat_id(7)
    api = FakeApi()
    cards = [{"id": "apr_a", "from_agent": "builder", "question": "ship it?", "kind": "approval"},
             {"id": "apr_m", "from_agent": "gm", "question": "which?", "kind": "menu",
              "menu": {"options": [{"n": 1, "label": "red"}, {"n": 2, "label": "blue"}]}},
             {"id": "apr_d", "from_agent": "demo-planner", "question": "demo", "kind": "approval", "feature": "demo"}]
    r, _, _ = _router(data_dir, api, pending=lambda: cards)
    tg_send._api_override = api
    assert r.push_pending_cards() == 2
    sent = [p for m, p in api.calls if m == "sendMessage"]
    assert [p["chat_id"] for p in sent] == [7, 7]
    kb_a = sent[0]["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for b in kb_a[0]] == ["a|apr_a|approve", "a|apr_a|deny"]
    kb_m = sent[1]["reply_markup"]["inline_keyboard"]
    assert [row[0]["callback_data"] for row in kb_m] == ["a|apr_m|o1", "a|apr_m|o2"]
    assert "apr_d" not in json.dumps(sent)
    # idempotent: second sweep sends nothing
    assert r.push_pending_cards() == 0
    assert set(r.state.notified()) == {"apr_a", "apr_m", "apr_d"}


# ---- outbound CLI ----

def test_tg_send_cli_uses_the_remembered_chat_and_checks_ok_true(data_dir, capsys):
    tg.remember_chat_id(7)
    calls = []
    tg_send._api_override = lambda m, p: calls.append((m, p)) or {"ok": True}
    assert tg_send.main(["hello there"]) == 0
    assert calls == [("sendMessage", {"chat_id": 7, "text": "hello there", "disable_web_page_preview": True})]
    tg_send._api_override = lambda m, p: {"ok": False, "description": "chat not found"}
    assert tg_send.main(["x"]) == 1
    assert "chat not found" in capsys.readouterr().err


def test_tg_send_without_a_chat_fails_with_guidance(data_dir, capsys):
    tg_send._api_override = lambda m, p: {"ok": True}
    assert tg_send.main(["hello"]) == 1
    assert "message the bot once" in capsys.readouterr().err


def test_tg_send_splits_long_text_into_numbered_chunks(data_dir):
    tg.remember_chat_id(7)
    calls = []
    tg_send._api_override = lambda m, p: calls.append(p) or {"ok": True}
    assert tg_send.send_text("x" * 9000)
    assert len(calls) == 3 and calls[0]["text"].startswith("[1/3]")
