"""Dismiss works for EVERY card kind and says who, where and why (operator order 2026-11-25, gm msg_b083c465).

Shaw pressed Deny on a task-for-you card on the web and got a bare 400; there was no Dismiss. The gateway's discard
(pending -> 'discarded', history, one honest notify to the author) already took any pending row; it now also keeps a
history line on the card's summary naming the surface, the answerer and the reason (as retire does), stamps the
attribution columns when they are armed, and the author's notice carries the reason. No new columns: the line lives in
`summary`, so an unarmed install behaves the same.

Who and where are the EDGE's word, never the client's (R7d, the rule _gateway_answer_tags already applies to answers):
the gateway reads only `reason` and a surface hint from the body, derives `by` and the device from the authenticated
principal, and checks the hint against that edge's own surfaces. A forged `by` is ignored. A menu that is on an agent's
screen (the menu bridge's row) is refused: it is dismissed with Escape on the pane, so the pane and the card agree.

Hermetic: tmp tasks.db, HOME a tmp dir, no ntfy; the author's notice is captured by stubbing the message store; the HTTP
tests run the gateway app on a scratch box (attention_testbox) with a fleet bearer and minted device tokens.
"""
import asyncio
import json

import pytest
import approval_resume
from approval_schema import ApprovalStore, dismiss_line


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NTFY_TOKEN_FILE", "/nonexistent/ntfy-token-for-tests")
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate(); return s


def _approval(s):
    return s.create(from_agent="pm-demo", question="Ship it?", worker_kind="pane", options=["approve", "deny", "hold"])


def _task(s):
    return s.create(from_agent="postcall-dev", question="Call Dana back", worker_kind="pane",
                    options=["done", "cant", "snooze"], kind="human_task", block_task="Call Dana back")


