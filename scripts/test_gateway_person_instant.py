"""A person's message is never bounced (operator ruling via gm msg_5d3c5a01: "my messages should not be queued,
they should be instant").

POST /agent-message from a PERSON's surface that reads a held answer (source not "mail", no automation
X-Actor, accepts:["held"]: the iPhone, watch, Mac, Quest, the web chat, the voice call):
  R1  a mid-turn pane whose composer is safe is typed NOW (the CLI queues it); the composer is looked at
      again immediately before the keys, and a pane that stopped being safe is held instead;
  R2  every other "cannot type now" answer is HELD (a gateway_hold msg_store row, delivered raw at the
      seat's next boundary), never 409;
  R3  the rest is unchanged: compact 409, protected 403, unknown 404, cleared unverified submit, mail,
      automation actors, and a client without accepts:["held"].
Real app (scope middleware) + a scratch msg_store; tmux and the detector are stubbed.
"""
import asyncio
import json
import logging
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402

FLEET = "fleet-bearer-0123456789abcdef-for-tests"
#: The real kick, before the box fixture stubs it (the pass-4 kick tests put it back).
_REAL_PERSON_KICK = G._person_router_kick

WORKING_PANE = "✻ Sautéing… (esc to interrupt)\n  ⎿ running a subagent\n❯ \n"
WORKING_CLEARED = "✻ Sautéing… (esc to interrupt)\n❯ \n"
MENU_PANE = "Claude needs your permission to run Bash\n 1. Yes\n 2. Yes, and don't ask again\n 3. No\n"
DRAFT_PANE = "✻ Sautéing… (esc to interrupt)\n❯ my own half-typed draft\n"
CHIP_PANE = "✻ Sautéing… (esc to interrupt)\n❯ [Pasted text #1 +40 lines]\n"
IDLE_PANE = "some output\n❯ \n"

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
    type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
    source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
    metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
    attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ------------------------------------------------------------------ the app, a scratch store
@pytest.fixture(autouse=True)
def _no_runner_tmux(monkeypatch):
    """Results must not depend on whether the runner's shell sits in tmux."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    tok = tmp_path / "gateway-token"
    tok.write_text(FLEET)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setattr(G, "ORCH_DIR", data)
    monkeypatch.setattr(G, "TOKEN_FILE", tok)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"seat", "gm"})
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: None)
    db = tmp_path / "tasks.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.close()
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    from msg_store import MessageStore
    MessageStore(db_path=str(db)).migrate()
    monkeypatch.setattr(G, "_held_store", lambda: MessageStore(db_path=str(db)))
    kicks = []
    monkeypatch.setattr(G, "_person_router_kick", lambda: kicks.append(time.monotonic()))
    monkeypatch.setitem(G._person_kick, "handle", None)
    monkeypatch.setitem(G._person_kick, "loop", None)
    return {"data": data, "db": db, "fleet": FLEET, "kicks": kicks}


#: The shipped apps' User-Agent SHAPE (product/build CFNetwork/version Darwin/version); synthetic numbers.
PHONE_UA = "OrchestraUltra/1 CFNetwork/1 Darwin/1"
WATCH_UA = "OrchestraUltraWatch/1 CFNetwork/1 Darwin/1"
AGENT_UA = "curl/8.5.0"


def _headers(box, actor=None, ua=PHONE_UA, extra=None):
    h = {"Authorization": f"Bearer {box['fleet']}", "User-Agent": ua}
    if actor:
        h["X-Actor"] = actor
    h.update(extra or {})
    return h


def _post(box, body, actor=None, ua=PHONE_UA, extra=None):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-message", json=body, headers=_headers(box, actor, ua, extra))
            return r.status, await r.json()
    return asyncio.run(go())


def _post_many(box, bodies, gap_s=0.0, wait_s=0.0, ua=PHONE_UA):
    """Several sends on ONE app and loop (the debounced kick lives on that loop), then wait."""
    async def go():
        out = []
        async with TestClient(TestServer(G.build_app())) as c:
            for b in bodies:
                r = await c.post("/agent-message", json=b, headers=_headers(box, ua=ua))
                out.append((r.status, await r.json()))
                if gap_s:
                    await asyncio.sleep(gap_s)
            await asyncio.sleep(wait_s)
        return out
    return asyncio.run(go())


def _rows(box):
    con = sqlite3.connect(str(box["db"]))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM messages")]
    finally:
        con.close()


class _Refuse:
    """verified_inject stand-in that refuses with `info` and records each call's keyword arguments."""
    def __init__(self, info):
        self.info, self.calls = info, []

    def __call__(self, s, t, f=False, source="mail", **k):
        self.calls.append({"source": source, **k})
        return False, dict(self.info)


