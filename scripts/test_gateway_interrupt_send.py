"""A person's "now": interrupt=1 on POST /agent-message, and the honest outcome of every accepted send (the operator's
order: "messages which are important for context and steering are getting queued and not being ingested until the
turn is done").

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
    BUSY_SUBTYPES, DRAFT_PANE, IDLE_PANE, MENU_PANE, PHONE, WORKING_PANE, WORKING_CLEARED, AGENT_UA,
    _Det, _Tmux, _captures, _headers, _pane, _post, _refuse, _rows, _typed, box,
)


@pytest.fixture(autouse=True)
def _everything_under_tmp(tmp_path, monkeypatch):
    """No test here may write the live tree's inject journal, pane locks or state: the direct verified_inject
    tests have no `box`, and without these every inject_journal.record() lands in <checkout>/state/ and every
    pane lock in $HOME/runtime/locks."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "orch"))
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("PANE_LOCK_DIR", str(tmp_path / "locks"))


#: An idle prompt under output that merely QUOTES the esc hint: the whole-screen heuristic reads it as a turn.
QUOTED_PANE = "the log line said: esc to interrupt\nmore output\n❯ \n"
#: After Escape stopped the turn, text sits in the composer (someone typing, or the CLI's own doing).
RESTORED_PANE = "some output\n❯ an earlier steer the CLI put back\n"
#: MEASURED on Claude Code 2.1.284 (2026-11-25, a scratch seat): a turn with a todo list in progress. The spinner
#: label is the todo item (no gerund), there is no "esc to interrupt" anywhere on screen, and the spinner sits nine
#: rows above the prompt over the indented todo rows, a blank row and the rule line.
TODO_PANE = ("● Bash(for i in $(seq 1 45); do echo \"tick $i\"; sleep 1; done)\n"
             "  ⎿  tick 27\n     tick 28\n     +26 lines (30s)\n     (ctrl+b ctrl+b (twice) to run in background)\n\n"
             "✻ alpha… (51s · ↓ 2.0k tokens)\n"
             "  ⎿  ◼ alpha\n     ◻ beta\n     ◻ gamma\n     ◻ delta\n     ◻ epsilon\n      … +2 pending\n\n"
             "────────────────────────────────────────────\n❯ \n────────────────────────────────────────────\n"
             "  ⬆ /gsd:update │ Haiku 4.5 │ seat ██░░░░░░░░ 25%\n")
#: The same seat after Escape: the todo rows stay, the spinner row is gone.
TODO_IDLE = ("  ⎿  Interrupted · What should Claude do instead?\n\n"
             "  ⎿  ◼ alpha\n     ◻ beta\n     ◻ gamma\n     ◻ delta\n     ◻ epsilon\n      … +2 pending\n\n"
             "────────────────────────────────────────────\n❯ \n────────────────────────────────────────────\n")


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
    # pre-capture, the last look, the interrupt's own gate (a spinner above the composer NOW), the poll after Escape
    # (composer back), the look after the stop, the post-Enter capture
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE])
    ok, info = G.verified_inject("seat", "stop, read this first", source="person", interrupt=True)
    assert ok and info["interrupted"] is True and "queued_mid_turn" not in info and "interrupt_failed" not in info, info
    keys = _keys(t)
    assert keys[0] == ("Escape",), keys
    assert keys[1] == ("-l", "stop, read this first") and keys[2] == ("Enter",), keys
    assert det.marked == ["seat"], "the stop button's bookkeeping: the hook is not left at working for its TTL"
    assert _captures(t) == 6


def test_interrupt_that_does_not_stop_the_turn_steers_and_says_so(monkeypatch):
    # the polls keep showing the turn (the stub answers "" once its list is spent: no composer, not stopped)
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE] + [WORKING_PANE] * 12 + [WORKING_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and info["queued_mid_turn"] is True and "interrupted" not in info, info
    assert "did not stop within" in info["interrupt_failed"]
    keys = _keys(t)
    assert keys.count(("Escape",)) == 1 and ("-l", "hi") in keys
    assert det.marked == [], "a turn that kept running was marked idle: queued mail would be released into it"


def test_a_menu_that_appears_after_escape_is_never_typed_into(monkeypatch):
    # Escape ends the step, a permission menu replaces the composer: the look after the wait refuses, nothing typed
    t, _ = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE] + [MENU_PANE] * 14)
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert not ok and info["reason"] == "busy" and info["recheck"] == "no_composer", info
    assert not _typed(t), "typed into a menu"
    assert ("Escape",) in _keys(t)
    # the refusal says what happened: Escape went, the stop was not seen (a menu, not the composer, came back)
    assert info["escape_sent"] is True and info["turn_stopped"] is False and "interrupted" not in info, info


