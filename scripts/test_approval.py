import os, sqlite3, tempfile, time, pytest
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore
import approval_schema

@pytest.fixture
def store(tmp_path):
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db)
    s.migrate()
    return s

def test_request_creates_pending_row(store):
    rid = store.create(from_agent="acme-merge-3", question="Land PR 0dd384b?",
                       op_key="merge:0dd384b", worker_kind="pane", thread_key="acme-merge-3")
    row = store.get(rid)
    assert row["status"] == "pending"
    assert row["from_agent"] == "acme-merge-3"
    assert row["op_key"] == "merge:0dd384b"
    assert row["options"] == '["approve", "deny", "hold"]'

def test_dedup_on_from_agent_and_op_key(store):
    a = store.create(from_agent="x", question="approve deploy?", op_key="deploy:acme@aaa", worker_kind="pane")
    b = store.create(from_agent="x", question="approve deploy?", op_key="deploy:acme@aaa", worker_kind="pane")
    assert a == b  # same op_key + agent while pending -> same row

def test_same_text_different_opkey_are_distinct(store):
    a = store.create(from_agent="x", question="approve deploy?", op_key="deploy:acme@aaa", worker_kind="pane")
    b = store.create(from_agent="x", question="approve deploy?", op_key="deploy:northwind@bbb", worker_kind="pane")
    assert a != b  # identical text, different op -> two rows (the safety fix)

def test_no_opkey_never_dedups(store):
    a = store.create(from_agent="x", question="q", op_key=None, worker_kind="pane")
    b = store.create(from_agent="x", question="q", op_key=None, worker_kind="pane")
    assert a != b

def test_answer_is_id_bound_and_only_pending(store):
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    assert store.record_answer(rid, "approve", None) is True
    r = store.get(rid); assert r["status"] == "answered" and r["answer"] == "approve"
    # second answer to the same (now-answered) id is refused -> id-binding + answer-once
    assert store.record_answer(rid, "deny", None) is False
    assert store.get(rid)["answer"] == "approve"

def test_answer_unknown_id_refused(store):
    assert store.record_answer("apr_nope", "approve", None) is False

def test_ack_moves_answered_to_resumed(store):
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    store.record_answer(rid, "approve", None)
    store.mark_resumed_fired(rid)
    assert store.ack(rid) is True
    assert store.get(rid)["status"] == "resumed"

# NOTE (merge 2026-08-14): live keeps expire_due the operator-GATED (EXPIRE_PENDING,
# env-off on the running gateway); the cron sweep call is gone (R8). These
# test the gated mechanism; no-expiry regressions live in test_r8_*.py.
def test_expire_marks_pending_only(store, monkeypatch):
    # the operator's Q4 gate is OFF by default (apr_10c0c829); this test exercises the mechanics.
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True)
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", rid))
    expired = store.expire_due()
    assert rid in expired
    assert store.get(rid)["status"] == "expired"

def test_expire_does_not_clobber_answered_row(store):
    # simulate the TOCTOU: row is selected as pending-expired, but gets answered before the UPDATE lands.
    # We prove the fix by: answer the row, backdate expires_at into the past, then call expire_due().
    # An answered row must NOT be flipped to expired.
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    store.record_answer(rid, "approve", None)              # now 'answered'
    import sqlite3
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", rid))
    store.expire_due()                                     # must not touch the answered row
    assert store.get(rid)["status"] == "answered"          # answer preserved, NOT expired
    assert store.get(rid)["answer"] == "approve"

def test_expire_update_has_status_guard(store, monkeypatch):
    # A pending, past-expiry row DOES get expired (baseline) — with the operator's Q4 gate forced ON.
    monkeypatch.setattr(approval_schema, "_expire_pending_enabled", lambda: True)
    rid = store.create(from_agent="x", question="q", op_key="k1", worker_kind="pane")
    import sqlite3
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", rid))
    assert rid in store.expire_due()
    assert store.get(rid)["status"] == "expired"

