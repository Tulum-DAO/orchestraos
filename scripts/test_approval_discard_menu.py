"""The card's ✕ on an agent's own written menu (no live pane) reaches the gateway's
POST /approvals/{id}/discard: the card goes to history as 'discarded', the asking agent gets one
"don't keep waiting" note, and nothing is typed into any pane. A card already answered is left alone.
All seams are faked; nothing here touches a live pane or the real store."""
import os, sys, json, asyncio, types, pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore
import approval_resume
import watch_gateway as G

_MENU = {
    "question": "Which build should ship?",
    "options": [
        {"n": "1", "label": "Example A", "input_kind": "direct"},
        {"n": "2", "label": "Example B", "input_kind": "direct"},
    ],
    "source_session": None,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ApprovalStore(db_path=str(tmp_path / "t.db"))
    store.migrate()
    sent = []

    class _Msgs:
        def send(self, **kw):
            sent.append(kw)

    monkeypatch.setattr(G, "ApprovalStore", lambda *a, **k: store)
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    monkeypatch.setattr(approval_resume, "_msg_store", lambda: _Msgs())

    def no_pane(*a, **k):
        raise AssertionError("a dismiss must never type into a pane")
    monkeypatch.setattr(approval_resume, "_default_inject", no_pane)
    return store, sent


def _discard(rid):
    req = types.SimpleNamespace(match_info={"id": rid})
    resp = asyncio.run(G.handle_approval_discard(req))
    return resp.status, json.loads(resp.body)


def _menu_row(store):
    return store.create(from_agent="pm-demo", question=_MENU["question"], worker_kind="pane",
                        kind="menu", menu=_MENU, options=[o["label"] for o in _MENU["options"]])


def test_pending_written_menu_is_discarded_and_the_agent_told(env):
    store, sent = env
    rid = _menu_row(store)
    status, body = _discard(rid)
    assert status == 200 and body["applied"] is True
    assert store.get(rid)["status"] == "discarded"
    assert len(sent) == 1
    assert sent[0]["to_agent"] == "pm-demo"
    assert sent[0]["type"] == "approval_discarded"
    assert "Do not keep waiting" in sent[0]["body"]


def test_answered_menu_is_left_alone(env):
    store, sent = env
    rid = _menu_row(store)
    store.record_answer(rid, "option", None, option_n="1")
    status, body = _discard(rid)
    assert status == 200 and body["applied"] is False
    assert store.get(rid)["status"] == "answered"
    assert sent == []


def test_unknown_card_is_404(env):
    status, _ = _discard("apr_missing_demo")
    assert status == 404