def test_text_that_appears_after_the_stop_is_never_typed_over_and_the_answer_says_escape_went(monkeypatch):
    # Escape stops the turn (poll sees the composer), then the look before the keys finds text in it
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE, IDLE_PANE, RESTORED_PANE])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert not ok and info["recheck"] == "composer_text" and info["composer_text"] == "an earlier steer the CLI put back", info
    assert info["escape_sent"] is True and info["turn_stopped"] is True and "appeared in the composer" in info["activity"]
    assert not _typed(t) and det.marked == ["seat"]


def test_a_stale_detector_on_an_idle_screen_gets_no_escape_and_an_honest_delivered_now(monkeypatch):
    # the hook says working, the screen is an idle prompt: pre, last look, the gate (no spinner), post-Enter
    t, det = _pane_i(monkeypatch, "working", [IDLE_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and "interrupted" not in info and "queued_mid_turn" not in info, info
    assert "no running turn on screen" in info["interrupt_failed"]
    assert ("Escape",) not in _keys(t) and det.marked == [] and ("-l", "hi") in _keys(t)


def test_quoted_esc_hint_above_an_idle_prompt_gets_no_escape(monkeypatch):
    # the detector says idle, the whole-screen heuristic reads the quoted hint as a turn: the gate anchors on the glyph
    t, det = _pane_i(monkeypatch, "idle", [QUOTED_PANE, QUOTED_PANE, QUOTED_PANE, QUOTED_PANE])
    assert G._pane_shows_active_turn(QUOTED_PANE) and not G._turn_above_composer(QUOTED_PANE)
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    # something on screen reads as a turn and the anchored read cannot see a spinner: no Escape, and the
    # conservative label stays (the words may have been queued by a turn the anchored read missed)
    assert ok and "interrupted" not in info and info["queued_mid_turn"] is True, info
    assert "no spinner is drawn above the input box" in info["interrupt_failed"]
    assert ("Escape",) not in _keys(t) and det.marked == []


def test_the_measured_todo_list_layout_is_a_running_turn_and_gets_the_escape(monkeypatch):
    assert G._turn_above_composer(TODO_PANE) and G._pane_shows_active_turn(TODO_PANE)
    assert not G._turn_above_composer(TODO_IDLE) and not G._pane_shows_active_turn(TODO_IDLE)
    assert not G._SPINNER_GERUND_RE.match("✻ alpha… (51s · ↓ 2.0k tokens)"), "the todo spinner has no gerund: the timed shape must carry it"
    assert not G._is_spinner_row("● Cogitated for 2s · done 7:45 PM") and not G._is_spinner_row("● Bash(sleep 45)")
    # the time group must be followed by " ·" or ")": a reply bullet with a time in its parenthesis is not a spinner
    # ("● Retrying... (30s backoff)" IS one by the detector's own gerund+ellipsis shape, which predates this PR)
    assert not G._is_spinner_row("● Done... (30s backoff)") and not G._is_spinner_row("✻ Worked for 51s")
    assert not G._SPINNER_TIMED_RE.match("● Retrying... (30s backoff)")
    assert G._is_spinner_row("✻ alpha… (1m 4s)") and G._is_spinner_row("* Fermenting… (1h 14m 31s · ↓ 78.0k tokens)")
    assert not G._pane_shows_active_turn("● Retrying... (30s backoff)\n\n❯ \n")
    t, det = _pane_i(monkeypatch, "working", [TODO_PANE, TODO_PANE, TODO_PANE, TODO_IDLE, TODO_IDLE, TODO_IDLE])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and info["interrupted"] is True, info
    assert _keys(t)[0] == ("Escape",) and det.marked == ["seat"]


def test_a_menu_at_the_interrupts_own_look_refuses_before_any_escape(monkeypatch):
    # the recheck before the branch saw a safe pane; the interrupt's one look sees a permission menu
    for force in (False, True):
        t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, MENU_PANE])
        ok, info = G.verified_inject("seat", "hi", force=force, source="person", interrupt=True)
        assert not ok and info["recheck"] == "no_composer" and "escape_sent" not in info, (force, info)
        assert ("Escape",) not in _keys(t) and not _typed(t) and det.marked == []