def _menu(s):
    """An agent's OWN written decision menu: not on any screen, so the card is the only place to dismiss it."""
    return s.create(from_agent="codex-gm", question="Which build?", worker_kind="pane", kind="menu",
                    op_key="decide:codex-gm:build", menu={"options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]})


def _bridge_menu(s):
    """The menu bridge's row for a menu that IS on the agent's screen (origin menu_bridge, op_key menu:...)."""
    return s.create(from_agent="codex-gm", question="Which build?", worker_kind="pane", kind="menu",
                    op_key="menu:codex-gm:abc", menu={"options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]},
                    origin="menu_bridge")


# ---- the store ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make", [_approval, _task, _menu], ids=["approval", "human_task", "menu"])
def test_every_kind_is_dismissed_with_a_history_line(store, make):
    rid = make(store)
    assert store.mark_discarded(rid, surface="web", by="operator", reason="old, remove") is True
    row = store.get(rid)
    assert row["status"] == "discarded" and row["discarded_at"]
    assert "DISMISSED" in (row["summary"] or "") and "via web" in row["summary"] and "old, remove" in row["summary"]


def test_a_dismiss_without_a_reason_still_leaves_a_line(store):
    rid = _task(store)
    assert store.mark_discarded(rid, surface="phone") is True
    assert "DISMISSED" in store.get(rid)["summary"] and "via phone" in store.get(rid)["summary"]


def test_the_plain_call_still_works_and_only_a_pending_row_is_dismissed(store):
    rid = _task(store)
    assert store.mark_discarded(rid) is True            # the gateway's existing call shape: no line, same as before
    assert "DISMISSED" not in (store.get(rid)["summary"] or "")
    assert store.mark_discarded(rid, reason="again") is False
    assert "again" not in (store.get(rid)["summary"] or "")


def test_the_line_is_one_string_and_the_store_writes_exactly_it(store):
    rid = _task(store)
    now = "2026-11-25T21:30:00.123456+00:00"
    line = dismiss_line(now, "web", "operator", "old, remove")
    assert line == "— DISMISSED 2026-11-25T21:30:00Z via web by operator: old, remove"
    assert dismiss_line(now) == "— DISMISSED 2026-11-25T21:30:00Z"
    store.mark_discarded(rid, surface="web", by="operator", reason="old, remove", now=now)
    assert store.get(rid)["summary"].endswith("\n\n" + line)


def test_attribution_columns_and_the_device_are_stamped_when_armed(monkeypatch, tmp_path):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    s = ApprovalStore(db_path=str(tmp_path / "armed.db")); s.migrate()
    rid = _task(s)
    assert s.mark_discarded(rid, surface="watch", by="operator", reason="done another way", device="dev1:Watch") is True
    row = s.get(rid)
    assert (row.get("answer_surface"), row.get("answered_by"), row.get("answer_device")) == ("watch", "operator", "dev1:Watch")


# ---- the author's notice ---------------------------------------------------------------------------------------------

def _capture(monkeypatch):
    sent = {}

    class Msgs:
        def send(self, **kw): sent.update(kw)
    monkeypatch.setattr(approval_resume, "_msg_store", lambda: Msgs())
    return sent


def test_the_author_is_told_the_reason_from_the_line_this_dismiss_wrote(store, monkeypatch):
    rid = _task(store)
    now = "2026-11-25T21:30:00+00:00"
    line = dismiss_line(now, "web", "operator", "old, remove")
    store.mark_discarded(rid, surface="web", by="operator", reason="old, remove", now=now)
    sent = _capture(monkeypatch)
    approval_resume.deliver_discarded(store.get(rid), store, line)
    assert sent["to_agent"] == "postcall-dev" and sent["type"] == "approval_discarded"
    assert sent["body"].endswith("\n\n" + line)
    assert "notify" not in sent["type"]            # told by mail, never a push


def test_a_plain_dismiss_never_mails_an_older_cards_line_carried_in_the_summary(store, monkeypatch):
    # A re-raise that copied the old card's summary carries its "— DISMISSED ...: old, remove" line along.
    rid = store.create(from_agent="postcall-dev", question="Call Dana back", worker_kind="pane", kind="human_task",
                       options=["done", "cant", "snooze"], block_task="Call Dana back",
                       summary="Call Dana back\n\n— DISMISSED 2026-11-24T10:00:00Z via web by operator: old, remove")
    store.mark_discarded(rid)                      # a plain dismiss (iOS today, an attention rule): no line of its own
    sent = _capture(monkeypatch)
    approval_resume.deliver_discarded(store.get(rid), store)
    assert "— DISMISSED" not in sent["body"] and "old, remove" not in sent["body"]


# ---- the gateway edge: POST /approvals/{id}/discard ------------------------------------------------------------------

@pytest.fixture
def box(tmp_path, monkeypatch):
    from attention_testbox import Box
    b = Box(tmp_path, monkeypatch)                 # armed: the attribution columns exist, so the stamps are checked
    b.sent = _capture(monkeypatch)
    return b


def _raw(box, path, data, who="fleet", headers=None):
    """A POST with a raw body (non-JSON, or none at all) through the same scratch app."""
    from aiohttp.test_utils import TestClient, TestServer
    import scripts.watch_gateway as G

    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            h = dict(box.auth(who)); h.update(headers or {})
            r = await c.request("POST", path, data=data, headers=h)
            return r.status, await r.json()
    return asyncio.run(go())


def test_the_route_needs_the_approve_scope_and_a_principal(box):
    import scripts.watch_gateway as G
    assert G.ROUTE_SCOPES[("POST", "/approvals/{id}/discard")] == "approve"
    rid = _task(box.ledger)
    st, _ = _raw(box, f"/approvals/{rid}/discard", None, headers={"Authorization": "Bearer nope"})
    assert st == 401 and box.ledger.get(rid)["status"] == "pending"
    st, _ = box.call("POST", f"/approvals/{rid}/discard", who="read")
    assert st in (401, 403) and box.ledger.get(rid)["status"] == "pending"


@pytest.mark.parametrize("body", [None, b"", b"{}", b"not json", b"[1,2]", b"null"],
                         ids=["no-body", "empty", "empty-object", "non-json", "list", "null"])
def test_no_fields_is_the_plain_dismiss_as_before(box, body):
    rid = _task(box.ledger)
    headers = {"Content-Type": "application/json"} if body is not None else None
    st, d = _raw(box, f"/approvals/{rid}/discard", body, headers=headers)
    assert (st, d["ok"], d["applied"], d["id"]) == (200, True, True, rid)
    row = box.ledger.get(rid)
    assert row["status"] == "discarded" and "DISMISSED" not in (row["summary"] or "")
    assert row.get("answered_by") is None and row.get("answer_surface") is None
    assert box.sent["to_agent"] == "postcall-dev" and "— DISMISSED" not in box.sent["body"]


def test_the_web_dismiss_records_where_who_and_why_with_who_from_the_bearer(box):
    rid = _task(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"surface": "web", "reason": "old, remove"})
    assert (st, d["applied"]) == (200, True)
    row = box.ledger.get(rid)
    assert "via web by operator: old, remove" in row["summary"]
    assert (row["answer_surface"], row["answered_by"], row.get("answer_device")) == ("web", "operator", None)
    assert box.sent["body"].endswith(row["summary"][row["summary"].rfind("— DISMISSED"):])   # the same line, once


def test_a_forged_by_and_a_foreign_surface_are_ignored_on_the_fleet_edge(box):
    rid = _task(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard",
                     body={"surface": "watch", "by": "pm-demo", "reason": "r"})
    assert (st, d["applied"]) == (200, True)
    row = box.ledger.get(rid)
    assert row["answered_by"] == "operator" and "by pm-demo" not in row["summary"]
    assert row["answer_surface"] == "gateway" and "via watch" not in row["summary"]     # the fleet bearer is not a watch
    assert "via gateway by operator: r" in row["summary"]


def test_a_device_dismiss_is_stamped_with_its_device_and_may_only_be_the_watch_or_the_phone(box):
    rid = _task(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard", who="approve",
                     body={"surface": "web", "by": "operator", "reason": "done another way"})
    assert (st, d["applied"]) == (200, True)
    row = box.ledger.get(rid)
    assert row["answer_surface"] == "watch" and row["answered_by"] == "operator"        # a device is not the web
    assert row["answer_device"] and row["answer_device"].endswith(":Phone")
    rid2 = _task(box.ledger)
    box.call("POST", f"/approvals/{rid2}/discard", who="approve", body={"surface": "phone", "reason": "x"})
    assert box.ledger.get(rid2)["answer_surface"] == "phone"


def test_a_reason_alone_is_enough_and_is_capped(box):
    rid = _task(box.ledger)
    box.call("POST", f"/approvals/{rid}/discard", body={"reason": " " + "r" * 900 + " "})
    row = box.ledger.get(rid)
    line = row["summary"][row["summary"].rfind("— DISMISSED"):]
    assert line.endswith(": " + "r" * 500) and "via gateway by operator" in line


def test_a_menu_on_the_agents_screen_is_refused_and_stays_pending(box):
    rid = _bridge_menu(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"surface": "web", "reason": "old"})
    assert st == 409 and d["ok"] is False and d["error"] == "dismiss it on the pane"
    assert d["agent"] == "codex-gm" and "Escape" in d["why"] and d["id"] == rid
    assert box.ledger.get(rid)["status"] == "pending" and not box.sent
    st, _ = box.call("POST", f"/approvals/{rid}/discard")                                # the plain call too
    assert st == 409 and box.ledger.get(rid)["status"] == "pending"


def test_an_agents_own_menu_card_is_dismissed_like_any_other(box):
    rid = _menu(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"surface": "web", "reason": "decided elsewhere"})
    assert (st, d["applied"]) == (200, True)
    assert "via web by operator: decided elsewhere" in box.ledger.get(rid)["summary"]
    assert box.sent["to_agent"] == "codex-gm"


