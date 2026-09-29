"""Regression suite for router.py's getUpdates offset handling (P0: silent message loss).

The bug: poll_once() advanced self.state.offset OUTSIDE the try/except, so any
exception while handling an update still confirmed it. Telegram never redelivers an
update below the confirmed offset, so a transient failure (msg_store write blip,
attachment download error) destroyed an operator message permanently and silently.

These are the first tests router.py has ever had. Written FIRST, per the repo's TDD
convention (see test_router_prefixes.py): run them against the old code, watch them
fail, then fix.

Contract under test — commit-then-confirm:
  1. offset only advances past an update AFTER its effect durably succeeded;
  2. a failed update is NOT confirmed, so Telegram redelivers it;
  3. redelivery is idempotent on update_id, so (1)+(2) don't turn silent loss into
     silent duplication;
  4. a permanently-poisonous update eventually gets stepped over LOUDLY rather than
     blocking the operator's only command channel forever.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_router():
    """Import router.py by path — plugins/telegram is not an importable test package."""
    spec = importlib.util.spec_from_file_location(
        "tg_router_under_test", str(ROOT / "plugins" / "telegram" / "router.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scratch_db(tmp_path) -> Path:
    """Minimal messages/conversations schema, same pattern as test_msg_store_cli.py.

    msg_store does not create these tables itself, so a test that exercises the real
    store has to lay them down first.
    """
    import sqlite3 as _sq
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True, exist_ok=True)
    db = state / "tasks.db"
    conn = _sq.connect(str(db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
            type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
            subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
            source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
            metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT,
            archived_at TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.close()
    return db


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    """router module + scratch state and a scratch msg_store DB. No network."""
    db = _scratch_db(tmp_path)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MSG_DB_PATH", str(db))   # scopes no-arg MessageStore() in deliver_to_gm
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    import msg_store
    msg_store.MessageStore(db_path=str(db)).migrate()
    mod = _load_router()
    return mod


def _mk(mod, tmp_path, deliver=None, answer=None, api=None):
    state = mod.State(tmp_path / "tgstate")
    calls = {"delivered": [], "answered": [], "api": []}

    def _api(method, payload=None, timeout=None):
        calls["api"].append((method, payload))
        return {"ok": True, "result": {}}

    def _deliver(text, meta):
        calls["delivered"].append((text, meta))
        return f"msg_{len(calls['delivered'])}"

    def _answer(card_id, verb):
        calls["answered"].append((card_id, verb))
        return True, "ok"

    r = mod.Router(
        {"plugins": {"telegram": {"enabled": True, "allowed_chat_ids": [7]}}},
        api=api or _api,
        deliver=deliver or _deliver,
        answer=answer or _answer,
        pending=lambda: [],
        state=state,
    )
    return r, calls, state


def _msg_update(uid: int, text: str) -> dict:
    return {"update_id": uid,
            "message": {"message_id": 1000 + uid, "chat": {"id": 7},
                        "from": {"username": "operator"}, "text": text}}


def _batch(updates):
    """An api() stub whose getUpdates honours the offset, like Telegram does."""
    def _api(method, payload=None, timeout=None):
        if method != "getUpdates":
            return {"ok": True, "result": {}}
        off = (payload or {}).get("offset", 0)
        return {"ok": True, "result": [u for u in updates if u["update_id"] >= off]}
    return _api


# --------------------------------------------------------------------------------------
# 1. The P0 itself
# --------------------------------------------------------------------------------------

def test_exception_mid_batch_does_not_confirm_failed_update(rt, tmp_path):
    """deliver raises on update 2 of 3 -> offset must NOT advance past update 2."""
    mod = rt
    seen = []

    def deliver(text, meta):
        seen.append(meta["update_id"])
        if meta["update_id"] == 2:
            raise RuntimeError("transient msg_store write failure")
        return f"msg_{meta['update_id']}"

    r, calls, state = _mk(mod, tmp_path, deliver=deliver,
                          api=_batch([_msg_update(1, "one"), _msg_update(2, "two"),
                                      _msg_update(3, "three")]))
    r.poll_once()

    # update 1 committed, so offset may sit at 2 — but NEVER past 2.
    assert state.offset <= 2, (
        f"offset advanced to {state.offset}, confirming failed update 2 — "
        "Telegram will never redeliver it and the operator's message is lost")
    assert 1 in seen


def test_failed_update_is_redelivered_and_eventually_succeeds(rt, tmp_path):
    """The whole point of not confirming: the next poll gets the message again."""
    mod = rt
    attempts = {"n": 0}
    delivered = []

    def deliver(text, meta):
        if meta["update_id"] == 2:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient blip")
        delivered.append(meta["update_id"])
        return f"msg_{meta['update_id']}"

    r, calls, state = _mk(mod, tmp_path, deliver=deliver,
                          api=_batch([_msg_update(1, "one"), _msg_update(2, "two"),
                                      _msg_update(3, "three")]))
    r.poll_once()   # 1 ok, 2 blows up
    r.poll_once()   # redelivery: 2 succeeds this time, then 3

    assert 2 in delivered, "the failed operator message was never redelivered — silent loss"
    assert delivered.count(1) == 1, f"update 1 double-delivered: {delivered}"
    assert delivered.count(2) == 1, f"update 2 double-delivered: {delivered}"
    assert delivered.count(3) == 1, f"update 3 double-delivered: {delivered}"
    assert state.offset == 4


# --------------------------------------------------------------------------------------
# 2. Idempotency — so the fix above doesn't trade loss for duplication
# --------------------------------------------------------------------------------------

def test_redelivered_update_is_idempotent(rt, tmp_path):
    """Same update_id handed to poll_once twice -> exactly one delivery."""
    mod = rt
    r, calls, state = _mk(mod, tmp_path, api=_batch([_msg_update(1, "hello")]))
    r.poll_once()
    # Telegram redelivers the same update (e.g. our offset write was lost on restart)
    state.offset = 1
    r.poll_once()

    uids = [m["update_id"] for _, m in calls["delivered"]]
    assert uids.count(1) == 1, f"update 1 delivered {uids.count(1)}x — duplicate operator message"


def test_deterministic_msg_id_makes_delivery_idempotent_at_the_store(rt, tmp_path,
                                                                     monkeypatch):
    """The kill -9 case: the router's own state is gone, only the DB can dedupe.

    Crash between the msg_store insert and the offset write means the update IS
    redelivered with no router-side memory of it. Only a deterministic, update_id-derived
    primary key can stop a duplicate row, so assert on the real store, not a stub.
    """
    mod = rt
    import msg_store

    meta = {"chat_id": 7, "message_id": 1001, "username": "operator",
            "attachments": [], "update_id": 42}
    first = mod.deliver_to_gm("approve the deploy", dict(meta), seat="gm-test")
    second = mod.deliver_to_gm("approve the deploy", dict(meta), seat="gm-test")

    assert first == second, (
        f"same update_id produced two ids ({first} != {second}) — a crash between the "
        "insert and the offset write will duplicate the operator's message")
    rows = msg_store.MessageStore().query(to_agent="gm-test", from_agent="telegram")
    assert len(rows) == 1, f"expected exactly 1 stored row for update_id 42, got {len(rows)}"


# --------------------------------------------------------------------------------------
# 3. Success path still works (don't fix the bug by breaking the feature)
# --------------------------------------------------------------------------------------

def test_clean_batch_confirms_every_update(rt, tmp_path):
    mod = rt
    r, calls, state = _mk(mod, tmp_path,
                          api=_batch([_msg_update(1, "a"), _msg_update(2, "b")]))
    n = r.poll_once()
    assert n == 2
    assert state.offset == 3, f"clean batch must confirm through update 2, got {state.offset}"
    assert [m["update_id"] for _, m in calls["delivered"]] == [1, 2]


def test_failed_callback_is_not_confirmed(rt, tmp_path):
    """A button tap that fails to land must also be retried, not swallowed."""
    mod = rt

    def answer(card_id, verb):
        raise RuntimeError("approval.py unreachable")

    upd = {"update_id": 5,
           "callback_query": {"id": "cq1", "data": "a|apr_x|approve",
                              "message": {"message_id": 99, "chat": {"id": 7},
                                          "text": "Approve?"}}}
    r, calls, state = _mk(mod, tmp_path, answer=answer, api=_batch([upd]))
    r.poll_once()
    assert state.offset <= 5, (
        f"offset {state.offset} confirmed a callback whose answer never landed")


# --------------------------------------------------------------------------------------
# 4. Poison message must not deadlock the operator's only channel
# --------------------------------------------------------------------------------------

def test_permanently_failing_update_is_stepped_over_loudly(rt, tmp_path):
    """Never-advancing on failure would head-of-line block the channel forever.

    After a bounded number of attempts the router must step past the poison update so
    later operator messages get through — but it must say so, not silently drop it
    (silence is the bug we are fixing).
    """
    mod = rt
    logged = []
    monkey_log = getattr(mod, "log")

    def cap(msg):
        logged.append(msg)
        monkey_log(msg)

    mod.log = cap
    got = []

    def deliver(text, meta):
        if meta["update_id"] == 2:
            raise RuntimeError("permanently malformed")
        got.append(meta["update_id"])
        return f"msg_{meta['update_id']}"

    r, calls, state = _mk(mod, tmp_path, deliver=deliver,
                          api=_batch([_msg_update(2, "poison"), _msg_update(3, "after")]))
    for _ in range(mod.MAX_UPDATE_ATTEMPTS + 2):
        r.poll_once()

    assert 3 in got, (
        "update 3 never got through — a single poison update deadlocked the operator's "
        "only command channel")
    assert any("giving up" in m.lower() or "dropped" in m.lower() for m in logged), (
        "stepped over the poison update without a loud log line — that is the silent "
        "drop we are supposed to be fixing")


def test_malformed_update_is_skipped_immediately_not_retried(rt, tmp_path):
    """The other half of the contract, and the easy thing to get wrong.

    A structurally broken update (no chat.id) can never succeed. Retrying it would stall
    every real operator message behind it for the whole attempt budget. It must be
    confirmed and stepped over on the FIRST pass — which is also what the pre-existing
    suite (tests/test_router.py::test_a_bad_update_is_skipped_and_offset_still_advances)
    has always required. Transient failures keep the no-confirm treatment; only shape
    decides, never the outcome of an effect.
    """
    mod = rt
    bad = {"update_id": 1, "message": {"chat": {}}}          # no chat.id -> hopeless
    good = _msg_update(2, "a real instruction")
    r, calls, state = _mk(mod, tmp_path, api=_batch([bad, good]))
    r.poll_once()

    assert state.offset == 3, (
        f"offset {state.offset}: a malformed update must not block the good one behind it")
    assert [m["update_id"] for _, m in calls["delivered"]] == [2]
    assert state.attempts_for(1) == 0, "a permanently-bad update must not burn retries"


def test_transient_and_permanent_failures_are_treated_differently(rt, tmp_path):
    """Guard against someone later collapsing the two paths into one."""
    mod = rt

    def deliver(text, meta):
        raise RuntimeError("store down")     # transient: shape is fine

    r, calls, state = _mk(mod, tmp_path, deliver=deliver, api=_batch([_msg_update(9, "hi")]))
    r.poll_once()
    assert state.offset == 0, "a transient failure must NOT be confirmed"
    assert state.attempts_for(9) == 1, "a transient failure must burn a retry"

    r2, _, state2 = _mk(mod, tmp_path / "second",
                        api=_batch([{"update_id": 9, "message": {"chat": {}}}]))
    r2.poll_once()
    assert state2.offset == 10, "a malformed update must be confirmed immediately"
    assert state2.attempts_for(9) == 0, "a malformed update must NOT burn a retry"


def test_attempt_counter_resets_after_success(rt, tmp_path):
    """A flaky update that eventually succeeds must not carry its strikes forward."""
    mod = rt
    n = {"i": 0}

    def deliver(text, meta):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("one-off blip")
        return "msg_ok"

    r, calls, state = _mk(mod, tmp_path, deliver=deliver, api=_batch([_msg_update(1, "x")]))
    r.poll_once()
    r.poll_once()
    assert state.offset == 2
    assert state.attempts_for(1) == 0, "strike count survived a successful delivery"


# --------------------------------------------------------------------------------------
# 5. F1 — attachment fetch failure must not vanish the message
# --------------------------------------------------------------------------------------

def _photo_update(uid: int, caption: str = "") -> dict:
    m = {"message_id": 1000 + uid, "chat": {"id": 7}, "from": {"username": "operator"},
         "photo": [{"file_id": "small"}, {"file_id": "big"}]}
    if caption:
        m["caption"] = caption
    return {"update_id": uid, "message": m}


def test_photo_only_message_survives_a_failed_download(rt, tmp_path):
    """F1: a bare screenshot whose fetch fails used to vanish without a trace.

    download_file() swallows its own exception and returns None, so with no caption the
    text stays empty, `if not text: return` bails, poll_once sees SUCCESS and confirms the
    offset. Silent permanent loss — the exact class the P0 fix exists to remove, reached by
    a path that never raises so none of that machinery engages.
    """
    mod = rt

    def api(method, payload=None, timeout=None):
        if method == "getUpdates":
            off = (payload or {}).get("offset", 0)
            return {"ok": True, "result": [_photo_update(1)] if 1 >= off else []}
        if method == "getFile":
            return {"ok": False, "description": "file is temporarily unavailable"}
        return {"ok": True, "result": {}}

    r, calls, state = _mk(mod, tmp_path, api=api)
    r.poll_once()

    assert calls["delivered"], (
        "photo-only message with a failed download reached nobody — silently dropped")
    text, meta = calls["delivered"][0]
    assert "download failed" in text.lower(), (
        f"delivered text carries no signal that an attachment was lost: {text!r}")
    assert meta.get("attachment_failures"), "the failure is not recorded in metadata"


def test_caption_message_still_delivers_and_flags_the_failed_attachment(rt, tmp_path):
    """With a caption the text always survived; the lost attachment must still be visible."""
    mod = rt

    def api(method, payload=None, timeout=None):
        if method == "getUpdates":
            off = (payload or {}).get("offset", 0)
            return {"ok": True, "result": [_photo_update(2, "look at this")] if 2 >= off else []}
        if method == "getFile":
            return {"ok": False, "description": "nope"}
        return {"ok": True, "result": {}}

    r, calls, state = _mk(mod, tmp_path, api=api)
    r.poll_once()
    text, meta = calls["delivered"][0]
    assert "look at this" in text
    assert "download failed" in text.lower(), "caption survived but the lost photo is invisible"


def test_successful_attachment_is_unchanged(rt, tmp_path):
    """Don't fix the failure path by regressing the happy one."""
    mod = rt
    dest = tmp_path / "fetched"

    def api(method, payload=None, timeout=None):
        if method == "getUpdates":
            off = (payload or {}).get("offset", 0)
            return {"ok": True, "result": [_photo_update(3, "hi")] if 3 >= off else []}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/x.jpg"}}
        return {"ok": True, "result": {}}

    r, calls, state = _mk(mod, tmp_path, api=api)
    r.fetch = lambda fpath: b"jpegbytes"
    r.poll_once()
    text, meta = calls["delivered"][0]
    assert "Attachments:" in text and "download failed" not in text.lower()
    assert len(meta["attachments"]) == 1 and not meta.get("attachment_failures")