def test_pane_resume_sends_msg_and_marks_fired(store, monkeypatch):
    import approval_resume as R
    rid = store.create(from_agent="acme-merge-3", question="Land it?", op_key="k",
                       worker_kind="pane", thread_key="acme-merge-3")
    store.record_answer(rid, "approve", None)
    sent = {}
    class FakeMsgStore:
        def send(self, **kw): sent.update(kw); return "msg_1"
    monkeypatch.setattr(R, "_msg_store", lambda: FakeMsgStore())
    R.fire_resume(store.get(rid), store)
    assert sent["to_agent"] == "acme-merge-3"
    assert "approve" in sent["body"]
    assert rid in sent["body"]                      # tells the agent which id to ack
    # SLA spec 2026-08-14: no live head in the test env = CHEAP refusal -> retry
    # stamp only, no real attempt burned.
    row = R and store.get(rid)
    assert row["last_attempt_at"] is not None
    assert row["resume_attempts"] == 0 and row["resumed_at"] is None

def test_watchdog_refires_unacked_answered(store, monkeypatch):
    import approval_resume as R
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane", thread_key="x")
    store.record_answer(rid, "approve", None)
    store.mark_resumed_fired(rid)  # attempt 1, but no ack
    import sqlite3
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET resumed_at=?, last_attempt_at=? WHERE id=?",
                  ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", rid))
    refired = []
    monkeypatch.setattr(R, "fire_resume", lambda row, s: refired.append(row["id"]))
    R.watchdog(store=store)
    assert rid in refired

def test_watchdog_escalates_after_max_attempts(store, monkeypatch):
    import approval_resume as R
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane", thread_key="x")
    store.record_answer(rid, "approve", None)
    import sqlite3
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE approval_requests SET resumed_at=?, last_attempt_at=?, resume_attempts=3 WHERE id=?",
                  ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", rid))
    escalated = []
    monkeypatch.setattr(R, "escalate",
                        lambda row, s, cheap=False: escalated.append(row["id"]))
    monkeypatch.setattr(R, "fire_resume", lambda row, s: (_ for _ in ()).throw(AssertionError("should escalate, not refire")))
    R.watchdog(store=store)
    assert rid in escalated

def test_escalate_stamps_escalated_at_not_attempts(store, monkeypatch):
    # SLA spec 2026-08-14: escalation re-arms via escalated_at (30-min throttle)
    # and must NOT burn a delivery attempt.
    import urllib.request
    import approval_resume as R
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane", thread_key="x")
    store.record_answer(rid, "approve", None)
    before_attempts = store.get(rid)["resume_attempts"]
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(Exception("no net")))
    R.escalate(store.get(rid), store)
    row = store.get(rid)
    assert row["escalated_at"] is not None                      # throttle re-armed
    assert row["last_attempt_at"] is not None
    assert row["resume_attempts"] == before_attempts            # NOT burned

def test_watchdog_survives_fire_resume_exception(store, monkeypatch):
    # one row whose fire_resume raises must not stop the watchdog from processing others
    import approval_resume as R
    r1 = store.create(from_agent="a1", question="q1", op_key="k1", worker_kind="pane", thread_key="a1")
    r2 = store.create(from_agent="a2", question="q2", op_key="k2", worker_kind="pane", thread_key="a2")
    for rid in (r1, r2):
        store.record_answer(rid, "approve", None)
    # both are answered, resumed_at NULL -> both due now
    processed = []
    def flaky(row, s):
        if row["id"] == r1:
            raise RuntimeError("boom on r1")
        processed.append(row["id"])
    monkeypatch.setattr(R, "fire_resume", flaky)
    R.watchdog(store=store)   # must NOT raise
    assert r2 in processed    # r2 still processed despite r1 blowing up