def _refuse(info):
    return _Refuse(info)


PHONE = {"session": "seat", "text": "look at the failing test", "accepts": ["held"]}   # iPhone/watch: fleet bearer, no actor, app UA

BUSY_SUBTYPES = {
    "codex_or_gemini_mid_turn": {"reason": "busy", "state": "working", "activity": "Active turn"},
    "typed_text_in_composer": {"reason": "busy", "state": "working", "activity": "Composer has unsubmitted text",
                               "composer_text": "my own half-typed draft"},
    "stranded_chip": {"reason": "busy", "state": "stranded", "activity": "stranded",
                      "stranded": {"text": "[Pasted text #1]", "age_s": 40}, "composer_text": "[Pasted text #1]"},
    "permission_dialog": {"reason": "busy", "state": "waiting",
                          "activity": "Menu/permission prompt is pending — needs the operator"},
    "no_composer_on_screen": {"reason": "busy", "state": "working", "activity": "No composer accepting input"},
    "recheck_flipped_unsafe": {"reason": "busy", "state": "working", "recheck": "no_composer",
                               "activity": G.MID_TURN_RECHECK_WORDS["no_composer"]},
    "pane_lock_busy": {"reason": "busy", "state": "busy", "activity": "another sender is typing into this pane"},
    "stopped_pane": {"reason": "busy", "state": "stopped", "activity": ""},
    "initializing": {"reason": "initializing", "state": "idle", "uptime_s": 3},
    "grok_unverified_composer": {"reason": "unverified-composer", "state": "working", "runtime": "grok"},
    "headless_question_waiting": {"reason": "busy", "state": "waiting", "headless": True,
                                  "activity": "A question or permission is waiting — needs the operator"},
    "headless_supervisor_down": {"reason": "busy", "state": "offline", "headless": True, "activity": "down"},
    "headless_idle_only_caller": {"reason": "busy", "state": "headless", "headless": True,
                                  "activity": "this seat runs headless; slash commands are not sent to it yet"},
    "stalled_turn": {"reason": "busy", "state": "stalled", "activity": "Active turn, screen unchanged"},
}


#: Causes that clear on their own (or by the operator's own answer): HELD. name -> the hold class.
HELD_SUBTYPES = {
    "codex_or_gemini_mid_turn": "mid_turn",
    "permission_dialog": "permission_or_menu",
    "no_composer_on_screen": "mid_turn",
    "recheck_flipped_unsafe": "permission_or_menu",
    "pane_lock_busy": "pane_lock",
    "initializing": "cli_starting",
    "headless_question_waiting": "question_pending",
    "headless_idle_only_caller": "idle_only_headless",
    "stalled_turn": "mid_turn",
}
#: Causes that never clear by themselves (or may already have delivered): the LIVE answer, no row. name -> status.
LIVE_SUBTYPES = {
    "typed_text_in_composer": 409,
    "stranded_chip": 409,
    "stopped_pane": 409,
    "headless_supervisor_down": 409,
    "grok_unverified_composer": 502,
}


def test_every_busy_subtype_is_classified():
    assert set(HELD_SUBTYPES) | set(LIVE_SUBTYPES) == set(BUSY_SUBTYPES)
    assert set(HELD_SUBTYPES.values()) == set(G.PERSON_HOLD_CLASSES), "every hold class has a case"
    for name, cls in HELD_SUBTYPES.items():
        assert G._person_hold_class(BUSY_SUBTYPES[name]) == cls, name
    for name in LIVE_SUBTYPES:
        assert G._person_hold_class(BUSY_SUBTYPES[name]) is None, name


@pytest.mark.parametrize("name", sorted(LIVE_SUBTYPES))
def test_M1_a_cause_that_never_clears_by_itself_keeps_the_live_answer_and_writes_no_row(box, monkeypatch, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, PHONE)
    assert status == LIVE_SUBTYPES[name] and body["ok"] is False and not body.get("held"), body
    assert _rows(box) == [] and box["kicks"] == []
    if BUSY_SUBTYPES[name].get("composer_text"):
        assert body["composer_text"] == BUSY_SUBTYPES[name]["composer_text"]   # the stuck-text contract