# --------------------------------------------------------------------------------------
# 6. F5 — a message we cannot render must not vanish either
# --------------------------------------------------------------------------------------

def _bare(uid: int, **payload) -> dict:
    m = {"message_id": 1000 + uid, "chat": {"id": 7}, "from": {"username": "operator"},
         "date": 1700000000}
    m.update(payload)
    return {"update_id": uid, "message": m}


@pytest.mark.parametrize("kind,payload", [
    ("sticker", {"sticker": {"file_id": "s1", "emoji": "👍"}}),
    ("animation", {"animation": {"file_id": "g1"}}),
    ("video_note", {"video_note": {"file_id": "v1"}}),
    ("location", {"location": {"latitude": 1.0, "longitude": 2.0}}),
    ("poll", {"poll": {"question": "?"}}),
])
def test_unrenderable_message_is_delivered_not_dropped(rt, tmp_path, kind, payload):
    """F5: the attachment loop enumerates 5 kinds; anything else never even attempts a
    download, so there is no failure to record — text stays empty, `if not text: return`
    fires, and the update is confirmed. Silent loss with zero signal.

    Parametrised across kinds review verified plus two it only suspected, to prove the fix
    is a catch-all and not another enumeration that will rot as the Bot API grows.
    """
    mod = rt
    r, calls, state = _mk(mod, tmp_path, api=_batch([_bare(1, **payload)]))
    r.poll_once()

    assert calls["delivered"], f"{kind}-only message reached nobody — silently dropped"
    text, meta = calls["delivered"][0]
    assert "unsupported" in text.lower(), f"no signal in delivered text: {text!r}"
    assert meta.get("unsupported"), "the drop-reason is not recorded in metadata"
    assert state.offset == 2


