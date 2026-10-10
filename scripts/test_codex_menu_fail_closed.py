"""FAIL CLOSED: a Codex menu is never answered from a device (gm msg_325ca9bf, PR 2).

Codex draws its menu cursor as '›' (U+203A). The detector only knew Claude's '❯' and agy's '>', so a
Codex menu produced no pending_menu at all, and the gateway's fallback imported a codex_menu_parser
module this repo never shipped, inside a bare `except: pass`: it silently did nothing. Codex seats run
`--yolo`, so they raise no command-approval prompts; the menus they can still meet have no answer
contract proven on the real CLI. So: the detector now SEES a codex menu (real captures below), marks it
answer-in-terminal, the bridge makes no card, and every gateway answer path refuses with zero keys.

Real captures (codex-cli 0.153.4, gate container): fixtures/codex/trust_prompt_0.153.4.pane.txt,
fixtures/codex/update_prompt_0.153.4.pane.txt.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

import scripts.watch_gateway as G
from scripts import menu_bridge_core as MB

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("agent_status_codex_menu", HERE / "agent-status.py")
AS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AS)


def lines(name):
    return (HERE / "fixtures" / "codex" / f"{name}_0.153.4.pane.txt").read_text().split("\n")


# --- the detector ---------------------------------------------------------------------------

@pytest.mark.parametrize("fx,labels,kind", [
    ("trust_prompt", ["Yes, continue", "No, quit"], "yes_no"),
    ("update_prompt", ["Update now (runs `npm install -g @openai/codex`)", "Skip", "Skip until next version"], "options"),
])
def test_a_real_codex_menu_is_seen_and_marked_answer_in_terminal(fx, labels, kind):
    m = AS.parse_pending_menu(lines(fx), now=0, runtime="codex")
    assert m is not None, "a codex menu must be visible on the surfaces, not silently absent"
    assert [o["label"] for o in m["options"]] == labels and m["kind"] == kind
    assert m["selected_n"] == "1"
    assert m["menu_family"] == "codex" and m["answer_in_terminal"] is True


@pytest.mark.parametrize("fx", ["trust_prompt", "update_prompt"])
@pytest.mark.parametrize("rt", [None, "claude", "gemini"])
def test_other_runtimes_parse_exactly_as_before(fx, rt):
    """The '›' normalisation is codex-only: on any other runtime these screens parse as they did."""
    assert AS.parse_pending_menu(lines(fx), now=0, runtime=rt) is None


def test_parse_status_hands_the_runtime_to_the_menu_parser():
    raw = "\n".join(lines("trust_prompt"))
    assert AS.parse_status(raw)["pending_menu"] is None
    m = AS.parse_status(raw, runtime="codex")["pending_menu"]
    assert m and m["menu_family"] == "codex"


# --- the bridge -----------------------------------------------------------------------------

def test_the_bridge_never_makes_a_card_from_a_codex_menu():
    m = AS.parse_pending_menu(lines("update_prompt"), now=0, runtime="codex")
    assert MB.should_bridge("idle", m, first_seen_ts=0, now=60) is False


def test_control_a_claude_options_menu_still_bridges():
    m = {"kind": "options", "question": "Which?", "options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]}
    assert MB.should_bridge("idle", m, first_seen_ts=0, now=60) is True


# --- the gateway ----------------------------------------------------------------------------

SESSION = "cx"
UA = "OrchestraOS/269 CFNetwork Darwin iOS"


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
    state = {"menu": AS.parse_pending_menu(lines("update_prompt"), now=0, runtime="codex"), "sent": []}

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
    monkeypatch.setattr(G, "_is_codex_session", lambda s: True)
    monkeypatch.setattr(G, "_protected_refusal", lambda request, session: None)

    class _R:
        returncode = 0

    monkeypatch.setattr(G, "_tmux", lambda *a: state["sent"].append(a) or _R())
    monkeypatch.setattr(G, "_SERVED", G._OrderedDict())
    return state


def _post(payload):
    resp = asyncio.run(G.handle_agent_key(_Req({"session": SESSION, "confirm": True, **payload})))
    return resp.status, json.loads(resp.text)


def test_a_digit_on_a_codex_menu_is_refused_with_zero_keys(screen):
    status, body = _post({"key": "1"})          # "1" here is "Update now": the worst possible key
    assert status == 409 and body["reason"] == "answer_in_terminal"
    assert "Codex" in body["error"] and "terminal" in body["error"]
    assert screen["sent"] == []


def test_the_ledger_answer_paths_refuse_a_codex_menu_with_zero_keys(screen):
    ok, info = G.menu_resume_keypress(SESSION, "1")
    assert ok is False and info["reason"] == "answer_in_terminal"
    ok, info = G.menu_resume_free_text(SESSION, "1", "anything")
    assert ok is False and info["reason"] == "answer_in_terminal"
    assert screen["sent"] == []


@pytest.mark.parametrize("fx", ["trust_prompt", "update_prompt"])
def test_an_armed_batch_submit_refuses_a_codex_menu_with_zero_keys(fx):
    """/agent-key submit -> durable_first_batch_submit -> menu_batch_submit(armed=True) whenever a
    pending row exists for the session; a STALE row (written before the seat ran codex, or by a
    non-bridge writer) would otherwise be replayed as digits onto the codex screen, with only the
    per-part question match in the way (orchestraos-builder review of #397). The row here even
    carries the codex screen's own question, so that match would pass."""
    menu = AS.parse_pending_menu(lines(fx), now=0, runtime="codex")
    sent = []
    ok, info = G.menu_batch_submit(
        SESSION, answers=[{"part": 0, "ns": ["1"]}], armed=True,
        read_fn=lambda: menu, key_fn=lambda k: sent.append(("key", k)) or True,
        type_fn=lambda t: sent.append(("type", t)) or True, settle_s=0,
        expect_questions={0: menu.get("question")})
    assert ok is False and info["reason"] == "answer_in_terminal"
    assert sent == []


@pytest.mark.parametrize("fx", ["trust_prompt", "update_prompt"])
def test_an_armed_single_submit_refuses_a_codex_menu_with_zero_keys(fx):
    menu = AS.parse_pending_menu(lines(fx), now=0, runtime="codex")
    sent = []
    ok, info = G.menu_submit(SESSION, dry_run=False, read_fn=lambda: menu,
                             key_fn=lambda k: sent.append(k) or True, settle_s=0)
    assert ok is False and info["reason"] == "answer_in_terminal"
    assert sent == []


def test_control_a_claude_menu_still_reaches_the_batch_replay():
    """The codex refusal must not catch a claude menu: the same call on a claude-family menu goes on
    to the AUQ replay (here it presses, so the refusal is codex-only, not a blanket stop)."""
    menu = {"kind": "options", "question": "Which?", "context": None, "part_count": 1,
            "options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]}
    sent = []
    ok, info = G.menu_batch_submit(
        SESSION, answers=[{"part": 0, "ns": ["1"]}], armed=True, read_fn=lambda: menu,
        key_fn=lambda k: sent.append(k) or True, type_fn=lambda t: True, settle_s=0)
    assert info.get("reason") != "answer_in_terminal"
    assert sent, "a claude menu must still be replayed"


def test_every_codex_menu_kind_is_terminal_only():
    for fx in ("trust_prompt", "update_prompt"):
        m = AS.parse_pending_menu(lines(fx), now=0, runtime="codex")
        assert G._terminal_only(m) is True
        assert "Codex" in G._terminal_only_reason(m)


def test_control_a_claude_menu_is_not_terminal_only():
    assert G._terminal_only({"kind": "options", "question": "Which?", "context": None,
                             "options": [{"n": "1", "label": "A"}]}) is False


def test_the_gateway_no_longer_imports_a_module_it_never_shipped():
    src = (HERE / "watch_gateway.py").read_text()
    assert "codex_menu_parser" not in src.replace("imported a codex_menu_parser module", "")
