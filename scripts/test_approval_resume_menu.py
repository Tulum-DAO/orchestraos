"""Phase 2 (app-watch-decision-surface §1a + AGY #1): approval_resume kind=='menu'
branch — keypress/three-phase transport, NOT msg_store text. All tmux/detector
seams mocked; nothing here touches a live pane."""
import os, sys, json, types, pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore
import approval_resume
import watch_gateway as G


_MENU = {
    "question": "Which color?",
    "options": [
        {"n": "1", "label": "Red", "input_kind": "direct"},
        {"n": "2", "label": "Green", "input_kind": "direct"},
        {"n": "3", "label": "Type something.", "input_kind": "free_text"},
    ],
    "selected_n": "1",
    "source_session": "acme-dev",
    "captured_at": 1786520000.0,
}


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db"))
    s.migrate()
    return s


def _mk_menu_row(store, option_n="2", answer_text=None):
    rid = store.create(from_agent="ag1", question=_MENU["question"],
                       worker_kind="pane", kind="menu", menu=_MENU,
                       options=[o["label"] for o in _MENU["options"]])
    store.record_answer(rid, "option", answer_text, option_n=option_n)
    return rid


class _FakeTransport(types.SimpleNamespace):
    """Stands in for watch_gateway's menu transport functions."""
    def __init__(self, key_result=(True, {}), text_result=(True, {})):
        super().__init__()
        self.key_calls, self.text_calls = [], []
        self._key_result, self._text_result = key_result, text_result

    def menu_resume_keypress(self, session, key, expect_question=None):
        self.key_calls.append((session, key, expect_question))
        return self._key_result

    def menu_resume_free_text(self, session, key, text, expect_question=None):
        self.text_calls.append((session, key, text, expect_question))
        return self._text_result


@pytest.fixture
def no_msg_store(monkeypatch):
    """Menu rows must NEVER ride the msg_store text path (AGY #1)."""
    def boom():
        raise AssertionError("msg_store used for a menu row")
    monkeypatch.setattr(approval_resume, "_msg_store", boom)


@pytest.fixture
def live_source(monkeypatch):
    """The bridged-menu KEYPRESS path (DEC-1786664626) requires a LIVE source
    pane. These tests exercise that path, so mark the source session live."""
    monkeypatch.setattr(approval_resume, "_session_is_live",
                        lambda session, live_fn=None: True)


# ---------------- store: new terminal states ----------------
def test_mark_resolved_elsewhere_from_answered(store):
    rid = _mk_menu_row(store)
    assert store.mark_resolved_elsewhere(rid) is True
    assert store.get(rid)["status"] == "resolved_elsewhere"
    assert store.mark_resolved_elsewhere(rid) is False   # once


def test_mark_resume_failed_records_error(store):
    rid = _mk_menu_row(store)
    assert store.mark_resume_failed(rid, "phase 2 failed: composer holds text") is True
    row = store.get(rid)
    assert row["status"] == "resume_failed"
    assert "phase 2" in row["resume_error"]


# ---------------- fire_resume: menu branch ----------------
def test_direct_menu_fires_keypress_not_msg_store(store, monkeypatch, no_msg_store, live_source):
    rid = _mk_menu_row(store, option_n="2")
    t = _FakeTransport(key_result=(True, {}))
    monkeypatch.setattr(approval_resume, "_menu_transport", lambda: t)
    approval_resume.fire_resume(store.get(rid), store)
    assert t.key_calls == [("acme-dev", "2", "Which color?")]
    assert t.text_calls == []
    row = store.get(rid)
    assert row["status"] == "resumed"          # keypress resolves the menu; self-ack
    assert row["resumed_at"] and row["resume_attempts"] == 1


def test_free_text_menu_fires_three_phase(store, monkeypatch, no_msg_store, live_source):
    rid = _mk_menu_row(store, option_n="3", answer_text="use the blue variant")
    t = _FakeTransport(text_result=(True, {}))
    monkeypatch.setattr(approval_resume, "_menu_transport", lambda: t)
    approval_resume.fire_resume(store.get(rid), store)
    assert t.text_calls == [("acme-dev", "3", "use the blue variant", "Which color?")]
    assert t.key_calls == []
    assert store.get(rid)["status"] == "resumed"


