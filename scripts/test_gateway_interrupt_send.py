"""A person's "now": interrupt=1 on POST /agent-message, and the honest outcome of every accepted send (operator
order via gm msg_4952c4ff: "messages which are important for context and steering are getting queued and not being
ingested until the turn is done").

Today a person's send into a working seat is typed and the CLI queues it until the turn ends (outcome
"delivered-steer"); the dashboard showed that as QUEUED, which read as "not delivered". With interrupt:true (or
?interrupt=1) and the seat mid-turn, the gateway sends Escape first (the CLI's own interrupt: the running step ends,
the turn stops cleanly), waits for the composer, looks once more, then types + Enter: outcome "interrupted". Never on
a menu, a permission prompt or typed text (the same holds refuse first). A turn that does not stop in time is steered
as today and the answer says so (interrupt_failed). Mail and automation never interrupt. Every accepted answer carries
`outcome`: delivered-now | delivered-steer | interrupted | held.

Real app + a scratch msg_store; tmux and the detector are stubbed (the harness of test_gateway_person_instant).
"""
import asyncio
import logging
import time
import types

import pytest
from aiohttp.test_utils import TestClient, TestServer

import scripts.watch_gateway as G
from test_gateway_person_instant import (  # noqa: F401  (fixtures and helpers)
    BUSY_SUBTYPES, IDLE_PANE, MENU_PANE, PHONE, WORKING_PANE, WORKING_CLEARED, AGENT_UA,
    _Det, _Tmux, _captures, _headers, _pane, _post, _refuse, _rows, _typed, box,
)


