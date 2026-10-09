"""FAIL CLOSED: a permission prompt whose context did not parse is never answered from a device.

Claude Code 2.1.286+ draws a permission prompt's command between dashed lines, and the parser used
to come back with NO context for it: the device card read "Do you want to proceed?" with no command,
and a touch prompt and an rm -rf prompt shared one identity. The parser is fixed
(test_menu_context_2_1_295.py); this is the belt behind it, on ANY CLI version: such a prompt is
flagged on every card and refused on every device answer path with zero keys sent. It is answered
in the terminal.
"""
import asyncio
import json

import pytest

import scripts.watch_gateway as G

SESSION = "seat"
UA = "OrchestraOS/269 CFNetwork Darwin iOS"
BLIND = {"kind": "permission", "question": "Do you want to proceed?", "context": None,
         "options": [{"n": "1", "label": "Yes"}, {"n": "2", "label": "No, and tell Claude what to do differently"}]}
SEEN = {**BLIND, "context": "Bash command\ntouch /tmp/x"}


class _Req:
    def __init__(self, payload=None):
        self.headers = {"Authorization": "Bearer fleet-tok", "User-Agent": UA}
        self._payload = payload or {}
        self.query = {}
        self.match_info = {"session": SESSION}

    async def json(self):
        return self._payload


@pytest.fixture
def screen(monkeypatch, tmp_path):
    state = {"menu": BLIND, "sent": []}

    class _AS:
        def get_agent_status(self, session):
            return {"pending_menu": state["menu"], "state": "waiting"}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("PERM_RESPOND_ARMED", "1")
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-tok")
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: [SESSION])
    monkeypatch.setattr(G, "_is_gemini_session", lambda s: False)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)

    class _R:
        returncode = 0

    monkeypatch.setattr(G, "_tmux", lambda *a: state["sent"].append(a) or _R())
    monkeypatch.setattr(G, "_SERVED", G._OrderedDict())
    return state


def _serve(menu, now=100.0):
    n, d = G._stamp_instance(SESSION, menu, now=now)
    G._note_served(_Req(), SESSION, f"{d}:{n}")


def _post(payload):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "confirm": True, **payload})))
    return resp.status, json.loads(resp.text)


def test_a_digit_on_a_contextless_permission_prompt_is_refused_even_after_it_was_served(screen):
    _serve(BLIND)                                     # the device WAS shown it: still refused
    status, body = _post({"key": "1"})
    assert status == 409 and body["reason"] == "answer_in_terminal"
    assert "terminal" in body["error"]
    assert screen["sent"] == [], "zero keys reached the pane"


def test_a_typed_respond_on_a_contextless_permission_prompt_is_refused(screen):
    _serve(BLIND)
    status, body = _post({"key": "2", "answer": "respond", "text": "do X instead"})
    assert status == 409 and body["reason"] == "answer_in_terminal" and screen["sent"] == []


def test_control_the_same_prompt_WITH_its_command_is_answerable(screen):
    screen["menu"] = SEEN
    _serve(SEEN)
    status, body = _post({"key": "1"})
    assert status == 200 and screen["sent"], body


def test_an_empty_or_blank_context_counts_as_none(screen):
    for ctx in ("", "   \n "):
        screen["menu"] = {**BLIND, "context": ctx}
        _serve(screen["menu"])
        assert _post({"key": "1"})[1]["reason"] == "answer_in_terminal"
    assert screen["sent"] == []


def test_a_non_permission_menu_with_no_context_is_not_affected(screen):
    assert G._contextless_permission({"kind": "options", "question": "Which?", "context": None}) is False


def test_the_card_and_the_screen_both_say_answer_in_the_terminal(screen, monkeypatch):
    row = G._perm_pseudo_row(SESSION, BLIND)
    assert row["answer_in_terminal"] is True and row["menu"]["answer_in_terminal"] is True
    assert "terminal" in row["answer_in_terminal_reason"]
    assert "answer_in_terminal" not in G._perm_pseudo_row(SESSION, SEEN)
