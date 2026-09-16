"""Fail-closed guard: the resume path's REAL side-effect seams (durable msg_store
send + live pane inject) refuse to run for a store that is not the canonical
DB (gm msg_3477cbfb, incident apr_07805db5).

Incident: a by-effect probe fed approval_listener.handle_event a synthetic
answer against a TEMPORARY sqlite store whose row named a LIVE agent
(from_agent=pm-aiordie). record_answer -> fire_resume -> the real seams
delivered a "[DECISION ANSWERED ...] -> Approve both" digest into the live
pane; the agent acted on it. Nothing scoped side effects to the store.

Guard: fire_resume/_fire_* stamp the ACTIVE store; _default_msg_send (real
factory only) and _default_inject refuse when that store's db_path != the
canonical approval_config.DB_PATH — unless APPROVAL_RESUME_ALLOW_NONCANONICAL=1.
Belt: APPROVAL_RESUME_BLOCK_REAL_SEAMS=1 (conftest default) refuses the real
seams under ANY pytest run, canonical or not. Hermetic tests that monkeypatch
_msg_store / _default_inject are untouched (patched seam != real seam).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_resume as R
import approval_config as C
from approval_schema import ApprovalStore

AGENT = "zz-guard-fixture-agent"        # never a live seat


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    # Spies that KEEP "real seam" identity: the factory spy is installed as
    # both the module name and the captured real reference, so the guard
    # treats it as the real escape; injects are spied at the gateway seam.
    sent, injected = [], []
    class SpyMsgStore:
        def send(self, **kw): sent.append(kw); return "msg_spy"
    factory = lambda: SpyMsgStore()
    monkeypatch.setattr(R, "_msg_store", factory)
    monkeypatch.setattr(R, "_REAL_MSG_STORE_FACTORY", factory)
    import watch_gateway
    monkeypatch.setattr(watch_gateway, "verified_inject", lambda sess, text: (injected.append((sess, text)), (True, {}))[1])
    monkeypatch.setattr(R, "_default_resolve", lambda a: (a, "live"))
    monkeypatch.setenv("ANSWER_TELEMETRY_PATH", str(tmp_path / "telemetry.jsonl"))
    monkeypatch.delenv("APPROVAL_RESUME_BLOCK_REAL_SEAMS", raising=False)
    monkeypatch.delenv("APPROVAL_RESUME_ALLOW_NONCANONICAL", raising=False)
    return {"store": s, "db": db, "sent": sent, "injected": injected, "tmp": tmp_path}


def _answered_pane_row(env):
    s = env["store"]
    rid = s.create(from_agent=AGENT, question="Land it?", worker_kind="pane", thread_key=AGENT)
    s.record_answer(rid, "approve", None)
    return rid


def test_noncanonical_store_refuses_real_seams(env, capsys):
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"])
    assert env["sent"] == [] and env["injected"] == []          # RED today: both fire
    assert "noncanonical_store_refused" in capsys.readouterr().err
    assert env["store"].get(rid)["status"] != "resumed"


def test_canonical_store_is_allowed(env, monkeypatch):
    monkeypatch.setattr(C, "DB_PATH", env["db"])
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"])
    assert len(env["sent"]) == 1 and env["sent"][0]["to_agent"] == AGENT
    assert len(env["injected"]) == 1


def test_explicit_allow_env_overrides(env, monkeypatch):
    monkeypatch.setenv("APPROVAL_RESUME_ALLOW_NONCANONICAL", "1")
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"])
    assert len(env["sent"]) == 1 and len(env["injected"]) == 1


def test_block_env_refuses_even_canonical(env, monkeypatch):
    monkeypatch.setattr(C, "DB_PATH", env["db"])
    monkeypatch.setenv("APPROVAL_RESUME_BLOCK_REAL_SEAMS", "1")
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"])
    assert env["sent"] == [] and env["injected"] == []


def test_conftest_belt_is_on_under_pytest():
    assert os.environ.get("APPROVAL_RESUME_BLOCK_REAL_SEAMS") == "1"
    assert "answer-telemetry-TESTS" in (os.environ.get("ANSWER_TELEMETRY_PATH") or "")


def test_the_incident_shape_listener_on_tmp_store_is_inert(env, capsys):
    """apr_07805db5 replayed: handle_event on a tmp store naming an agent."""
    import json
    import approval_listener as L
    s = env["store"]
    menu = {"question": "Q?", "options": [{"n": "1", "label": "Approve both", "input_kind": "direct"},
                                          {"n": "2", "label": "Hold", "input_kind": "direct"}]}
    rid = s.create(from_agent=AGENT, question="Q?", worker_kind="pane", kind="menu", menu=menu,
                   options=["Approve both", "Hold"])
    L.handle_event({"event": "message", "message": json.dumps({"id": rid, "answer": "Approve both"})}, store=s)
    assert env["sent"] == [] and env["injected"] == []
    assert s.get(rid)["status"] != "resumed"
    assert "noncanonical_store_refused" in capsys.readouterr().err


def test_menu_and_human_task_lanes_guarded_too(env):
    s = env["store"]
    m = s.create(from_agent=AGENT, question="Which?", worker_kind="pane", kind="menu",
                 menu={"question": "Which?", "options": [{"n": "1", "label": "A"}]}, options=["A"])
    s.record_answer(m, "option", None) if False else None
    R.apply = None
    # menu row answered via the option contract
    import watch_gateway as G
    G.apply_answer(s, m, "option", option_n="1", fire=True)
    h = s.create(from_agent=AGENT, question="Plug in", worker_kind="pane", kind="human_task",
                 options=["done", "cant", "snooze"])
    s.record_answer(h, "done", None)
    R.fire_resume(s.get(h), s)
    assert env["sent"] == [] and env["injected"] == []


def test_hermetic_tests_with_patched_seams_still_work(env, monkeypatch):
    """The existing suites' style: patch _msg_store (a FAKE factory, not the
    real one) — the guard must not interfere with a fake seam."""
    fake_sent = []
    class Fake:
        def send(self, **kw): fake_sent.append(kw); return "m"
    monkeypatch.setattr(R, "_msg_store", lambda: Fake())        # != _REAL_MSG_STORE_FACTORY
    monkeypatch.setattr(R, "_default_inject", lambda sess, text: (True, {}))
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"])
    assert len(fake_sent) == 1


def test_explicit_allow_noncanonical_kwarg(env):
    """gm's spec names an explicit opt-in: fire_resume(..., allow_noncanonical=True)
    lets a deliberate caller run the real seams against a non-canonical store;
    the opt-in is logged and does not persist past the call."""
    rid = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid), env["store"], allow_noncanonical=True)
    assert len(env["sent"]) == 1 and len(env["injected"]) == 1
    rid2 = _answered_pane_row(env)
    R.fire_resume(env["store"].get(rid2), env["store"])            # default again
    assert len(env["sent"]) == 1 and len(env["injected"]) == 1      # refused: opt-in did not leak