def test_text_that_appears_at_the_interrupts_own_look_is_refused_without_force(monkeypatch):
    # the recheck before the branch saw a clean composer; by the interrupt's one look someone has typed: the
    # operator's text is sacred, so nothing is typed onto it and no Escape goes (with force the draft is appended)
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, DRAFT_PANE])
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert not ok and info["recheck"] == "composer_text" and info["composer_text"] == "my own half-typed draft", info
    assert "escape_sent" not in info and not _typed(t) and ("Escape",) not in _keys(t) and det.marked == []


def test_force_with_text_appearing_after_the_escape_is_refused_not_appended(monkeypatch):
    # force appends to a draft that was there; text that appears AFTER the Escape is never that draft
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE, IDLE_PANE, RESTORED_PANE])
    ok, info = G.verified_inject("seat", "hi", force=True, source="person", interrupt=True)
    assert not ok and info["recheck"] == "composer_text" and info["escape_sent"] is True and info["turn_stopped"] is True, info
    assert not _typed(t) and det.marked == ["seat"]


def test_force_with_a_draft_in_the_composer_never_sends_escape(monkeypatch):
    # force skips the typed-text look (it appends to the draft): no Escape over a draft, its effect on one is not measured
    t, det = _pane_i(monkeypatch, "working", [DRAFT_PANE, DRAFT_PANE, DRAFT_PANE, WORKING_CLEARED])
    ok, info = G.verified_inject("seat", "hi", force=True, source="person", interrupt=True)
    assert ok and info["queued_mid_turn"] is True and "interrupted" not in info, info
    assert "typed text in the composer" in info["interrupt_failed"] and "not measured" in info["interrupt_failed"]
    assert ("Escape",) not in _keys(t) and ("-l", "hi") in _keys(t) and det.marked == []


def test_force_with_an_empty_composer_still_interrupts(monkeypatch):
    t, det = _pane_i(monkeypatch, "working", [WORKING_PANE, WORKING_PANE, WORKING_PANE, IDLE_PANE, IDLE_PANE, IDLE_PANE])
    ok, info = G.verified_inject("seat", "hi", force=True, source="person", interrupt=True)
    assert ok and info["interrupted"] is True, info
    assert _keys(t)[0] == ("Escape",) and det.marked == ["seat"]


def test_a_headless_seat_has_no_pane_to_interrupt_and_says_so(monkeypatch):
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: "hs")
    monkeypatch.setattr(G, "_headless_inject", lambda seat, text, idle_only=False, source="mail":
                        (True, {"state": "headless", "attempts": 1, "headless": True, "message_id": "gw-1", "msg_state": "queued"}))
    ok, info = G.verified_inject("seat", "hi", source="person", interrupt=True)
    assert ok and "headless seat" in info["interrupt_failed"] and "interrupted" not in info, info
    ok, info = G.verified_inject("seat", "hi", source="person")
    assert ok and "interrupt_failed" not in info
    ok, info = G.verified_inject("seat", "hi", source="mail", interrupt=True)
    assert ok and "interrupt_failed" not in info


@pytest.mark.parametrize("raw,want", [("abc", "cap"), (None, "cap"), ("nan", "cap"), ("99", "cap"), ("3", "cap"), ("0.01", 0.25), ("0.5", 0.5)])
def test_the_wait_env_var_never_stops_the_boot_and_stays_within_the_lock_budget(raw, want):
    cap = G.INTERRUPT_WAIT_MAX_S
    assert G._interrupt_wait_s(raw) == (min(cap, 0.75) if want == "cap" else min(cap, want))
    # the cap is DERIVED from the lock budget: every sleep a send spends under the lock is in the sum, by name
    spent = G.RATING_DISMISS_WAIT_S + G.INJECT_INGEST_WAIT_S + G.SUBMIT_WITNESS_S + G.LOCK_OVERHEAD_S
    assert cap == max(0.25, G.PANE_LOCK_WAIT_S - spent)
    if cap > 0.25:
        assert cap + spent <= G.PANE_LOCK_WAIT_S