class _DetI(_Det):
    """The detector with the stop button's bookkeeping hook."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.marked = []

    def mark_turn_interrupted(self, sess):
        self.marked.append(sess)
        return True


def _pane_i(monkeypatch, state, captures, runtime="claude"):
    t = _Tmux(captures)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    det = _DetI(state, runtime)
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t, det


def _keys(t):
    """The keystroke calls in order: ('Escape',) or ('-l', text)."""
    out = []
    for c in t.calls:
        if c[0] == "send-keys":
            out.append(("-l", c[-1]) if "-l" in c else (c[-1],))
    return out


# ------------------------------------------------------------------ inside verified_inject (tmux stubbed)
def test_interrupt_mid_turn_sends_escape_waits_for_the_composer_looks_again_then_types(monkeypatch):
    # pre-capture, the last look, the poll after Escape (composer back), the look after the stop, the post-Enter capture
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE])
    ok, info = G.verified_inject("seat", "stop, read this first", source="person", interrupt=True)
    assert ok and info["interrupted"] is True and "queued_mid_turn" not in info and "interrupt_failed" not in info, info
    keys = _keys(t)
    assert keys[0] == ("Escape",), keys
    assert keys[1] == ("-l", "stop, read this first") and keys[2] == ("Enter",), keys
    assert det.marked == ["seat"], "the stop button's bookkeeping: the hook is not left at working for its TTL"
    assert _captures(t) == 5


def test_interrupt_that_does_not_stop_the_turn_steers_and_says_so(monkeypatch):
    # the polls keep showing the turn (the stub answers "" once its list is spent: no composer, not stopped)
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE] + [WORKING_PANE] * 12 + [WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and info["queued_mid_turn"] is True and "interrupted" not in info, info
    assert "did not stop within" in info["interrupt_failed"]
    keys = _keys(t)
    assert keys.count(("Escape",)) == 1 and ("-l", "hi") in keys


def test_a_menu_that_appears_after_escape_is_never_typed_into(monkeypatch):
    # Escape ends the step, a permission menu replaces the composer: the look after the wait refuses, nothing typed
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE] + [MENU_PANE] * 14)
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert not ok and info["reason"] == "busy" and info["recheck"] == "no_composer", info
    assert not _typed(t), "typed into a menu"
    assert ("Escape",) in _keys(t)


def test_interrupt_on_an_idle_pane_never_sends_escape(monkeypatch):
    t, det = _pane_i(monkeypatch, "idle", [IDLE_PANE, "✻ Working… (esc to interrupt)\n❯ \n"])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and "interrupted" not in info and "queued_mid_turn" not in info, info
    assert ("Escape",) not in _keys(t) and det.marked == []


def test_the_default_send_never_sends_escape(monkeypatch):
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert ok and info["queued_mid_turn"] is True and ("Escape",) not in _keys(t)


def test_mail_never_interrupts_even_if_asked(monkeypatch):
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", source="mail", interrupt=True)
    assert ok and ("Escape",) not in _keys(t) and "interrupted" not in info


def test_an_unsafe_last_look_refuses_before_any_escape(monkeypatch):
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, MENU_PANE])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert not ok and info["recheck"] == "no_composer" and ("Escape",) not in _keys(t) and not _typed(t)


def test_interrupt_turn_reads_the_screen_until_the_composer_is_back(monkeypatch):
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, IDLE_PANE])
    assert G._interrupt_turn("seat") is True and _captures(t) == 3 and det.marked == ["seat"]
    t2, _ = _pane_i(monkeypatch, "working", [])
    assert G._interrupt_turn("seat") is False and _captures(t2) == int(G.INTERRUPT_WAIT_S / G.INTERRUPT_POLL_S)


def test_a_failed_escape_keystroke_is_not_a_stop(monkeypatch):
    class _Bad(_Tmux):
        def __call__(self, *args, timeout=5):
            if args[0] == "send-keys" and args[-1] == "Escape":
                return types.SimpleNamespace(returncode=1, stdout="")
            return super().__call__(*args, timeout=timeout)
    t = _Bad([IDLE_PANE])
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "_agent_status", lambda: _DetI("working"))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    assert G._interrupt_turn("seat") is False and _captures(t) == 0


# ------------------------------------------------------------------ the handler: who may ask, what it answers
class _Deliver:
    def __init__(self, info):
        self.info, self.calls = info, []

    def __call__(self, s, t, f=False, source="mail", **k):
        self.calls.append({"source": source, **k})
        return True, dict(self.info)


def _post_q(box, body, query, actor=None, ua=None, extra=None):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post(f"/agent-message?{query}", json=body, headers=_headers(box, actor, ua or _headers.__defaults__[1], extra))
            return r.status, await r.json()
    return asyncio.run(go())


def test_a_persons_interrupt_reaches_the_inject_and_the_answer_says_interrupted(box, monkeypatch, caplog):
    d = _Deliver({"state": "working", "attempts": 1, "interrupted": True})
    monkeypatch.setattr(G, "verified_inject", d)
    with caplog.at_level(logging.INFO, logger=G.log.name):
        status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and body["delivered"] is True and body["outcome"] == "interrupted" and body["interrupted"] is True
    assert d.calls == [{"source": "person", "interrupt": True}]
    assert any("typed now (interrupted" in r.getMessage() for r in caplog.records)


def test_the_query_flag_works_too(box, monkeypatch):
    d = _Deliver({"state": "working", "interrupted": True})
    monkeypatch.setattr(G, "verified_inject", d)
    status, body = _post_q(box, PHONE, "interrupt=1")
    assert status == 200 and body["outcome"] == "interrupted" and d.calls[0]["interrupt"] is True


@pytest.mark.parametrize("value", [1, "1", "true", "yes", None, False])
def test_only_a_boolean_true_in_the_body_asks_for_an_interrupt(box, monkeypatch, value):
    d = _Deliver({"state": "working", "queued_mid_turn": True})
    monkeypatch.setattr(G, "verified_inject", d)
    status, body = _post(box, {**PHONE, "interrupt": value})
    assert status == 200 and body["outcome"] == "delivered-steer" and not d.calls[0].get("interrupt")


def test_automation_and_mail_never_get_an_interrupt(box, monkeypatch):
    d = _Deliver({"state": "idle"})
    monkeypatch.setattr(G, "verified_inject", d)
    _post(box, {**PHONE, "interrupt": True}, ua=AGENT_UA)                                   # fleet bearer, no person signal
    _post(box, {"session": "seat", "text": "x", "source": "mail", "interrupt": True}, actor="automation:lineage")
    _post(box, {**PHONE, "interrupt": True}, actor="automation:relay")
    assert all(not c.get("interrupt") for c in d.calls), d.calls
    assert d.calls[0].get("idle_only") is True                                           # automation: idle only, as before


@pytest.mark.parametrize("info,outcome", [
    ({"state": "idle", "attempts": 1}, "delivered-now"),
    ({"state": "working", "attempts": 1, "queued_mid_turn": True}, "delivered-steer"),
    ({"state": "working", "attempts": 1, "queued_mid_turn": True, "interrupt_failed": "the turn did not stop within 3 s after Escape"},
     "delivered-steer"),
    ({"state": "working", "attempts": 1, "interrupted": True}, "interrupted"),
], ids=["idle", "steer", "steer-after-failed-interrupt", "interrupted"])
def test_every_delivered_answer_carries_the_honest_outcome(box, monkeypatch, info, outcome):
    monkeypatch.setattr(G, "verified_inject", _Deliver(info))
    status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and body["outcome"] == outcome, body
    if "interrupt_failed" in info:
        assert body["interrupt_failed"] == info["interrupt_failed"]


def test_every_held_answer_says_held(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and body["held"] is True and body["outcome"] == "held"
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["codex_or_gemini_mid_turn"]))
    status, body = _post(box, {"session": "seat", "text": "please read the failing test output", "source": "mail", "accepts": ["held"]}, actor="automation:arturo-inject")
    assert status == 200 and body["held"] is True and body["outcome"] == "held"           # the legacy mid-turn hold too


def test_a_refusal_carries_no_outcome(box, monkeypatch):
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["typed_text_in_composer"]))
    status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 409 and "outcome" not in body


# ------------------------------------------------------------------ the kick: a hold that lands after the fire keeps its handle
def test_a_hold_scheduled_after_the_kick_fires_keeps_its_own_handle(box, monkeypatch):
    """h1's deadline passes, a hold schedules h2 before h1's callback runs, then h1 fires late: the slot keeps h2."""
    monkeypatch.setattr(G, "PERSON_HOLD_KICK_DELAY_S", 600.0)
    kicked = []
    monkeypatch.setattr(G, "_run_person_kick", lambda: kicked.append(1))

    async def go():
        loop = asyncio.get_running_loop()
        assert G._schedule_person_kick(loop) is True
        h1 = G._person_kick["handle"]
        real_time = loop.time
        monkeypatch.setattr(loop, "time", lambda: real_time() + 1000.0)      # h1's deadline is in the past now
        assert G._schedule_person_kick(loop) is True
        h2 = G._person_kick["handle"]
        assert h2 is not h1
        h1._run()                                                           # h1's callback fires late, after h2 exists
        h1.cancel()                                                         # by hand above; the loop must not fire it again
        await asyncio.sleep(0.05)
        assert G._person_kick["handle"] is h2, "the late fire dropped the next window's handle"
        h2.cancel()
        G._person_kick["handle"] = None
    asyncio.run(go())
    assert kicked == [1]