@pytest.mark.parametrize("name", sorted(HELD_SUBTYPES))
def test_R2_a_person_send_to_a_seat_busy_for_a_passing_cause_is_HELD(box, monkeypatch, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, PHONE)
    assert status != 409, f"{name}: a person's send bounced: {body}"
    assert status == G.PERSON_HOLD_STATUS == 200
    assert body["ok"] is True and body["held"] is True and body["delivered"] is False
    assert body["state"] == "held_for_boundary" and body["held_reason"] == BUSY_SUBTYPES[name]["reason"]
    assert body["hold_class"] == HELD_SUBTYPES[name]
    rows = _rows(box)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["id"] == body["message_id"] and row["to_agent"] == "seat"
    assert row["body"] == PHONE["text"] and row["source"] == "gateway_hold" and row["status"] == "pending"
    assert row["from_agent"] == "operator"           # no actor: the principal default, as _hold_for_boundary says
    meta = json.loads(row["metadata"])
    assert meta["held_for_boundary"] is True and meta["deliver_raw"] is True


def test_R2_web_chat_held_send_is_attributed_as_today(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    status, body = _post(box, PHONE, actor="operator:web")
    assert status == 200 and body["held"]
    assert _rows(box)[0]["from_agent"] == "web"      # _name or _cls, exactly as the pure-working hold already does


def test_R2_quest_source_is_a_person_and_rides_metadata(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    status, body = _post(box, {**PHONE, "source": "quest"})
    assert status == 200 and body["held"]
    assert json.loads(_rows(box)[0]["metadata"])["source"] == "quest"


def test_R2_a_failed_durable_write_is_never_a_409_and_never_claims_held(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(G, "_hold_for_boundary", boom)
    status, body = _post(box, PHONE)
    assert status == 409 and body["ok"] is False and body["held"] is False and body["busy"] is True
    assert "disk full" in body["hold_failed"]


def test_R4_held_for_person_is_logged_with_its_reason(box, monkeypatch, caplog):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["recheck_flipped_unsafe"]))
    with caplog.at_level(logging.INFO, logger=G.log.name):  # noqa: E501
        _post(box, PHONE)
    lines = [r.getMessage() for r in caplog.records if "AGENT-MESSAGE person" in r.getMessage()]
    assert len(lines) == 1 and "held-for-person" in lines[0] and "no_composer" in lines[0]


# ------------------------------------------------------------------ R1 at the handler
def test_R1_typed_mid_turn_answers_delivered_with_queued_mid_turn_and_logs(box, monkeypatch, caplog):
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False, source="mail":
                        (True, {"state": "working", "attempts": 1, "queued_mid_turn": True}))
    with caplog.at_level(logging.INFO, logger=G.log.name):
        status, body = _post(box, PHONE)
    assert status == 200 and body["delivered"] is True and body["queued_mid_turn"] is True
    assert _rows(box) == []
    assert any("typed mid-turn" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ R3: what must not change
@pytest.mark.parametrize("name", ["typed_text_in_composer", "permission_dialog", "pane_lock_busy"])
def test_R3_mail_source_keeps_the_409(box, monkeypatch, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, {**PHONE, "source": "mail"}, actor="automation:boundary-delivery")
    assert status == 409 and body["busy"] is True and _rows(box) == []


@pytest.mark.parametrize("name", ["typed_text_in_composer", "permission_dialog", "initializing"])
def test_R3_an_automation_actor_keeps_its_old_answer(box, monkeypatch, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, PHONE, actor="automation:arturo-inject")
    assert status in (409, 502) and not body.get("held") and _rows(box) == []


def test_R3_automation_pure_mid_turn_keeps_the_legacy_hold(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    status, body = _post(box, {**PHONE, "source": "mail"}, actor="automation:arturo-inject")
    assert status == 200 and body["held"] is True and "held_reason" not in body
    assert _rows(box)[0]["from_agent"] == "arturo-inject"


@pytest.mark.parametrize("actor", [None, "operator:reaction", "operator:web"])
def test_R3_a_client_without_accepts_held_keeps_the_409(box, monkeypatch, actor):
    """The dashboard's legacy /inject, the reaction wake and the iOS share sheet read anything but 409 as a failure."""
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["typed_text_in_composer"]))
    status, body = _post(box, {"session": "seat", "text": "hi"}, actor=actor)
    assert status == 409 and body.get("composer_text") and _rows(box) == []


def test_R3_compact_refused_is_still_409_for_a_person(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "compact_refused", "detail": G.COMPACT_OFF}))
    status, body = _post(box, {**PHONE, "text": "/compact"})
    assert status == 409 and body["reason"] == "compact_refused" and _rows(box) == []


def test_R3_exit_is_still_refused_not_held(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "slash_command_refused", "detail": "x"}))
    status, body = _post(box, {**PHONE, "text": "/exit"})
    assert status == 502 and _rows(box) == []