def test_a_second_dismiss_is_idempotent_and_an_unknown_id_is_404(box):
    rid = _approval(box.ledger)
    assert box.call("POST", f"/approvals/{rid}/discard", body={"reason": "first"})[1]["applied"] is True
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"reason": "second"})
    assert (st, d["applied"]) == (200, False) and "second" not in box.ledger.get(rid)["summary"]
    assert box.call("POST", "/approvals/apr_nope/discard", body={"reason": "x"})[0] == 404


def test_discard_fields_reads_only_the_reason_and_a_surface_hint():
    from watch_gateway import discard_fields, discard_provenance
    assert discard_fields(None) == {"surface": None, "reason": None}
    assert discard_fields({"surface": " web ", "reason": "  old  ", "by": "operator"}) == {"surface": "web", "reason": "old"}
    assert "by" not in discard_fields({"by": "operator"})
    assert discard_fields({"surface": 7, "reason": ["x"]}) == {"surface": None, "reason": None}
    assert len(discard_fields({"reason": "r" * 5000})["reason"]) == 500

    class Req(dict):
        pass
    fleet = Req(principal={"id": "legacy", "label": "fleet"})
    phone = Req(principal={"id": "dev7", "label": "Phone"})
    assert discard_provenance(fleet, "web") == ("web", "operator", None)
    assert discard_provenance(fleet, "watch") == ("gateway", "operator", None)       # not its edge
    assert discard_provenance(fleet, "nope") == ("gateway", "operator", None)
    assert discard_provenance(phone, "phone") == ("phone", "operator", "dev7:Phone")
    assert discard_provenance(phone, "web") == ("watch", "operator", "dev7:Phone")   # not its edge