def test_the_sleeps_under_the_lock_are_exactly_the_names_in_the_budget_sum():
    """Every `_time.sleep(<arg>)` inside _verified_inject_locked must be one of the names the budget sums, and every
    summed name must be slept: a literal, or a NEW named sleep not in the sum, would let an interrupt send outlive
    PANE_LOCK_WAIT_S."""
    import inspect, re
    src = inspect.getsource(G._verified_inject_locked)
    args = set(re.findall(r"_time\.sleep\(([^)]*)\)", src))
    summed = {"RATING_DISMISS_WAIT_S", "INJECT_INGEST_WAIT_S", "CLEAR_WAIT_S",
              "ENTER_CHECK_WAIT_S[0] if attempt == 1 else ENTER_CHECK_WAIT_S[1]"}
    assert args == summed, f"sleeps under the lock {args} != the budget's names {summed}"
    assert G.SUBMIT_WITNESS_S == sum(G.ENTER_CHECK_WAIT_S) + G.CLEAR_WAIT_S
    assert G.INTERRUPT_WAIT_MAX_S == 0.75 or G.PANE_LOCK_WAIT_S != 8.0 or G.INJECT_INGEST_WAIT_S != 1.0


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
    assert G._interrupt_turn("seat") == (True, True) and _captures(t) == 3 and det.marked == ["seat"]
    t2, det2 = _pane_i(monkeypatch, "working", [])
    assert G._interrupt_turn("seat") == (True, False) and _captures(t2) == int(G.INTERRUPT_WAIT_S / G.INTERRUPT_POLL_S)
    assert det2.marked == [], "marked idle without seeing the stop"
    # a quoted esc hint above the composer is not a running turn: the poll anchors on the spinner glyph
    t3, _ = _pane_i(monkeypatch, "working", [QUOTED_PANE])
    assert G._interrupt_turn("seat") == (True, True) and _captures(t3) == 1


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
    assert G._interrupt_turn("seat") == (False, False) and _captures(t) == 0


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


def test_automation_and_mail_never_get_an_interrupt_and_the_refusal_names_the_caller(box, monkeypatch, caplog):
    d = _Deliver({"state": "idle"})
    monkeypatch.setattr(G, "verified_inject", d)
    with caplog.at_level(logging.INFO, logger=G.log.name):
        _post(box, {**PHONE, "interrupt": True}, ua=AGENT_UA)                                   # fleet bearer, no person signal
        _post(box, {"session": "seat", "text": "x", "source": "mail", "interrupt": True}, actor="automation:lineage")
        _post(box, {**PHONE, "interrupt": True}, actor="automation:relay")
    assert all(not c.get("interrupt") for c in d.calls), d.calls
    assert d.calls[0].get("idle_only") is True                                           # automation: idle only, as before
    refused = [r.getMessage() for r in caplog.records if "interrupt refused" in r.getMessage()]
    assert len(refused) == 3 and all("actor=" in m or "ua=" in m or "automation" in m for m in refused), refused


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


def test_a_held_answer_after_an_escape_says_the_step_was_stopped(box, monkeypatch, caplog):
    # the look after Escape found a menu: held for the operator's answer, and the person is told their agent's step stopped
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "busy", "state": "working", "recheck": "no_composer",
                                                       "escape_sent": True, "turn_stopped": False,
                                                       "activity": "No composer on screen"}))
    status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and body["held"] is True and body["outcome"] == "held", body
    assert body["escape_sent"] is True and body["turn_stopped"] is False
    # text appeared after the stop: the legacy 409 with the text, escape_sent on it, and the interrupt's OWN words
    # (not the generic composer_text line), with the caller in the log
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "busy", "state": "working", "recheck": "composer_text",
                                                       "composer_text": "restored", "escape_sent": True, "turn_stopped": True,
                                                       "activity": "Escape stopped the turn and text appeared in the composer"}))
    with caplog.at_level(logging.INFO, logger=G.log.name):
        status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 409 and body["escape_sent"] is True and body["composer_text"] == "restored" and "outcome" not in body
    assert body["activity"].startswith("Escape stopped the turn"), body
    assert any("escape_sent turn_stopped=True" in r.getMessage() and "ua=" in r.getMessage() or
               "escape_sent turn_stopped=True" in r.getMessage() and "OrchestraUltra" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]
    # a hold with no Escape carries neither key
    monkeypatch.setattr(G, "verified_inject", _refuse(BUSY_SUBTYPES["permission_dialog"]))
    status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and "escape_sent" not in body and "turn_stopped" not in body