# ------------------------------------------------------------------ H1: a slash command is never held
@pytest.mark.parametrize("cmd", ["/exit", "/quit", "  /EXIT now", "/quit please"])
def test_H1_exit_and_quit_are_refused_before_anything_even_under_a_busy_pane_lock(box, monkeypatch, cmd):
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: pytest.fail("/exit reached the inject path"))
    status, body = _post(box, {**PHONE, "text": cmd})
    assert status == 502 and body["reason"] == "slash_command_refused" and _rows(box) == []


@pytest.mark.parametrize("cmd", ["/exit", "/quit", "/clear", "/exit\ufeff", "/exit.", "/quit!", "/exit/x", "/clear.",
                                 "hello\n/exit\u200b"])
def test_H1_real_inject_under_a_busy_pane_lock_never_writes_a_slash_row(box, monkeypatch, cmd):
    """The proven H1 path, end to end: the REAL verified_inject, another typer holding the pane lock."""
    from contextlib import contextmanager

    @contextmanager
    def busy(session):
        yield False
    monkeypatch.setattr(G, "_pane_guard", busy)
    status, body = _post(box, {**PHONE, "text": cmd})
    assert status in (409, 502) and not body.get("held") and _rows(box) == [], body
    assert box["kicks"] == []


@pytest.mark.parametrize("cmd", ["/clear", "/model opus", "/compact"])
@pytest.mark.parametrize("name", ["codex_or_gemini_mid_turn", "pane_lock_busy", "permission_dialog"])
def test_H1_no_slash_command_is_ever_held_for_a_person(box, monkeypatch, cmd, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, {**PHONE, "text": cmd})
    assert status == 409 and not body.get("held") and _rows(box) == []


def test_H1_the_legacy_holds_never_take_a_slash_command_either(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    status, _ = _post(box, {**PHONE, "text": "/clear", "source": "mail"}, actor="automation:arturo-inject")
    assert status == 409 and _rows(box) == []
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "unverified_submit", "state": "idle",
                                                       "cleared": True, "text_returned": "/clear"}))
    status, body = _post(box, {**PHONE, "text": "/clear"})
    assert status == 502 and body["text_returned"] == "/clear" and _rows(box) == []


@pytest.mark.parametrize("body", ["/exit", "  /clear", "/exit\ufeff", "/quit!", "/exit.", "a line\n/exit/x"])
def test_H1_hold_for_boundary_itself_refuses_a_slash_body(box, body):
    with pytest.raises(ValueError):
        G._hold_for_boundary("seat", body, "operator")
    assert _rows(box) == []


def test_H1_mail_keeps_its_first_line_rule(box, monkeypatch):
    """A later "/exit" line in a MAIL body is pasted text, never run: mail is delivered as before; a person's send
    with the same body is refused (a hold would type it raw later)."""
    seen = []
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False, source="mail", **k: (seen.append(source) or True, {}))
    status, _ = _post(box, {"session": "seat", "text": "see below\n/exit", "source": "mail"}, actor="automation:lineage")
    assert status == 200 and seen == ["mail"]
    status, body = _post(box, {**PHONE, "text": "see below\n/exit"})
    assert status == 502 and body["reason"] == "slash_command_refused" and seen == ["mail"]


def test_a_refusal_that_is_not_held_never_says_held(box, monkeypatch):
    """The recheck's words are neutral; only the handler, which knows a hold happened, says "held"."""
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["recheck_flipped_unsafe"]))
    status, body = _post(box, {"session": "seat", "text": "hi"}, actor="operator:share-sheet")   # no accepts: 409
    assert status == 409 and not body.get("held") and "held" not in body["activity"].lower(), body
    status, body = _post(box, PHONE)                                                           # the phone: held
    assert status == 200 and body["held"] and body["activity"].endswith(G.HELD_FOR_BOUNDARY_WORDS)
    for words in G.MID_TURN_RECHECK_WORDS.values():
        assert "held" not in words.lower()


def test_a_failed_person_hold_is_a_409_the_clients_know(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(G, "_hold_for_boundary", boom)
    status, body = _post(box, PHONE)
    assert status == 409 and body["busy"] is True and body["held"] is False and "hold_failed" in body
    assert _rows(box) == []


def test_R3_unknown_session_is_404(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: pytest.fail("injected into an unknown session"))
    status, _ = _post(box, {**PHONE, "session": "nope"})
    assert status == 404 and _rows(box) == []


def test_R3_protected_session_refusal_is_403(box, monkeypatch):
    (box["data"] / "state" / "protected-sessions.json").write_text(json.dumps({"enabled": True, "protected": ["gm"]}))
    monkeypatch.setattr(G, "PROTECTED_SESSIONS_PATH", box["data"] / "state" / "protected-sessions.json")
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: pytest.fail("a protected session was typed into"))
    status, body = _post(box, {**PHONE, "session": "gm"}, actor="automation:x")
    assert status == 403 and body["reason"] == "automation_actuation_refused" and _rows(box) == []


