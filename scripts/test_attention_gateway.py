"""GET /attention and POST /attention/seen on a scratch gateway."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attention_store as S  # noqa: E402
import scripts.watch_gateway as G  # noqa: E402
from attention_testbox import Box  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


@pytest.fixture
def unarmed(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch, armed=False)


def test_the_routes_are_declared_with_their_scopes():
    assert G.ROUTE_SCOPES[("GET", "/attention")] == "read"
    assert G.ROUTE_SCOPES[("POST", "/attention/seen")] == "read"     # the card accept inside is checked against approve


def test_attention_lists_waiting_then_updates_with_counts(box):
    box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    box.astore.add_done("gm", "Shipped picker", key="k1")
    st, d = box.call("GET", "/attention")
    assert st == 200 and d["ok"] and d["person"] == "alex" and d["rev"] >= 1 and d["generated_at"]
    assert [i["tab"] for i in d["items"]] == ["waiting", "update"]
    assert d["counts"] == {"waiting": 1, "waiting_blocking": 1, "updates_unread": 1}
    w = d["items"][0]
    assert w["card"]["question"] == "Ship?" and w["push"]["decision"] == "push"


def test_waiting_card_is_the_pending_feed_row(box):
    box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane", risk_level="high")
    _, pend = box.call("GET", "/pending-approvals")
    _, d = box.call("GET", "/attention", params={"tab": "waiting"})
    a, b = dict(d["items"][0]["card"]), dict(pend["pending"][0])
    a.pop("priority_score"); b.pop("priority_score")
    assert a == b and d["items"][0]["risk"] == "high"


def test_completion_cards_are_updates_not_waiting(box):
    card = box.ledger.create(from_agent="gm", question="Done X", worker_kind="pane",
                             options=["accept", "respond"], kind="completion", evidence={})
    S.mirror_completions(box.astore, box.ledger)
    _, d = box.call("GET", "/attention")
    assert [(i["tab"], i["kind"]) for i in d["items"]] == [("update", "done")]
    assert [a["type"] for a in d["items"][0]["actions"]] == ["seen", "respond"]
    assert d["items"][0]["source_id"] == card


def test_answered_tab_carries_who_and_where(box):
    rid = box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    box.ledger.record_answer(rid, "approve", None, surface="watch", answered_by="operator")
    _, d = box.call("GET", "/attention", params={"tab": "answered"})
    it = d["items"][0]
    assert (it["answered_by"], it["answer_surface"], it["state"]) == ("alex", "watch", "answered")
    assert it["push"] is None and it["answered_at"]


def test_answered_merges_ledger_and_questionnaires_newest_first(box):
    rid = box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    box.ledger.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    qid = box.qstore.create(from_agent="gm", title="Prefs", questions=[{"prompt": "C?", "kind": "free_text"}])
    box.qstore.submit(qid, {1: {"answer_text": "blue"}})
    c = sqlite3.connect(box.db)
    c.execute("UPDATE approval_requests SET answered_at='2026-11-04T10:00:00+00:00' WHERE id=?", [rid])
    c.execute("UPDATE questionnaires SET submitted_at='2026-11-04T11:00:00+00:00' WHERE id=?", [qid])
    c.commit(); c.close()
    _, d = box.call("GET", "/attention", params={"tab": "answered"})
    assert [i["id"] for i in d["items"]] == [qid, rid]


def test_agents_filter_applies_before_limits_and_counts(box):
    for i in range(3):
        box.astore.add_done("pm", f"pm {i}", key=f"p{i}")
    box.astore.add_done("gm", "gm note", key="g")
    box.ledger.create(from_agent="pm", question="pm ask", worker_kind="pane")
    _, d = box.call("GET", "/attention", params={"agents": "gm", "limit_updates": "1"})
    assert [i["agent"] for i in d["items"]] == ["gm"]
    assert d["counts"] == {"waiting": 0, "waiting_blocking": 0, "updates_unread": 1}


def test_unread_count_is_true_when_larger_than_the_page(box):
    for i in range(5):
        box.astore.add_done("gm", f"n{i}", key=f"k{i}")
    _, d = box.call("GET", "/attention", params={"tab": "update", "limit_updates": "2"})
    assert len(d["items"]) == 2 and d["counts"]["updates_unread"] == 5


def test_seen_marks_once_and_accepts_a_linked_card_only_with_approve(box):
    card = box.ledger.create(from_agent="gm", question="Done X", worker_kind="pane",
                             options=["accept", "respond"], kind="completion", evidence={})
    ev = S.mirror_completions(box.astore, box.ledger)[0]["id"]
    st, d = box.call("POST", "/attention/seen", who="read", body={"ids": [ev], "surface": "phone"})
    assert st == 200 and d["accepted_cards"] == [] and box.ledger.get(card)["status"] == "pending"
    assert d["seen"] == 1
    st, d = box.call("POST", "/attention/seen", who="approve", body={"ids": [ev], "surface": "phone"})
    assert d["accepted_cards"] == [card]
    row = box.ledger.get(card)
    assert row["status"] in ("answered", "resumed") and row["answer_surface"] == "phone"
    assert row["answer_device"].endswith(":Phone")


def test_seen_validates_every_id_and_bad_surface_is_null(box):
    rid = box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    ev = box.astore.add_done("gm", "x", key="k")
    st, d = box.call("POST", "/attention/seen", body={"ids": [rid, ev, "evt_nope", "apr_nope", "qnr_nope",
                                                              "perm:gm:x:1", "junk"], "surface": "fridge"})
    assert st == 200 and d["seen"] == 2
    assert {s["id"] for s in d["skipped"]} == {"evt_nope", "apr_nope", "qnr_nope", "perm:gm:x:1", "junk"}
    assert all(s["why"] == "unknown_id" for s in d["skipped"])
    assert box.astore.seen_map("alex", [rid])[rid][1] is None


def test_seen_on_a_live_permission_prompt(box):
    box.perm_rows = [{"id": "perm:gm:abc:1", "kind": "permission", "from_agent": "gm", "session": "gm",
                      "question": "Allow?", "created_at": "2026-11-04T10:00:00+00:00", "menu": {"options": []}}]
    _, d = box.call("POST", "/attention/seen", body={"ids": ["perm:gm:abc:1"], "surface": "watch"})
    assert d["seen"] == 1


def test_a_failed_accept_leaves_the_item_unread(box, monkeypatch):
    box.ledger.create(from_agent="gm", question="Done X", worker_kind="pane",
                      options=["accept", "respond"], kind="completion", evidence={})
    ev = S.mirror_completions(box.astore, box.ledger)[0]["id"]
    monkeypatch.setattr(G, "apply_answer", lambda *a, **k: {"ok": False, "status": 500, "error": "boom"})
    _, d = box.call("POST", "/attention/seen", body={"ids": [ev], "surface": "web"})
    assert d["seen"] == 0 and d["skipped"] == [{"id": ev, "why": "accept_failed: boom"}]
    assert box.astore.seen_map("alex", [ev]) == {}


def test_unarmed_store_still_serves_waiting(unarmed):
    unarmed.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    st, d = unarmed.call("GET", "/attention")
    assert st == 200 and d["ok"] and d["rev"] == 0 and [i["tab"] for i in d["items"]] == ["waiting"]


def test_bad_input(box):
    assert box.call("POST", "/attention/seen", body={"ids": "x"})[0] == 400
    assert box.call("POST", "/attention/seen", body={"ids": ["a"] * 201})[0] == 400
    assert box.call("GET", "/attention", params={"since": "yesterday"})[0] == 400
    assert box.call("GET", "/attention", params={"limit_updates": "x"})[0] == 400
    assert box.call("GET", "/attention", params={"limit_updates": "501"})[0] == 400
    assert box.call("GET", "/attention", params={"limit_answered": "0"})[0] == 400
    assert box.call("GET", "/attention", params={"tab": "everything"})[0] == 400
    assert box.call("GET", "/attention", params={"agents": ",".join(f"a{i}" for i in range(501))})[0] == 400


def test_since_filters_updates_and_answered_but_never_waiting(box):
    box.ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    box.astore.add_done("gm", "old", key="k")
    _, d = box.call("GET", "/attention", params={"since": "2999-01-01T00:00:00+00:00",
                                                 "tab": "waiting,update,answered"})
    assert [i["tab"] for i in d["items"]] == ["waiting"]


def test_reads_need_a_bearer(box):
    async_status = box.call  # noqa: F841
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            return (await c.get("/attention")).status
    assert asyncio.run(go()) == 401


def test_a_members_answered_page_is_full_however_far_back_their_rows_are(box):
    rid = box.ledger.create(from_agent="gm", question="mine", worker_kind="pane")
    box.ledger.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    for i in range(30):
        r = box.ledger.create(from_agent="pm", question=f"other {i}", worker_kind="pane")
        box.ledger.record_answer(r, "approve", None, surface="web", answered_by="operator")
    c = sqlite3.connect(box.db)
    c.execute("UPDATE approval_requests SET answered_at='2020-01-01T00:00:00+00:00' WHERE id=?", [rid])
    c.commit(); c.close()
    _, d = box.call("GET", "/attention", params={"tab": "answered", "agents": "gm", "limit_answered": "5"})
    assert [i["id"] for i in d["items"]] == [rid]
    assert box.ledger.history(5, agents=["gm"])[0]["id"] == rid


def test_questionnaire_history_filters_by_agent_before_the_limit(box):
    mine = box.qstore.create(from_agent="gm", title="Mine", questions=[{"prompt": "C?", "kind": "free_text"}])
    box.qstore.submit(mine, {1: {"answer_text": "x"}})
    for i in range(5):
        q = box.qstore.create(from_agent="pm", title=f"o{i}", questions=[{"prompt": "C?", "kind": "free_text"}])
        box.qstore.submit(q, {1: {"answer_text": "x"}})
    assert [q["id"] for q in box.qstore.history(2, agents=["gm"])] == [mine]
    assert box.qstore.history(2, agents=[]) == []