def test_build_payload_has_three_id_bound_actions(store):
    import approval_notify as n
    rid = store.create(from_agent="x", question="Land it?", op_key="k", worker_kind="pane")
    payload = n.build_payload(store.get(rid), token="tk_test")
    assert payload["topic"] == n.NTFY_APPROVALS_TOPIC
    acts = payload["actions"]
    assert len(acts) == 3
    labels = {a["label"] for a in acts}
    assert labels == {"Approve", "Deny", "Hold"}
    # EACH action's body is valid JSON carrying THIS id (the id-binding guard, per-button)
    import json as _j
    for a in acts:
        b = _j.loads(a["body"])          # must parse — proves no comma truncation
        assert b["id"] == rid
        assert b["answer"] in ("approve", "deny", "hold")
        assert a["headers"]["Authorization"] == "Bearer tk_test"
        assert a["url"].endswith(n.NTFY_ANSWERS_TOPIC)

def test_notify_posts_json_to_root_and_marks_notified(store, monkeypatch):
    import approval_notify as n, json as _j
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    captured = {}
    def fake_post(url, headers, data):
        captured["url"] = url; captured["headers"] = headers; captured["data"] = data; return 200
    monkeypatch.setattr(n, "_http_post", fake_post)
    monkeypatch.setattr(n, "ntfy_token", lambda: "tk_test")
    n.notify(rid, store=store)
    # posts to ROOT base (topic is in the body, not the path)
    assert captured["url"] == n.NTFY_BASE
    assert captured["headers"]["Authorization"] == "Bearer tk_test"
    body = _j.loads(captured["data"])
    assert body["topic"] == n.NTFY_APPROVALS_TOPIC
    assert len(body["actions"]) == 3
    assert store.get(rid)["notified_at"] is not None

def test_notify_refuses_empty_token(store, monkeypatch):
    import approval_notify as n
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    monkeypatch.setattr(n, "ntfy_token", lambda: "")
    import pytest
    with pytest.raises(RuntimeError):
        n.notify(rid, store=store)
    assert store.get(rid)["notified_at"] is None   # NOT marked notified on refusal

def test_notify_skips_non_pending(store, monkeypatch):
    import approval_notify as n
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    store.record_answer(rid, "approve", None)   # now 'answered', not pending
    called = []
    monkeypatch.setattr(n, "_http_post", lambda *a, **k: called.append(1) or 200)
    monkeypatch.setattr(n, "ntfy_token", lambda: "tk_test")
    n.notify(rid, store=store)
    assert called == []   # no push for a non-pending row

def test_listener_applies_id_bound_answer(store, monkeypatch):
    import approval_listener as L, json
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    fired = {}
    monkeypatch.setattr(L, "fire_resume", lambda r, s: fired.setdefault("id", r["id"]))
    # a tap event as ntfy delivers it: message body is our JSON
    event = {"event": "message", "time": 111, "message": json.dumps({"id": rid, "answer": "approve"})}
    L.handle_event(event, store=store)
    assert store.get(rid)["answer"] == "approve"
    assert store.get(rid)["status"] == "answered"
    assert fired["id"] == rid                       # resume was triggered

def test_listener_ignores_unknown_id(store, monkeypatch):
    import approval_listener as L, json
    monkeypatch.setattr(L, "fire_resume", lambda r, s: (_ for _ in ()).throw(AssertionError("should not fire")))
    L.handle_event({"event": "message", "time": 1, "message": json.dumps({"id": "apr_nope", "answer": "approve"})}, store=store)
    # no crash, nothing fired

def test_listener_skips_non_message_events(store):
    import approval_listener as L
    L.handle_event({"event": "open"}, store=store)      # keepalive/open events ignored, no crash
    L.handle_event({"event": "keepalive"}, store=store)

def test_listener_ignores_malformed_message_json(store, monkeypatch):
    import approval_listener as L
    monkeypatch.setattr(L, "fire_resume", lambda r, s: (_ for _ in ()).throw(AssertionError("should not fire")))
    L.handle_event({"event": "message", "time": 1, "message": "{not valid json"}, store=store)  # no crash

def test_listener_missing_id_or_answer_noop(store, monkeypatch):
    import approval_listener as L, json
    monkeypatch.setattr(L, "fire_resume", lambda r, s: (_ for _ in ()).throw(AssertionError("should not fire")))
    L.handle_event({"event": "message", "time": 1, "message": json.dumps({"answer": "approve"})}, store=store)  # no id
    L.handle_event({"event": "message", "time": 1, "message": json.dumps({"id": "apr_x"})}, store=store)        # no answer