def test_R3_cleared_unverified_submit_is_held_as_before(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "unverified_submit", "state": "working",
                                                       "cleared": True, "text_returned": "x"}))
    status, body = _post(box, PHONE)
    assert status == 200 and body["held"] is True and body["cleared"] is True and len(_rows(box)) == 1


# ------------------------------------------------------------------ R1 inside verified_inject (tmux stubbed)
class _Det:
    def __init__(self, state, runtime="claude", hook=None):
        self._r = {"state": state, "activity": "Running a subagent", "process": {"runtime": runtime}}
        self._hook = hook

    def get_agent_status(self, sess):
        return dict(self._r)

    def _read_hook_event(self, sess):
        return self._hook


class _Tmux:
    def __init__(self, captures):
        self.calls = []
        self._c = list(captures)

    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        out = self._c.pop(0) if (args[0] == "capture-pane" and self._c) else ""
        return types.SimpleNamespace(returncode=0, stdout=out)


def _pane(monkeypatch, state, captures, runtime="claude", hook=None):
    t = _Tmux(captures)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    monkeypatch.setattr(G, "_agent_status", lambda: _Det(state, runtime, hook))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def _typed(t):
    return [c for c in t.calls if c[0] == "send-keys" and "-l" in c]


def _captures(t):
    return sum(1 for c in t.calls if c[0] == "capture-pane")


def test_R1_safe_claude_mid_turn_is_typed_now_after_a_second_look(monkeypatch):
    # pre-capture, the re-check right before the keys, then the post-Enter capture with the composer cleared
    t = _pane(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "look at the failing test", source="person")
    assert ok and info["queued_mid_turn"] is True, info
    assert _typed(t) and _captures(t) == 3


@pytest.mark.parametrize("flip,why", [(MENU_PANE, "no_composer"), (DRAFT_PANE, "composer_text"),  # noqa
                                      (CHIP_PANE, "stranded_chip")])
def test_R1_recheck_that_flips_unsafe_types_nothing(monkeypatch, flip, why):
    t = _pane(monkeypatch, "working", [WORKING_PANE, flip])
    ok, info = G.verified_inject("seat", "look at the failing test", source="person")
    assert not ok and info["reason"] == "busy" and info["recheck"] == why, info
    assert not _typed(t), "typed into a pane the last look found unsafe"


def test_R1_fresh_permission_hook_holds_a_mid_turn_person_send(monkeypatch):
    t = _pane(monkeypatch, "working", [WORKING_PANE, WORKING_PANE],
              hook={"state": "waiting_permission", "ts": time.time() - 5})
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert not ok and info["recheck"] == "permission_pending" and not _typed(t)


def test_R1_a_stale_permission_hook_does_not_block(monkeypatch):
    t = _pane(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_CLEARED],
              hook={"state": "waiting_permission", "ts": time.time() - G.MID_TURN_HOOK_WAITING_TTL_S - 5})
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert ok and _typed(t), info


def test_R1_codex_mid_turn_still_types_with_the_recheck(monkeypatch):
    t = _pane(monkeypatch, "working", ["• Working (12s • esc to interrupt)\n› \n", "• Working (12s • esc to interrupt)\n› \n",
                                       "• Working (13s • esc to interrupt)\n› \n"], runtime="codex")
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert ok and info.get("queued_mid_turn") and _typed(t), info


