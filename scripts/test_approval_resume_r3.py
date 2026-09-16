"""R3 (reconciled onto current main) -- pane answers land in the LIVE agent head.

The pane-resume path verified-injects the answer into from_agent's LIVE HEAD pane
(post-rotation aware) AND preserves the durable msg_store row. Also asserts the
kind=='menu' keypress path (_fire_menu_resume) is untouched by R3. Hermetic: a
temp ApprovalStore + injected msg_send/resolve/inject seams -- no live store, no
gateway, no tmux, no live services (must NOT inject into a live pane).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_resume as ar
from approval_schema import ApprovalStore


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = ApprovalStore(db_path=path)
    s.migrate()
    return s, path


def _answered_pane_row(store, from_agent="gm"):
    rid = store.create(from_agent=from_agent, question="Advance now or hold?",
                       worker_kind="pane", options=["advance", "hold"])
    store.record_answer(rid, "advance", "ship it")
    return store.get(rid)


# --- R3 pane path: dual delivery (inject to live head + durable row) ----------

def test_pane_resume_injects_into_live_head_and_keeps_row():
    store, path = _tmp_store()
    try:
        row = _answered_pane_row(store, from_agent="gm")
        rows = []
        outcome = ar._fire_pane_resume(
            row, store,
            msg_send=lambda r, body: rows.append((r["from_agent"], body)),
            resolve=lambda aid: ("gm-g5", "succeeded-by-chain"),  # gm rotated -> gm-g5
            inject=lambda session, text: (True, {"reason": "delivered"}),
        )
        assert outcome["session"] == "gm-g5"       # injected into the LIVE HEAD
        assert outcome["injected"] is True
        assert len(rows) == 1 and rows[0][0] == "gm"   # durable row ALSO written
        assert "[APPROVAL RESOLVED" in rows[0][1]
    finally:
        os.unlink(path)


def test_pane_resume_body_carries_answer_and_ack_instruction():
    store, path = _tmp_store()
    try:
        row = _answered_pane_row(store)
        seen = {}
        ar._fire_pane_resume(row, store,
                             msg_send=lambda r, body: seen.setdefault("body", body),
                             resolve=lambda aid: ("gm", "direct-live"),
                             inject=lambda s, t: (True, {}))
        body = seen["body"]
        assert "advance (ADVANCE)" in body and "ship it" in body
        assert f"approval.py ack {row['id']}" in body
    finally:
        os.unlink(path)


def test_no_live_head_keeps_row_and_does_not_inject():
    store, path = _tmp_store()
    try:
        row = _answered_pane_row(store)
        injected = []
        rows = []
        outcome = ar._fire_pane_resume(
            row, store,
            msg_send=lambda r, body: rows.append(r["id"]),
            resolve=lambda aid: (None, "no-live-head"),
            inject=lambda s, t: injected.append(s) or (True, {}),
        )
        assert injected == []                       # never inject a dead pane
        assert outcome["injected"] is False and outcome["reason"] == "no-live-head"
        assert len(rows) == 1                        # durable row STILL written
    finally:
        os.unlink(path)


def test_inject_refused_when_pane_busy_row_is_backstop():
    store, path = _tmp_store()
    try:
        row = _answered_pane_row(store)
        rows = []
        outcome = ar._fire_pane_resume(
            row, store,
            msg_send=lambda r, body: rows.append(r["id"]),
            resolve=lambda aid: ("gm", "direct-live"),
            inject=lambda s, t: (False, {"reason": "busy"}),
        )
        assert outcome["injected"] is False and outcome["reason"] == "busy"
        assert len(rows) == 1                        # row is the watchdog backstop
    finally:
        os.unlink(path)


def test_fire_resume_pane_routes_through_pane_resume(monkeypatch):
    """fire_resume(worker_kind='pane', no kind) delegates to _fire_pane_resume."""
    store, path = _tmp_store()
    try:
        row = _answered_pane_row(store)
        called = {}
        monkeypatch.setattr(ar, "_fire_pane_resume",
                            lambda r, s: called.setdefault("id", r["id"]) and {} or
                            {"injected": True, "reason": "delivered", "session": "x"})
        ar.fire_resume(row, store)
        assert called.get("id") == row["id"]
        # delivered = REAL attempt stamped by the outer fire_resume (SLA spec)
        assert store.get(row["id"])["resume_attempts"] == 1
    finally:
        os.unlink(path)


# --- menu path (main's _fire_menu_resume) is UNTOUCHED by R3 ------------------

def test_menu_row_still_uses_keypress_path_not_pane_inject(monkeypatch):
    """A kind=='menu' row must route to _fire_menu_resume (keypress), NEVER the
    R3 pane inject. Guards the near-regression GM caught."""
    store, path = _tmp_store()
    try:
        rid = store.create(from_agent="gm", question="pick", worker_kind="pane",
                           options=["a", "b"], kind="menu")
        store.record_answer(rid, "a", None)
        row = store.get(rid)
        calls = []
        monkeypatch.setattr(ar, "_fire_menu_resume",
                            lambda r, s: calls.append("menu"))
        monkeypatch.setattr(ar, "_fire_pane_resume",
                            lambda *a, **k: calls.append("pane"))
        ar.fire_resume(row, store)
        assert calls == ["menu"]                     # menu keypress path, not pane
    finally:
        os.unlink(path)


def test_node_path_unchanged_no_pane_inject(monkeypatch):
    store, path = _tmp_store()
    try:
        rid = store.create(from_agent="worker", question="q", worker_kind="node")
        store.record_answer(rid, "approve", None)
        row = store.get(rid)
        calls = []
        monkeypatch.setattr(ar, "_fire_pane_resume",
                            lambda *a, **k: calls.append("pane"))
        ar.fire_resume(row, store)
        assert calls == []                           # node path injects nothing
        assert store.get(rid)["resume_attempts"] == 1
    finally:
        os.unlink(path)