def test_menu_gone_fires_durable_fallback(store, monkeypatch, live_source):
    """R7b (the operator ruling 2026-08-25, supersedes the old resolved_elsewhere DROP):
    menu_gone means the keypress delivery is NOT registered with the agent, so the
    recorded answer is delivered DURABLY (msg_store row + best-effort live-head
    inject) via the _fire_menu_inject fallback — never silently dropped. See
    scripts/test_menu_gone_fallback.py for the full fallback contract."""
    sent = []
    monkeypatch.setattr(approval_resume, "_fallback_msg_send",
                        lambda row, body: sent.append(row["id"]))
    monkeypatch.setattr(approval_resume, "_default_resolve",
                        lambda aid: ("ag1", "direct-live"))
    monkeypatch.setattr(approval_resume, "_default_inject", lambda s, t: (True, {}))
    rid = _mk_menu_row(store, option_n="2")
    t = _FakeTransport(key_result=(False, {"reason": "menu_gone"}))
    monkeypatch.setattr(approval_resume, "_menu_transport", lambda: t)
    approval_resume.fire_resume(store.get(rid), store)
    assert rid in sent                                    # answer delivered durably
    assert store.get(rid)["status"] != "resolved_elsewhere"
    assert store.get(rid)["status"] == "resumed"          # inject succeeded -> self-ack


def test_phase_failure_marks_resume_failed_honestly(store, monkeypatch, no_msg_store, live_source):
    rid = _mk_menu_row(store, option_n="3", answer_text="typed body")
    t = _FakeTransport(text_result=(False, {"reason": "unverified_submit", "phase": 3}))
    monkeypatch.setattr(approval_resume, "_menu_transport", lambda: t)
    approval_resume.fire_resume(store.get(rid), store)
    row = store.get(rid)
    assert row["status"] == "resume_failed"
    assert "unverified_submit" in (row["resume_error"] or "")


def test_menu_row_without_session_injects_to_live_head(store, monkeypatch):
    """DEC-1786664626 Q1: an AUTHORED menu decision (no source_session) no longer
    fails — it resolves from_agent -> live head and verified-injects the answer
    digest + writes the durable msg_store row. Menu transport must NOT be used."""
    menu = dict(_MENU); menu.pop("source_session")
    rid = store.create(from_agent="ag1", question="q", worker_kind="pane",
                       kind="menu", menu=menu, options=["Red"])
    store.record_answer(rid, "option", None, option_n="1")
    monkeypatch.setattr(approval_resume, "_menu_transport",
                        lambda: (_ for _ in ()).throw(AssertionError("keypress on authored menu")))
    sent, injected = {}, {}
    fake_ms = types.SimpleNamespace(send=lambda **kw: sent.update(kw))
    monkeypatch.setattr(approval_resume, "_msg_store", lambda: fake_ms)
    monkeypatch.setattr(approval_resume, "_default_resolve",
                        lambda aid: ("ag1", "direct-live"))
    monkeypatch.setattr(approval_resume, "_default_inject",
                        lambda s, t: injected.update(session=s, text=t) or (True, {}))
    approval_resume.fire_resume(store.get(rid), store)
    assert injected["session"] == "ag1"                 # injected into live head
    assert "Red" in injected["text"]                     # answer digest = option label
    assert sent["to_agent"] == "ag1"                     # durable row ALSO written
    assert store.get(rid)["status"] == "resumed"         # not resume_failed


def test_menu_rows_never_refire_from_watchdog(store, monkeypatch, no_msg_store, live_source):
    # at-most-once: success AND failure both leave answered_unacked() empty
    ok_rid = _mk_menu_row(store, option_n="2")
    t = _FakeTransport(key_result=(True, {}))
    monkeypatch.setattr(approval_resume, "_menu_transport", lambda: t)
    approval_resume.fire_resume(store.get(ok_rid), store)
    fail_rid = _mk_menu_row(store, option_n="2")
    monkeypatch.setattr(approval_resume, "_menu_transport",
                        lambda: _FakeTransport(key_result=(False, {"reason": "send_failed"})))
    approval_resume.fire_resume(store.get(fail_rid), store)
    assert store.answered_unacked() == []


def test_non_menu_pane_row_keeps_msg_store_path(store, monkeypatch):
    rid = store.create(from_agent="ag1", question="deploy?", worker_kind="pane")
    store.record_answer(rid, "approve", None)
    sent = {}
    fake_ms = types.SimpleNamespace(send=lambda **kw: sent.update(kw))
    monkeypatch.setattr(approval_resume, "_msg_store", lambda: fake_ms)
    monkeypatch.setattr(approval_resume, "_menu_transport",
                        lambda: (_ for _ in ()).throw(AssertionError("menu transport on plain row")))
    approval_resume.fire_resume(store.get(rid), store)
    assert sent["to_agent"] == "ag1" and "approve" in sent["body"]
    assert store.get(rid)["status"] == "answered"   # waits for agent ack, as today


# ---------------- gateway transport: menu_resume_keypress ----------------
class _SeqDetector:
    """Detector whose pending_menu evolves across get_agent_status calls."""
    def __init__(self, statuses):
        self._seq = list(statuses)
    def get_agent_status(self, sess):
        return self._seq.pop(0) if len(self._seq) > 1 else self._seq[0]


class _FakeTmux:
    def __init__(self):
        self.calls = []
    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="")