def test_R3_mail_mid_turn_has_no_extra_look(monkeypatch):
    """Mail's call is byte-identical: no re-check capture, no new field read from the pane."""
    t = _pane(monkeypatch, "working", [WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", source="mail")
    assert ok and _captures(t) == 2


def test_R1_an_idle_person_send_has_no_extra_look(monkeypatch):
    t = _pane(monkeypatch, "idle", [IDLE_PANE, "✻ Working… (esc to interrupt)\n❯ \n"])
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert ok and "queued_mid_turn" not in info and _captures(t) == 2


# ------------------------------------------------------------------ end to end: the re-check flips, the phone gets held
def test_end_to_end_recheck_flip_is_held_for_the_phone(box, monkeypatch):
    t = _pane(monkeypatch, "working", [WORKING_PANE, MENU_PANE])
    monkeypatch.setattr(G, "_client_attached", lambda s: False)
    status, body = _post(box, PHONE)
    assert status == 200 and body["held"] is True and body["held_reason"] == "busy"
    assert not _typed(t) and len(_rows(box)) == 1


# ------------------------------------------------------------------ pass 2 (gm msg_b73cc273): who may be typed mid-turn
def _delivered(calls):
    def inject(s, t, f=False, source="mail", **k):
        calls.append(k)
        return True, {"state": "working", "attempts": 1, "queued_mid_turn": True}
    return inject


@pytest.mark.parametrize("ua", [PHONE_UA, WATCH_UA])
def test_v2_the_phone_and_watch_user_agents_keep_mid_turn_typing(box, monkeypatch, ua):
    calls = []
    monkeypatch.setattr(G, "verified_inject", _delivered(calls))
    status, body = _post(box, PHONE, ua=ua)
    assert status == 200 and body["delivered"] and calls == [{}], calls      # no idle_only: mid-turn allowed


@pytest.mark.parametrize("actor,extra", [("operator:web", None), ("operator:voice-call", None), ("app:mac", None),
                                         (None, {"X-Client-Install": "mac-install-1"})])
def test_v2_other_person_signals_keep_mid_turn_typing(box, monkeypatch, actor, extra):
    calls = []
    monkeypatch.setattr(G, "verified_inject", _delivered(calls))
    status, body = _post(box, PHONE, actor=actor, ua="node", extra=extra)
    assert status == 200 and calls == [{}], calls


def test_v2_a_paired_device_token_is_a_person(box, monkeypatch):
    from scripts.device_tokens import DeviceStore
    _id, tok = DeviceStore(box["data"] / "state" / "devices").mint("quest-3", ["read", "message"])
    calls = []
    monkeypatch.setattr(G, "verified_inject", _delivered(calls))

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-message", json=PHONE,
                             headers={"Authorization": f"Bearer {tok}", "User-Agent": "Quest/1"})
            return r.status
    assert asyncio.run(go()) == 200 and calls == [{}], calls


def test_v2_fleet_bearer_without_a_person_signal_is_IDLE_ONLY_and_held_as_operator(box, monkeypatch):
    fake = _refuse({"reason": "busy", "state": "working", "activity": "A turn is running; this needs an idle agent"})
    monkeypatch.setattr(G, "verified_inject", fake)
    status, body = _post(box, PHONE, ua=AGENT_UA)
    assert fake.calls == [{"source": "person", "idle_only": True}]
    assert status == 200 and body["held"] is True and body["activity"] == G.IDLE_ONLY_HOLD_WORDS
    assert _rows(box)[0]["from_agent"] == "operator"


def test_v2_fleet_bearer_without_a_person_signal_is_never_typed_into_a_running_turn(box, monkeypatch):
    """End to end with the real verified_inject: a safe mid-turn Claude pane that the PHONE would get typed
    into is held for this caller, and nothing reaches the pane."""
    t = _pane(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_CLEARED])
    monkeypatch.setattr(G, "_client_attached", lambda s: False)
    status, body = _post(box, PHONE, ua=AGENT_UA)
    assert status == 200 and body["held"] is True and not _typed(t) and len(_rows(box)) == 1


def test_v2_fleet_bearer_without_a_person_signal_is_still_typed_into_an_idle_pane(box, monkeypatch):
    t = _pane(monkeypatch, "idle", [IDLE_PANE, "✻ Working… (esc to interrupt)\n❯ \n"])
    monkeypatch.setattr(G, "_client_attached", lambda s: False)
    status, body = _post(box, PHONE, ua=AGENT_UA)
    assert status == 200 and body["delivered"] is True and _typed(t) and _rows(box) == []


# ------------------------------------------------------------------ pass 2: the debounced router kick
def test_v2_one_person_hold_asks_for_one_router_pass(box, monkeypatch):
    monkeypatch.setattr(G, "PERSON_HOLD_KICK_DELAY_S", 0.2)
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    t0 = time.monotonic()
    out = _post_many(box, [PHONE], wait_s=0.6)
    assert out[0][0] == 200 and len(box["kicks"]) == 1
    assert box["kicks"][0] - t0 >= 0.2                 # after the delay, not inside the request


def test_v2_ten_holds_in_one_window_schedule_one_pass(box, monkeypatch):
    """No wall clock: a window long enough that the ten sends cannot leave it, then the scheduler's own record
    (ONE handle, ten sends, no kick yet), then the handle fired by hand gives exactly one kick."""
    monkeypatch.setattr(G, "PERSON_HOLD_KICK_DELAY_S", 600.0)
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    scheduled = []
    real = G._schedule_person_kick
    monkeypatch.setattr(G, "_schedule_person_kick", lambda loop: scheduled.append(real(loop)) or scheduled[-1])
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            res = [(await c.post("/agent-message", json=PHONE, headers=_headers(box))).status for _ in range(10)]
            h = G._person_kick["handle"]
            assert h is not None and not h.cancelled() and box["kicks"] == []
            h.cancel()
            G._person_kick["handle"] = None
            G._run_person_kick()                                     # what the one handle would have done
            return res
    res = asyncio.run(go())
    assert res == [200] * 10 and len(_rows(box)) == 10
    assert scheduled == [True] + [False] * 9, scheduled
    assert len(box["kicks"]) == 1