def test_unsupported_placeholder_names_what_arrived(rt, tmp_path):
    """'resend as text' is far more actionable if it says what could not be read.

    Derived by reflecting over the message's own keys, NOT from a hardcoded kind list —
    an unknown future type still gets named instead of silently becoming '(unsupported)'.
    """
    mod = rt
    r, calls, state = _mk(mod, tmp_path,
                          api=_batch([_bare(1, dice={"emoji": "🎲", "value": 4})]))
    r.poll_once()
    text, meta = calls["delivered"][0]
    assert "dice" in text.lower(), f"placeholder does not name the content kind: {text!r}"
    assert "dice" in (meta.get("unsupported") or [])


def test_text_and_attachment_messages_are_untouched_by_the_catch_all(rt, tmp_path):
    """The catch-all must only fire when nothing usable was found."""
    mod = rt
    r, calls, state = _mk(mod, tmp_path, api=_batch([_msg_update(1, "plain text")]))
    r.poll_once()
    text, meta = calls["delivered"][0]
    assert text == "plain text"
    assert not meta.get("unsupported"), "catch-all fired on a perfectly good text message"


def test_failed_download_does_not_also_trip_the_unsupported_catch_all(rt, tmp_path):
    """F1 and F5 must not double-report the same message."""
    mod = rt

    def api(method, payload=None, timeout=None):
        if method == "getUpdates":
            off = (payload or {}).get("offset", 0)
            return {"ok": True, "result": [_photo_update(4)] if 4 >= off else []}
        if method == "getFile":
            return {"ok": False, "description": "unavailable"}
        return {"ok": True, "result": {}}

    r, calls, state = _mk(mod, tmp_path, api=api)
    r.poll_once()
    text, meta = calls["delivered"][0]
    assert "download failed" in text.lower()
    assert not meta.get("unsupported"), (
        "a failed photo download was also reported as an unsupported type — double signal")