def _transport_env(monkeypatch, statuses, sessions=("acme-dev",)):
    fake = _FakeTmux()
    det = _SeqDetector(statuses)   # ONE stateful detector across calls
    monkeypatch.setattr(G, "_tmux", fake)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: list(sessions))
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return fake


_PM = {"kind": "options", "question": "Which color?", "options": [], "selected_n": "1"}


def test_transport_keypress_commits_with_enter_and_verifies(monkeypatch):
    # 2.1.260 Claude AskUserQuestion commits on Enter ('Enter to select' footer),
    # not on the bare digit. The direct answer path (commit=True default) must
    # send digit+Enter AND verify the menu actually resolved before reporting ok.
    fake = _transport_env(monkeypatch, [
        {"state": "idle", "pending_menu": _PM},   # phase-1 gate: menu present
        {"state": "idle"},                        # post-commit: menu resolved
    ])
    ok, info = G.menu_resume_keypress("acme-dev", "2", expect_question="Which color?")
    assert ok and info.get("verified") is True
    sk = [c for c in fake.calls if c[0] == "send-keys"]
    assert sk == [("send-keys", "-t", "acme-dev", "2", "Enter")]   # digit + Enter, one send


def test_transport_keypress_unverified_when_menu_stays(monkeypatch):
    # THE BUG: a digit-only keypress leaves the menu uncommitted, yet the old
    # code reported ok=True (falsely 'delivered'). After the fix, an un-resolved
    # menu returns an HONEST failure (unverified_submit) — never a false stamp.
    fake = _transport_env(monkeypatch, [{"state": "idle", "pending_menu": _PM}])  # never resolves
    ok, info = G.menu_resume_keypress("acme-dev", "2", expect_question="Which color?")
    assert not ok and info["reason"] == "unverified_submit"


def test_transport_keypress_free_text_phase1_is_digit_only(monkeypatch):
    # commit=False (menu_resume_free_text phase-1) must send the digit ALONE —
    # a blanket Enter here would submit the 'Type something' option with no body.
    fake = _transport_env(monkeypatch, [{"state": "idle", "pending_menu": _PM}])
    ok, info = G.menu_resume_keypress("acme-dev", "4", expect_question="Which color?",
                                      commit=False)
    assert ok
    sk = [c for c in fake.calls if c[0] == "send-keys"]
    assert sk == [("send-keys", "-t", "acme-dev", "4")]   # digit only, NO Enter, NO verify


def test_transport_keypress_menu_gone(monkeypatch):
    fake = _transport_env(monkeypatch, [{"state": "idle"}])
    ok, info = G.menu_resume_keypress("acme-dev", "2", expect_question="Which color?")
    assert not ok and info["reason"] == "menu_gone"
    assert not any(c[0] == "send-keys" for c in fake.calls)


def test_transport_keypress_different_menu_refuses(monkeypatch):
    # A DIFFERENT menu is now on screen — injecting would answer the wrong one.
    other = dict(_PM, question="Deploy to prod?")
    fake = _transport_env(monkeypatch, [{"state": "idle", "pending_menu": other}])
    ok, info = G.menu_resume_keypress("acme-dev", "2", expect_question="Which color?")
    assert not ok and info["reason"] == "menu_gone"
    assert not any(c[0] == "send-keys" for c in fake.calls)


def test_transport_keypress_no_session(monkeypatch):
    _transport_env(monkeypatch, [{"state": "idle", "pending_menu": _PM}], sessions=())
    ok, info = G.menu_resume_keypress("acme-dev", "2")
    assert not ok and info["reason"] == "no_session"


def test_transport_free_text_three_phase(monkeypatch):
    # menu present at phase 1, gone after Enter -> verified resolved
    fake = _transport_env(monkeypatch, [
        {"state": "idle", "pending_menu": _PM},   # phase-1 gate
        {"state": "idle"},                        # post-Enter verify: menu resolved
    ])
    ok, info = G.menu_resume_free_text("acme-dev", "3", "typed body",
                                       expect_question="Which color?")
    assert ok
    sk = [c for c in fake.calls if c[0] == "send-keys"]
    assert sk[0] == ("send-keys", "-t", "acme-dev", "3")                 # digit
    assert sk[1] == ("send-keys", "-t", "acme-dev", "-l", "typed body")  # literal text
    assert sk[2] == ("send-keys", "-t", "acme-dev", "Enter")             # submit


def test_transport_free_text_unresolved_menu_fails(monkeypatch):
    # menu still on screen after Enter (+retry) -> honest phase-3 failure
    fake = _transport_env(monkeypatch, [{"state": "idle", "pending_menu": _PM}])
    ok, info = G.menu_resume_free_text("acme-dev", "3", "typed body",
                                       expect_question="Which color?")
    assert not ok and info["reason"] == "unverified_submit" and info["phase"] == 3