def test_v2_a_failing_kick_never_fails_the_send(box, monkeypatch):
    monkeypatch.setattr(G, "PERSON_HOLD_KICK_DELAY_S", 0.1)
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))

    def boom():
        raise OSError("no router")
    monkeypatch.setattr(G, "_person_router_kick", boom)
    out = _post_many(box, [PHONE, PHONE], gap_s=0.3, wait_s=0.3)
    assert [s for s, _ in out] == [200, 200] and all(b["held"] for _, b in out)


def test_v2_scheduling_trouble_never_fails_the_send(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))

    monkeypatch.setitem(G._person_kick, "handle", object())     # a broken pending handle: scheduling raises inside
    status, body = _post(box, PHONE)
    assert status == 200 and body["held"]
    assert G._person_kick["handle"] is None, "the broken slot is dropped so the next hold schedules a real kick"


def test_v2_a_delivered_send_and_mail_ask_for_no_extra_pass(box, monkeypatch):
    monkeypatch.setattr(G, "PERSON_HOLD_KICK_DELAY_S", 0.1)
    monkeypatch.setattr(G, "verified_inject", _delivered([]))
    _post_many(box, [PHONE], wait_s=0.3)
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    _post_many(box, [{**PHONE, "source": "mail"}], wait_s=0.3)       # the legacy hold: the store's own kick only
    assert box["kicks"] == []



# ------------------------------------------------------------------ pass 4 (review)
def test_M1_recheck_that_finds_a_draft_keeps_the_409_with_the_text_end_to_end(box, monkeypatch):
    t = _pane(monkeypatch, "working", [WORKING_PANE, DRAFT_PANE])
    monkeypatch.setattr(G, "_client_attached", lambda s: False)
    status, body = _post(box, PHONE)
    assert status == 409 and body["recheck"] == "composer_text"
    assert body["composer_text"] == "my own half-typed draft" and not _typed(t) and _rows(box) == []


def test_M1_recheck_chip_and_draft_carry_the_composer_text(monkeypatch):
    for pane, why in ((DRAFT_PANE, "composer_text"), (CHIP_PANE, "stranded_chip")):
        _pane(monkeypatch, "working", [WORKING_PANE, pane])
        ok, info = G.verified_inject("seat", "hi", source="person")
        assert not ok and info["recheck"] == why and info["composer_text"], info


def _fake_router(tmp_path):
    """A stand-in router script: it only proves a process was started, by writing a marker file."""
    marker = tmp_path / "router-ran"
    script = tmp_path / "fake-router.py"
    script.write_text(f"import pathlib, sys; pathlib.Path({str(marker)!r}).write_text(' '.join(sys.argv[1:]))\n")
    return script, marker


def _wait_for(path, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_v4_the_real_kick_with_a_scratch_db_never_starts_a_router(box, monkeypatch):
    """The real chain _person_router_kick -> msg_store.kick_router_after_send(<scratch db>): even with pytest's
    suppression lifted, a store that is not the installed bus starts nothing (a scratch DB never drives panes)."""
    monkeypatch.setattr(G, "_person_router_kick", _REAL_PERSON_KICK)
    import msg_store as MS
    import subprocess as SP
    monkeypatch.setattr(MS, "_kick_suppressed", lambda: False)
    started = []
    monkeypatch.setattr(SP, "Popen", lambda *a, **k: started.append(a) or pytest.fail("a router was started"))
    G._person_router_kick()
    assert started == []


def test_v4_the_real_kick_starts_a_router_process_when_the_target_resolves(box, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "_person_router_kick", _REAL_PERSON_KICK)
    import msg_store as MS
    import tempfile
    script, marker = _fake_router(tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(MS, "_kick_suppressed", lambda: False)
    monkeypatch.setattr(MS, "_kick_target", lambda db: (tmp_path, script, tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))       # the flock file stays in the scratch dir
    G._person_router_kick()
    assert _wait_for(marker), "no router process was started"
    assert marker.read_text() == "--cron"


# ------------------------------------------------------------------ pass 4, gm msg_4a57fe0a
def test_gm_a_slash_refusal_logs_who_asked(box, monkeypatch, caplog):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["pane_lock_busy"]))
    with caplog.at_level(logging.INFO, logger=G.log.name):
        _post(box, {**PHONE, "text": "/exit"})
        _post(box, {**PHONE, "text": "/clear"}, actor="operator:web")
    lines = [r.getMessage() for r in caplog.records if "slash command" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "principal=fleet-bearer" in lines[0] and "OrchestraUltra/1" in lines[0] and "'/exit'" in lines[0]
    assert "actor=operator:web" in lines[1] and "'/clear'" in lines[1]
    assert FLEET not in " ".join(lines)                       # never the bearer itself


@pytest.mark.parametrize("name,words", [
    ("typed_text_in_composer", "unsent in the agent's box"),
    ("stranded_chip", "unsent in the agent's box"),
    ("stopped_pane", "isn't running"),
    ("headless_supervisor_down", "may have arrived"),
    ("grok_unverified_composer", "can't be typed into safely"),
])
def test_gm_a_permanent_refusal_says_what_to_do_in_plain_words(box, monkeypatch, name, words):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, PHONE)
    assert status == LIVE_SUBTYPES[name] and words in body["activity"], body
    assert "detector_activity" in body and _rows(box) == []