def test_the_stop_button_presses_escape_under_the_send_lock(box, monkeypatch):
    """An interrupt send holds the per-session send lock while it presses Escape and waits; the stop button must
    queue behind it, never add a second Escape (two on an idle prompt open the CLI's previous-message picker)."""
    import threading
    t = _Tmux([])
    monkeypatch.setattr(G, "_tmux", t)
    det = _DetI("working")
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    lk = threading.Lock()
    entered, at_escape = [], []

    class _Rec:
        def __enter__(self):
            entered.append(lk.locked())
            lk.acquire()
        def __exit__(self, *a):
            lk.release()
    monkeypatch.setattr(G, "_session_send_lock", lambda s: _Rec())

    class _TmuxEsc(_Tmux):
        def __call__(self, *args, timeout=5):
            if args[0] == "send-keys" and args[-1] == "Escape":
                at_escape.append(lk.locked())                 # the lock must be HELD at the moment of the Escape
            return super().__call__(*args, timeout=timeout)
    t = _TmuxEsc([WORKING_PANE])                              # the look under the lock: a spinner is drawn
    monkeypatch.setattr(G, "_tmux", t)

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-interrupt", json={"session": "seat"}, headers=_headers(box))
            return r.status, await r.json()
    status, body = asyncio.run(go())
    assert status == 200 and body["interrupted"] is True and body["hook_cleared"] is True, body
    assert entered == [False] and at_escape == [True] and not lk.locked()
    assert det.marked == ["seat"]


QUOTED_DRAFT_PANE = "the log line said: esc to interrupt\nmore output\n❯ my own half-typed draft\n"


@pytest.mark.parametrize("screen,escapes", [
    (IDLE_PANE, False),          # nothing on screen reads as a turn: no Escape onto an idle prompt
    (TODO_IDLE, False),
    ("", False),                 # the capture failed: an unreadable screen is not a turn, no blind Escape
    (QUOTED_DRAFT_PANE, False),  # only the whole screen reads a turn AND a draft sits in the box: Escape on a draft was not measured
    (QUOTED_PANE, True),         # something reads as a turn, the anchored read sees no spinner: the stop button still works
    (TODO_PANE, True),           # the anchored spinner
    (DRAFT_PANE, True),          # the anchored spinner with a draft: the stop button's long-standing behaviour; what Escape
                                 # does to the draft is NOT measured (the send path sends no Escape over a draft for that reason)
], ids=["idle", "todo-idle", "unreadable", "whole-screen-turn-with-draft", "whole-screen-turn", "anchored-spinner", "anchored-spinner-with-draft"])
def test_the_stop_button_looks_again_under_the_lock_with_the_send_paths_three_way_read(box, monkeypatch, screen, escapes):
    # the detector still says working (the read before the lock wait); under the lock the screen decides
    t = _Tmux([screen])
    monkeypatch.setattr(G, "_tmux", t)
    det = _DetI("working")
    monkeypatch.setattr(G, "_agent_status", lambda: det)

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            r = await c.post("/agent-interrupt", json={"session": "seat"}, headers=_headers(box))
            return r.status, await r.json()
    status, body = asyncio.run(go())
    if escapes:
        assert status == 200 and body["interrupted"] is True and ("Escape",) in _keys(t) and det.marked == ["seat"], body
    else:
        assert status == 409 and body["error"] == "not mid-turn" and "no running turn on the screen" in body["detail"], body
        assert ("Escape",) not in _keys(t) and det.marked == []


def test_a_cleared_unverified_submit_after_an_escape_keeps_the_escape_facts(box, monkeypatch, caplog):
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "unverified_submit", "state": "working", "cleared": True,
                                                       "text_returned": "hi", "escape_sent": True, "turn_stopped": True}))
    with caplog.at_level(logging.INFO, logger=G.log.name):
        status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and body["held"] is True and body["cleared"] is True and body["outcome"] == "held", body
    assert body["escape_sent"] is True and body["turn_stopped"] is True
    lines = [r.getMessage() for r in caplog.records if "submit unverified after an Escape" in r.getMessage()]
    assert len(lines) == 1 and "cleared=True" in lines[0] and ("ua=" in lines[0] or "OrchestraUltra" in lines[0]), lines
    caplog.clear()
    monkeypatch.setattr(G, "verified_inject", _refuse({"reason": "unverified_submit", "state": "working", "cleared": True,
                                                       "text_returned": "hi"}))
    with caplog.at_level(logging.INFO, logger=G.log.name):
        status, body = _post(box, {**PHONE, "interrupt": True})
    assert status == 200 and "escape_sent" not in body
    assert not [r for r in caplog.records if "submit unverified after an Escape" in r.getMessage()], "logged with no Escape"


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
