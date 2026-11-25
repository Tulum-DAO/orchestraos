"""Dismiss works for EVERY card kind and says who, where and why (operator order 2026-11-25, gm msg_b083c465).

Shaw pressed Deny on a task-for-you card on the web and got a bare 400; there was no Dismiss. The gateway's discard
(pending -> 'discarded', history, one honest notify to the author) already took any pending row; it now also keeps a
history line on the card's summary naming the surface, the answerer and the reason (as retire does), stamps the
attribution columns when they are armed, and the author's notice carries the reason. No new columns: the line lives in
`summary`, so an unarmed install behaves the same.

Who and where are the EDGE's word, never the client's (R7d, the rule _gateway_answer_tags already applies to answers):
the gateway reads only `reason` and a surface hint from the body, derives `by` and the device from the authenticated
principal, and checks the hint against that edge's own surfaces; every dismiss through the gateway leaves the line, so a
body-less dismiss (iOS today) and a web dismiss record the same act the same way. A forged `by` is ignored. A menu that
is on an agent's screen (the menu bridge's row, and the pane still shows it) is refused with 423: it is dismissed with
Escape on the pane, so the pane and the card agree; a bridge row whose pane moved on, a headless seat's card and an
agent's own written menu are dismissed like any other card.

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


def _bridge_menu(s, digest="abc"):
    """The menu bridge's row for a menu that IS on the agent's screen (origin menu_bridge, op_key menu:...)."""
    return s.create(from_agent="codex-gm", question="Which build?", worker_kind="pane", kind="menu",
                    op_key=f"menu:codex-gm:{digest}", menu={"options": [{"n": "1", "label": "A"}, {"n": "2", "label": "B"}]},
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
def test_no_fields_still_dismisses_and_records_where_and_who_from_the_edge(box, body):
    rid = _task(box.ledger)
    headers = {"Content-Type": "application/json"} if body is not None else None
    st, d = _raw(box, f"/approvals/{rid}/discard", body, headers=headers)
    assert (st, d["ok"], d["applied"], d["id"]) == (200, True, True, rid)
    row = box.ledger.get(rid)
    assert row["status"] == "discarded"
    line = row["summary"][row["summary"].rfind("— DISMISSED"):]
    assert line.endswith(" via gateway by operator") and ": " not in line               # where and who; no reason
    assert (row["answer_surface"], row["answered_by"], row.get("answer_device")) == ("gateway", "operator", None)
    assert box.sent["to_agent"] == "postcall-dev" and box.sent["body"].endswith("\n\n" + line)


def test_a_body_less_device_dismiss_is_recorded_like_a_web_one(box):
    rid = _task(box.ledger)
    st, d = _raw(box, f"/approvals/{rid}/discard", None, who="approve")
    assert (st, d["applied"]) == (200, True)
    row = box.ledger.get(rid)
    assert (row["answer_surface"], row["answered_by"]) == ("watch", "operator") and row["answer_device"].endswith(":Phone")
    assert "via watch by operator" in row["summary"]


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


def test_a_menu_still_on_the_agents_screen_is_refused_with_423_and_stays_pending(box, monkeypatch):
    import threading
    import scripts.watch_gateway as G
    rid = _bridge_menu(box.ledger)
    threads = []

    def live(session):
        threads.append(threading.current_thread() is threading.main_thread())
        return {"question": "Which build?", "options": []} if session == "codex-gm" else None
    monkeypatch.setattr(G, "_current_menu", live)
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"surface": "web", "reason": "old"})
    assert threads and not any(threads), "the pane read (ps + tmux capture) runs off the event loop"
    assert st == 423 and d["ok"] is False
    assert d["error"] == "This menu is on codex-gm's screen: dismiss it on the pane (Escape), which closes this card too."
    assert d["agent"] == "codex-gm" and "Escape" in d["why"] and d["id"] == rid
    assert box.ledger.get(rid)["status"] == "pending" and not box.sent
    st, _ = box.call("POST", f"/approvals/{rid}/discard")                                # the body-less call too
    assert st == 423 and box.ledger.get(rid)["status"] == "pending"


def test_a_bridge_row_whose_pane_moved_on_is_dismissed(box, monkeypatch):
    import scripts.watch_gateway as G
    rid = _bridge_menu(box.ledger)
    monkeypatch.setattr(G, "_current_menu", lambda session: None)                      # nothing on the screen now
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"reason": "stale"})
    assert (st, d["applied"]) == (200, True) and box.ledger.get(rid)["status"] == "discarded"
    rid2 = _bridge_menu(box.ledger, digest="def")
    monkeypatch.setattr(G, "_current_menu", lambda session: {"question": "Do you want to proceed?", "options": []})   # a different menu
    assert box.call("POST", f"/approvals/{rid2}/discard")[1]["applied"] is True


def test_a_headless_seats_card_is_not_mistaken_for_an_on_screen_menu_and_gets_its_own_deny(box, monkeypatch):
    import scripts.watch_gateway as G
    import headless_cards
    monkeypatch.setattr(G, "_current_menu", lambda session: {"question": "Which build?", "options": []})
    delivered = []
    monkeypatch.setattr(headless_cards, "deliver_dismissed", lambda row: delivered.append(row["id"]) or True)
    rid = box.ledger.create(from_agent="codex-gm", question="Which build?", worker_kind="pane", kind="menu",
                            op_key="menu:codex-gm:hl-0123456789abcdef", origin="headless",
                            menu={"options": [{"n": "1", "label": "A"}],
                                  "bridge": {"kind": "headless", "seat": "codex-gm", "session": "codex-gm", "request_id": "req-1", "tool": "AskUserQuestion"}})
    assert G._on_screen_menu(box.ledger.get(rid)) == "off"
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"reason": "x"})
    assert (st, d["applied"]) == (200, True)
    assert delivered == [rid] and not box.sent          # a headless card is answered over its held request, no inbox notice


def test_an_unreadable_pane_refuses_the_dismiss_instead_of_calling_the_menu_gone(box, monkeypatch):
    import scripts.watch_gateway as G
    rid = _bridge_menu(box.ledger)

    def boom(session):
        raise RuntimeError("tmux capture-pane timed out")
    monkeypatch.setattr(G, "_current_menu", boom)
    assert G._on_screen_menu(box.ledger.get(rid)) == "unreadable"
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"reason": "old"})
    assert st == 423 and d["ok"] is False and "Could not check codex-gm's screen" in d["error"] and "not proof" in d["why"]
    assert box.ledger.get(rid)["status"] == "pending" and not box.sent


def test_an_agents_own_menu_card_is_dismissed_like_any_other(box):
    rid = _menu(box.ledger)
    st, d = box.call("POST", f"/approvals/{rid}/discard", body={"surface": "web", "reason": "decided elsewhere"})
    assert (st, d["applied"]) == (200, True)
    assert "via web by operator: decided elsewhere" in box.ledger.get(rid)["summary"]
    assert box.sent["to_agent"] == "codex-gm"


def test_on_screen_menu_is_origin_and_the_live_pane_together(monkeypatch):
    import scripts.watch_gateway as G
    live = {"codex-gm": {"question": "Which build?  (1) A (2) B", "options": []}}
    monkeypatch.setattr(G, "_current_menu", lambda session: live.get(session))
    bridge = {"origin": "menu_bridge", "from_agent": "codex-gm", "question": "Which build?", "op_key": "menu:codex-gm:abc"}
    assert G._on_screen_menu(bridge) == "on"                                           # containment, version-tolerant
    assert G._on_screen_menu({**bridge, "from_agent": "pm-demo"}) == "off"              # another pane
    assert G._on_screen_menu({**bridge, "origin": None}) == "off"                       # an agent's own menu: never read
    assert G._on_screen_menu({**bridge, "origin": "headless"}) == "off"
    assert G._on_screen_menu({**bridge, "question": "Deploy now?"}) == "off"            # the pane shows a different menu


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
