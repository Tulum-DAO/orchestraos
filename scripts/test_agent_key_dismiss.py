"""Dismiss: refuse to answer an agent's own menu. It presses Escape, which is what the Dev terminal does, and
only on the menu the card showed: an Escape at any other moment would interrupt a working turn.

Also here: both menu answers (Escape and a digit) take the cross-process pane lock, and each answer is recorded
so the conversation keeps a line saying what was asked and what was chosen."""
import asyncio
import json
import os
from pathlib import Path

import pytest

import scripts.watch_gateway as G
from scripts.test_agent_key_could_have_seen import (  # noqa: F401 — the shared fixture and helpers
    CMD_A, CMD_B, SESSION, _Req, _serve, _show, _tap, screen)
from scripts import pane_lock


@pytest.fixture
def store(monkeypatch):
    calls = []

    class _Store:
        def migrate(self):
            pass

        def resolve_pending_menus_for_session(self, session):
            calls.append(session)
            return 1
    monkeypatch.setattr(G, "ApprovalStore", _Store)
    return calls


def _dismiss(confirm=True):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "answer": "dismiss", "confirm": confirm})))
    return resp.status, json.loads(resp.text)


def test_dismiss_presses_escape_on_the_menu_that_was_shown(screen, store):
    _serve(CMD_A, now=100.0)
    status, body = _dismiss()
    assert status == 200 and body == {"ok": True, "dismissed": True}
    assert screen["sent"] == [("send-keys", "-t", G._st(SESSION), "Escape")]
    assert store == [SESSION]                     # its mirror card clears at once


def test_dismiss_is_two_phase(screen, store):
    _serve(CMD_A, now=100.0)
    status, body = _dismiss(confirm=False)
    assert status == 428 and body["needs_confirm"] and screen["sent"] == []


def test_no_menu_on_screen_sends_nothing(screen, store):
    screen["menu"] = None
    status, body = _dismiss()
    assert status == 409 and body["reason"] == "menu_gone" and screen["sent"] == []


def test_a_prompt_the_device_was_never_shown_is_not_dismissed(screen, store):
    _show(CMD_A, now=100.0)                       # another surface showed it; this device never saw it
    status, _ = _dismiss()
    assert status == 409 and screen["sent"] == []


def test_a_menu_replaced_under_the_lock_is_left_alone(screen, store, monkeypatch):
    _serve(CMD_A, now=100.0)
    reads = iter([CMD_A, CMD_B])

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": next(reads), "state": "waiting"}
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    status, body = _dismiss()
    assert status == 409 and body["reason"] == "instance_mismatch" and screen["sent"] == []
    assert store == []


def _answers():
    p = Path(os.environ["MENU_ANSWER_LOG"])
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_a_dismiss_is_recorded_for_the_conversation(screen, store):
    _serve(CMD_A, now=100.0)
    _dismiss()
    [row] = _answers()
    assert row["session"] == SESSION and row["dismissed"] is True
    assert row["question"] == CMD_A["question"] and row["option_n"] is None


def test_a_digit_answer_is_recorded_with_its_label(screen):
    _serve(CMD_A, now=100.0)
    status, _ = _tap(key="2")
    assert status == 200
    [row] = _answers()
    assert row["option_n"] == "2" and row["label"] == "No" and row["dismissed"] is False


def test_a_refused_answer_records_nothing(screen, store):
    screen["menu"] = None
    _dismiss()
    assert _answers() == []


def test_dismiss_waits_for_the_pane_lock_and_sends_nothing_while_another_writer_holds_it(screen, store, monkeypatch):
    monkeypatch.setattr(G, "PANE_LOCK_WAIT_S", 0.1)
    _serve(CMD_A, now=100.0)
    with pane_lock.pane_lock(SESSION, wait_s=0):          # the router (another process) is typing
        status, body = _dismiss()
    assert status == 409 and body["reason"] == "busy" and screen["sent"] == []
    assert store == [] and _answers() == []


def test_a_digit_waits_for_the_pane_lock_too(screen, monkeypatch):
    monkeypatch.setattr(G, "PANE_LOCK_WAIT_S", 0.1)
    _serve(CMD_A, now=100.0)
    with pane_lock.pane_lock(SESSION, wait_s=0):
        status, body = _tap(key="1")
    assert status == 409 and body["reason"] == "busy" and screen["sent"] == []


def test_both_locks_are_released_after_an_answer(screen, store):
    _serve(CMD_A, now=100.0)
    _dismiss()
    with pane_lock.pane_lock(SESSION, wait_s=0):          # free again at once
        pass
    assert G._session_send_lock(SESSION).acquire(blocking=False)
    G._session_send_lock(SESSION).release()