def test_listener_second_tap_on_answered_is_noop(store, monkeypatch):
    import approval_listener as L, json
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    fire_count = {"n": 0}
    monkeypatch.setattr(L, "fire_resume", lambda r, s: fire_count.__setitem__("n", fire_count["n"] + 1))
    ev = {"event": "message", "time": 100, "message": json.dumps({"id": rid, "answer": "approve"})}
    L.handle_event(ev, store=store)          # first tap: answers + fires
    L.handle_event(ev, store=store)          # replayed/second tap: must be a NO-OP (already answered)
    assert fire_count["n"] == 1              # resume fired exactly once
    assert store.get(rid)["answer"] == "approve"   # first answer preserved

def test_cli_request_creates_and_notifies(store, monkeypatch, capsys):
    import approval as A
    monkeypatch.setattr(A, "_store", lambda: store)
    notified = {}
    monkeypatch.setattr(A, "notify", lambda rid, store=None: notified.setdefault("id", rid))
    rc = A.main(["request", "Land it?", "--from", "acme-merge-3",
                 "--worker-kind", "pane", "--op-key", "merge:0dd384b", "--thread-key", "acme-merge-3"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out.startswith("apr_")          # prints the id for the agent to reference
    assert notified["id"] == out           # notifier fired inline (instant, not cron-delayed)

def test_cli_request_survives_notify_failure(store, monkeypatch, capsys):
    # if notify raises (e.g. token missing), request STILL succeeds — cron backstop re-notifies
    import approval as A
    monkeypatch.setattr(A, "_store", lambda: store)
    def boom(rid, store=None): raise RuntimeError("token missing")
    monkeypatch.setattr(A, "notify", boom)
    rc = A.main(["request", "q", "--from", "x", "--worker-kind", "pane", "--op-key", "k"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out.startswith("apr_")          # id still printed; row persisted despite notify failure

def test_cli_ack_marks_resumed(store, monkeypatch):
    import approval as A
    monkeypatch.setattr(A, "_store", lambda: store)
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    store.record_answer(rid, "approve", None); store.mark_resumed_fired(rid)
    assert A.main(["ack", rid, "--from", "x"]) == 0
    assert store.get(rid)["status"] == "resumed"

def test_cli_get_prints_row(store, monkeypatch, capsys):
    import approval as A, json
    monkeypatch.setattr(A, "_store", lambda: store)
    rid = store.create(from_agent="x", question="q", op_key="k", worker_kind="pane")
    rc = A.main(["get", rid])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out)["id"] == rid

def test_cli_get_missing_id_exits_1(store, monkeypatch, capsys):
    import approval as A, json
    monkeypatch.setattr(A, "_store", lambda: store)
    rc = A.main(["get", "apr_does_not_exist"])
    out = capsys.readouterr().out
    assert rc == 1
    assert json.loads(out)["error"] == "not found"

def test_cli_request_options_stripped(store, monkeypatch, capsys):
    import approval as A, json
    monkeypatch.setattr(A, "_store", lambda: store)
    monkeypatch.setattr(A, "notify", lambda rid, store=None: None)
    rc = A.main(["request", "q", "--from", "x", "--worker-kind", "pane",
                 "--op-key", "k", "--options", "approve, deny , hold"])
    rid = capsys.readouterr().out.strip()
    assert rc == 0
    opts = json.loads(store.get(rid)["options"])
    assert opts == ["approve", "deny", "hold"]   # whitespace stripped, no empties

def test_cli_request_empty_options_falls_back_to_default(store, monkeypatch, capsys):
    import approval as A, json
    monkeypatch.setattr(A, "_store", lambda: store)
    monkeypatch.setattr(A, "notify", lambda rid, store=None: None)
    rc = A.main(["request", "q", "--from", "x", "--worker-kind", "pane", "--op-key", "k2", "--options", " , "])
    rid = capsys.readouterr().out.strip()
    opts = json.loads(store.get(rid)["options"])
    assert opts == ["approve", "deny", "hold"]   # empty -> default

# ---------------- menu fixture injection (app-watch-decision-surface §7) ----------------
def test_cli_request_menu_json_creates_menu_row(store, monkeypatch, capsys):
    import json, approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    menu = {"question": "Which color?",
            "options": [{"n": "1", "label": "Red", "input_kind": "direct"},
                        {"n": "2", "label": "Type something.", "input_kind": "free_text"}],
            "selected_n": "1", "source_session": "acme-dev", "captured_at": 1786520000.0}
    rc = approval.main(["request", "Which color?", "--from", "bridge",
                        "--worker-kind", "pane", "--op-key", "menu:acme-dev:abc",
                        "--menu-json", json.dumps(menu)])
    assert rc == 0
    rid = capsys.readouterr().out.strip().splitlines()[-1]
    row = store.get(rid)
    assert row["kind"] == "menu"
    assert json.loads(row["menu"]) == menu
    assert json.loads(row["options"]) == ["Red", "Type something."]  # labels for legacy renderers

def test_cli_request_bad_menu_json_errors(store, monkeypatch):
    import approval
    monkeypatch.setattr(approval, "_store", lambda: store)
    monkeypatch.setattr(approval, "notify", lambda rid, store=None: None)
    rc = approval.main(["request", "q", "--from", "bridge", "--worker-kind", "pane",
                        "--menu-json", "{not json"])
    assert rc != 0


# --- F6-polish-1: resolve_pending_menus_for_session (2026-08-25) ---------------
# In-agent MULTIPART menus are answered pane-bound, so the mirror ledger row
# lingers pending until the ~60s reconcile cron. A verified armed submit calls
# this to resolve it immediately. Must match reconcile_orphans' guard exactly
# (kind='menu' AND status='pending') so the two are idempotent, and scope to the
# session by op_key prefix without letting LIKE metacharacters act as wildcards.

def _mk_menu(store, session, hexs, kind="menu"):
    # origin='menu_bridge' models a genuine bridge mirror row (post
    # DEC-1788138920 C1 every bridge_one row is stamped; legacy rows are
    # backfilled). The per-session resolver is provenance-gated to these.
    return store.create(from_agent=session, question="which signals?",
                        op_key=f"menu:{session}:{hexs}", worker_kind="pane",
                        kind=kind, menu={"options": [{"n": "1", "label": "A"}]},
                        origin="menu_bridge")

def test_resolve_pending_menus_only_live_pending_row(store):
    sess = "parity-canary-gemini"
    live = _mk_menu(store, sess, "aaa")
    other = _mk_menu(store, "other-sess", "bbb")
    answered = _mk_menu(store, sess, "ccc"); store.record_answer(answered, "option", None, option_n="1")
    nonmenu = _mk_menu(store, sess, "ddd", kind="approval")
    n = store.resolve_pending_menus_for_session(sess)
    assert n == 1
    assert store.get(live)["status"] == "resolved_elsewhere"
    assert store.get(other)["status"] == "pending"        # different session untouched
    assert store.get(answered)["status"] == "answered"    # only pending rows move
    assert store.get(nonmenu)["status"] == "pending"      # only kind='menu' moves

def test_resolve_pending_menus_idempotent_with_reconcile(store):
    sess = "sess-a"
    rid = _mk_menu(store, sess, "eee")
    assert store.resolve_pending_menus_for_session(sess) == 1
    assert store.resolve_pending_menus_for_session(sess) == 0   # second call no-ops

def test_resolve_pending_menus_like_metachars_are_literal(store):
    u = _mk_menu(store, "sess_x", "f01")     # underscore = SQL LIKE single-char wildcard
    uw = _mk_menu(store, "sessYx", "f02")    # would match if '_' not escaped
    assert store.resolve_pending_menus_for_session("sess_x") == 1
    assert store.get(u)["status"] == "resolved_elsewhere"
    assert store.get(uw)["status"] == "pending"
