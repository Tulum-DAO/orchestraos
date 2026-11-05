"""A headless seat's questions and permissions as 4c cards (scripts/headless_cards.py), answered EXACTLY ONCE: the
answer goes back only as the held request's control response, never as an approval_resolved inbox row, an inject
or a keypress. Scratch approval ledger, a fake supervisor client; nothing touches the live ledger, tmux or a seat."""
import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import headless_cards as HC  # noqa: E402
import headless_switch as HS  # noqa: E402
import approval_resume as R  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

SEAT, SESSION = "dev-demo", "dev-demo"
ASK_ONE = {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [
    {"question": "Which size?", "header": "Size", "multiSelect": False,
     "options": [{"label": "Small", "description": "Fits a phone"}, {"label": "Large"}]}]}}
ASK_TWO = {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [
    {"question": "Which size?", "header": "Size", "options": [{"label": "Small"}, {"label": "Large"}]},
    {"question": "Which colours?", "header": "Colour", "multiSelect": True,
     "options": [{"label": "Blue"}, {"label": "Green"}, {"label": "Red"}]}]}}
PERM = {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls /tmp/sample"}}


class FakeSupervisor:
    """headless_switch's client, answering `resolve` the way the supervisor does: once per request."""
    SupervisorDown = HS.SupervisorDown

    def __init__(self, held=("r1",), down=False):
        self.held, self.down, self.calls, self.resolved = set(held), down, [], {}

    def request(self, seat, payload, **kw):
        if self.down:
            raise HS.SupervisorDown("no socket")
        self.calls.append((seat, payload))
        rid = payload.get("request_id")
        if rid in self.resolved:
            return {"ok": True, "already": True}
        if rid not in self.held:
            return {"ok": False, "reason": "no-such-request"}
        self.held.discard(rid)
        self.resolved[rid] = payload
        return {"ok": True, "already": False}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    s = ApprovalStore(db_path=str(tmp_path / "t.db"))
    s.migrate()
    sent, injected, pressed = [], [], []

    class NoInbox:
        def send(self, **kw):
            sent.append(kw)
            return "msg_x"
    monkeypatch.setattr(R, "_msg_store", lambda: NoInbox())
    monkeypatch.setattr(R, "_REAL_MSG_STORE_FACTORY", lambda: NoInbox())
    monkeypatch.setattr(R, "_default_inject", lambda sess, text: injected.append((sess, text)) or (True, {}))
    monkeypatch.setattr(R, "_default_resolve", lambda a: (a, "live"))
    monkeypatch.setenv("ANSWER_TELEMETRY_PATH", str(tmp_path / "telemetry.jsonl"))
    t = types.SimpleNamespace(menu_resume_keypress=lambda *a, **k: pressed.append(a) or (True, {}),
                              menu_resume_free_text=lambda *a, **k: pressed.append(a) or (True, {}))
    monkeypatch.setattr(R, "_menu_transport", lambda: t)
    sup = FakeSupervisor()
    monkeypatch.setattr(HS, "request", sup.request)
    return types.SimpleNamespace(store=s, sent=sent, injected=injected, pressed=pressed, sup=sup)


def _file(ledger, request, rid="r1"):
    return HC.file_card(SEAT, SESSION, rid, request, store=ledger.store, notify=lambda *a, **k: None)


@pytest.fixture
def G(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("watch_gateway_hlcards", HERE / "watch_gateway.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


# --- the card ----------------------------------------------------------------------------------------

def test_one_question_is_a_plain_menu_card():
    p = HC.card_payload(SEAT, SESSION, "r1", ASK_ONE)
    m = p["menu"]
    assert p["question"] == "Which size?" and p["op_key"].startswith(f"menu:{SESSION}:hl-")
    assert [o["label"] for o in m["options"]] == ["Small", "Large", HC.FREE_TEXT_LABEL]
    assert m["options"][0]["detail"] == "Fits a phone" and m["options"][2]["input_kind"] == "free_text"
    assert "parts" not in m and m["bridge"]["request_id"] == "r1" and m["bridge"]["kind"] == "headless"


def test_several_questions_are_a_multipart_card():
    m = HC.card_payload(SEAT, SESSION, "r1", ASK_TWO)["menu"]
    assert m["multipart"] and m["walk_complete"] and m["part_count"] == 2
    assert [p["select"] for p in m["parts"]] == ["single", "multi"]
    assert m["tabs"] == ["Size", "Colour", "Submit"] and m["submit_tab_index"] == 2
    assert m["bridge"]["questions"] == ["Which size?", "Which colours?"]


def test_a_permission_is_allow_deny_card():
    p = HC.card_payload(SEAT, SESSION, "r1", PERM)
    assert p["question"] == "Allow Bash? ls /tmp/sample"
    assert [o["label"] for o in p["menu"]["options"]] == ["Allow", "Deny", HC.DENY_WITH_REASON]
    assert p["menu"]["bridge"]["permission"] is True


def test_response_for_the_control_request():
    r = HC.response_for(ASK_ONE, {"kind": "question", "answers": {"Which size?": "Large"}})
    assert r == {"behavior": "allow", "updatedInput": {**ASK_ONE["input"], "answers": {"Which size?": "Large"}}}
    assert HC.response_for(PERM, {"kind": "permission", "allow": True}) == {
        "behavior": "allow", "updatedInput": PERM["input"]}
    d = HC.response_for(PERM, {"kind": "permission", "allow": False, "message": "No."})
    assert d == {"behavior": "deny", "message": "No."}


def test_filing_is_once_per_request_and_findable_by_session(ledger):
    rid = _file(ledger, ASK_TWO)
    assert _file(ledger, ASK_TWO) == rid
    row = ledger.store.get(rid)
    assert row["origin"] == "headless" and row["kind"] == "menu" and row["status"] == "pending"
    assert ledger.store.pending_menu_row_for_session(SESSION)["id"] == rid


# --- exactly once -----------------------------------------------------------------------------------

def test_a_single_answer_reaches_the_agent_once_and_only_as_the_control_response(ledger, G):
    rid = _file(ledger, ASK_ONE)
    out = G.apply_answer(ledger.store, rid, "option", option_n="2", fire=True, surface="web")
    assert out["ok"] and out["applied"]
    assert len(ledger.sup.calls) == 1
    seat, payload = ledger.sup.calls[0]
    assert seat == SEAT and payload["cmd"] == "resolve" and payload["request_id"] == "r1"
    assert payload["outcome"] == {"kind": "question", "answers": {"Which size?": "Large"}}
    assert ledger.sent == [] and ledger.injected == [] and ledger.pressed == []   # no inbox, inject or keys
    assert ledger.store.get(rid)["status"] == "resumed"
    # A watchdog beat or a second surface: the card is no longer 'answered', nothing is sent again.
    R.fire_resume(ledger.store.get(rid), ledger.store)
    assert G.apply_answer(ledger.store, rid, "option", option_n="1", fire=True)["applied"] is False
    assert len(ledger.sup.calls) == 1 and ledger.sent == []


def test_own_words_on_a_question(ledger, G):
    rid = _file(ledger, ASK_ONE)
    G.apply_answer(ledger.store, rid, "option", option_n="3", answer_text="Medium, please", fire=True)
    assert ledger.sup.calls[0][1]["outcome"]["answers"] == {"Which size?": "Medium, please"}


def test_a_permission_allow_and_deny(ledger, G):
    rid = _file(ledger, PERM)
    G.apply_answer(ledger.store, rid, "option", option_n="1", fire=True)
    assert ledger.sup.calls[-1][1]["outcome"] == {"kind": "permission", "allow": True}
    ledger.sup.held.add("r2")
    rid2 = _file(ledger, PERM, rid="r2")
    G.apply_answer(ledger.store, rid2, "option", option_n="3", answer_text="use the sample folder", fire=True)
    o = ledger.sup.calls[-1][1]["outcome"]
    assert o["allow"] is False and "use the sample folder" in o["message"]
    assert ledger.sent == [] and ledger.injected == []


def test_a_multipart_answer_is_persisted_and_delivered_once_armed_or_not(ledger, G):
    rid = _file(ledger, ASK_TWO)
    boom = lambda *a, **k: pytest.fail("a pane replay was attempted for a headless card")
    answers = [{"part": 0, "ns": ["2"]}, {"part": 1, "ns": ["1", "3"]}]
    ok, info = G.durable_first_batch_submit(SESSION, answers, store=ledger.store, armed=False,
                                            submit_fn=boom, resume_fn=boom, expect_row_id=rid)
    assert ok and info["delivered"] and info["headless"]
    assert ledger.sup.calls[0][1]["outcome"]["answers"] == {"Which size?": "Large", "Which colours?": "Blue, Red"}
    assert ledger.store.get(rid)["status"] == "resumed" and ledger.sent == []
    # The device retries the same submit: already durable, nothing sent again.
    ok, info = G.durable_first_batch_submit(SESSION, answers, store=ledger.store, armed=True,
                                            submit_fn=boom, resume_fn=boom, expect_row_id=rid)
    assert ok and info.get("already") and len(ledger.sup.calls) == 1


def test_a_multipart_batch_is_validated_first(ledger, G):
    rid = _file(ledger, ASK_TWO)
    ok, info = G.durable_first_batch_submit(SESSION, [{"part": 0, "ns": ["2"]}], store=ledger.store,
                                            armed=True, expect_row_id=rid)
    assert not ok and info["reason"] == "batch_validation" and ledger.sup.calls == []
    assert ledger.store.get(rid)["status"] == "pending"


def test_the_question_ended_before_the_answer(ledger, G):
    ledger.sup.held.clear()                       # the process that asked is gone
    rid = _file(ledger, ASK_ONE)
    G.apply_answer(ledger.store, rid, "option", option_n="1", fire=True)
    row = ledger.store.get(rid)
    assert row["status"] == "resume_failed" and "ended" in row["resume_error"]
    assert ledger.sent == [] and ledger.injected == []


def test_a_supervisor_that_does_not_answer_leaves_it_for_the_watchdog(ledger, G):
    ledger.sup.down = True
    rid = _file(ledger, ASK_ONE)
    G.apply_answer(ledger.store, rid, "option", option_n="1", fire=True)
    assert ledger.store.get(rid)["status"] == "answered" and ledger.sent == []
    ledger.sup.down = False
    R.fire_resume(ledger.store.get(rid), ledger.store)          # the watchdog's next beat
    assert ledger.store.get(rid)["status"] == "resumed" and len(ledger.sup.calls) == 1


def test_a_dismissed_card_is_a_deny_not_an_inbox_notice(ledger):
    rid = _file(ledger, ASK_ONE)
    ledger.store.mark_discarded(rid)
    out = R.deliver_discarded(ledger.store.get(rid), ledger.store)
    assert out["delivered"] and ledger.sent == []
    o = ledger.sup.calls[0][1]["outcome"]
    assert o["allow"] is False and "dismissed" in o["message"]


def test_withdraw_and_withdraw_seat(ledger):
    a = _file(ledger, ASK_ONE, rid="r1")
    b = _file(ledger, PERM, rid="r9")
    assert HC.withdraw(a, store=ledger.store) and ledger.store.get(a)["status"] == "resolved_elsewhere"
    assert HC.withdraw(a, store=ledger.store) is False                   # only a pending card
    assert HC.withdraw_seat(SEAT, SESSION, store=ledger.store) == [b]


# --- keypresses retired for headless seats -----------------------------------------------------------

def test_no_keypress_into_a_headless_pane_for_an_old_menu_card(ledger, monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    (tmp_path / "state").mkdir(exist_ok=True)
    HS.set_switch(SEAT, True, runtime="claude", orch_dir=tmp_path)
    rid = ledger.store.create(from_agent=SEAT, question="Old menu?", worker_kind="pane", kind="menu",
                              menu={"question": "Old menu?", "source_session": SESSION,
                                    "options": [{"n": "1", "label": "Yes", "input_kind": "direct"}]},
                              options=["Yes"], op_key=f"menu:{SESSION}:old")
    ledger.store.record_answer(rid, "option", None, option_n="1")
    R._fire_menu_resume(ledger.store.get(rid), ledger.store, live_fn=lambda: {SESSION})
    assert ledger.pressed == []                                  # the digest goes through the supervisor
    assert len(ledger.injected) == 1


def test_menu_bridge_scan_skips_headless_seats(monkeypatch):
    spec = importlib.util.spec_from_file_location("menu_bridge_hl", HERE / "menu_bridge.py")
    mb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mb)
    asked = []
    fake_status = types.SimpleNamespace(get_agent_status=lambda s: asked.append(s) or {"state": "idle"})
    import importlib as _il
    monkeypatch.setattr(_il, "import_module", lambda name: fake_status)
    monkeypatch.setattr(mb.subprocess if hasattr(mb, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="dev-demo\npm-demo\n", returncode=0))
    monkeypatch.setattr(mb, "registry_scoped_sessions", lambda s, registry_path=None: s)
    monkeypatch.setattr(mb, "_headless", lambda s: s == "dev-demo")
    fleet = mb.collect_live()
    assert asked == ["pm-demo"] and "dev-demo" not in fleet


def test_agent_key_refuses_a_headless_seat(G, monkeypatch):
    monkeypatch.setattr(G, "_authorized", lambda r: True)
    monkeypatch.setattr(G, "_protected_refusal", lambda r, s: None)
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: "dev-demo")
    monkeypatch.setattr(G, "_tmux", lambda *a, **k: pytest.fail("keys sent to a headless pane"))

    class Req:
        headers = {}

        async def json(self):
            return {"session": "dev-demo", "key": "1", "confirm": True}
    resp = asyncio.run(G.handle_agent_key(Req()))
    assert resp.status == 409 and json.loads(resp.text)["reason"] == "headless"