def test_gm_the_held_row_records_why_for_the_sla_mail(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    _post(box, PHONE)
    meta = json.loads(_rows(box)[0]["metadata"])
    assert meta["hold_class"] == "permission_or_menu" and "Menu/permission" in meta["hold_why"]



# ------------------------------------------------------------------ pass 5: invisible prefixes and later lines
@pytest.mark.parametrize("text", ["\ufeff/exit", "\u200b/exit", "\uff0fexit", "hello\n/exit", "\u2060/quit",
                                  "\u200e/exit", "\u00a0\u200b/QUIT now", "ok\r\n\ufeff/exit"])
def test_v5_exit_behind_an_invisible_prefix_or_on_a_later_line_is_refused_and_never_stored(box, monkeypatch, text):
    monkeypatch.setattr(G, "verified_inject", lambda *a, **k: pytest.fail("reached the inject path"))
    status, body = _post(box, {**PHONE, "text": text})
    assert status == 502 and body["reason"] == "slash_command_refused" and _rows(box) == []


@pytest.mark.parametrize("text", ["\u200b/clear", "\uff0fclear", "hello\n/clear", "\ufeff/model opus"])
@pytest.mark.parametrize("name", ["codex_or_gemini_mid_turn", "pane_lock_busy"])
def test_v5_any_slash_command_behind_a_prefix_or_on_a_later_line_is_never_held(box, monkeypatch, text, name):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES[name]))
    status, body = _post(box, {**PHONE, "text": text})
    assert status == 409 and not body.get("held") and _rows(box) == []


@pytest.mark.parametrize("text", ["\u200b/clear", "\uff0fexit", "a\n/clear"])
def test_v5_hold_for_boundary_refuses_those_bodies_too(box, text):
    with pytest.raises(ValueError):
        G._hold_for_boundary("seat", text, "operator")


def test_v5_ordinary_text_with_a_slash_inside_is_still_held(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    status, body = _post(box, {**PHONE, "text": "look at /tmp/x and a/b"})
    assert status == 200 and body["held"] and len(_rows(box)) == 1


# ------------------------------------------------------------------ v6: a path is text, not a slash command (gm msg_1cf0ff32)
PATH_BODIES = ["/home/<you>/orchestraos/scripts/x.py", "Read /tmp/agent-msg-msg_x.md\n/tmp/agent-msg-msg_x.md",
               "see below\n/home/<you>/.orchestra/state/backups/y/", "﻿/tmp/x", "/ just a slash", "//comment"]


@pytest.mark.parametrize("text", PATH_BODIES)
def test_v6_a_line_that_starts_with_a_path_is_held_like_any_text(box, monkeypatch, text):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    status, body = _post(box, {**PHONE, "text": text})
    assert status == 200 and body["held"] and len(_rows(box)) == 1


@pytest.mark.parametrize("text", PATH_BODIES)
def test_v6_a_path_is_neither_a_slash_line_nor_session_ending(text):
    assert G._slash_lines(text) == [] and not G._ends_session(text)


@pytest.mark.parametrize("text", ["/exit", "/clear", "/model opus", "/superpowers:brainstorm", "/ship", "x\n/compact",
                                  "／exit", "/rate-limit-options"])
def test_v6_a_command_name_is_still_a_slash_line(text):
    assert G._slash_lines(text)


def test_v6_hold_for_boundary_takes_a_path_body(box):
    G._hold_for_boundary("seat", "/home/<you>/x.py\n/tmp/y", "operator")
    assert len(_rows(box)) == 1
