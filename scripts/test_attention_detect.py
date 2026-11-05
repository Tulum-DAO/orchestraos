"""Detector transitions. The headless usage-limit / sign-in fixtures follow the Claude Code stream-json
`result` event shape ({type: result, subtype, is_error, result, session_id}); no such error has been seen
live yet, so they are built from that shape, not captured."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_config as AC  # noqa: E402
import attention_detect as D  # noqa: E402
import attention_store as S  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
cfg = AC.load({})


def iso(t):
    return t.isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    return str(tmp_path / "tasks.db")


@pytest.fixture
def store(db):
    st = S.AttentionStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def ledger(db):
    st = ApprovalStore(db_path=db); st.migrate()
    return st


def test_conditions_from_agents_rows():
    rows = [{"id": "gm", "state": "crashed"}, {"id": "qa", "state": "stalled", "state_age_s": 700},
            {"id": "dev", "state": "stalled", "state_age_s": 300}, {"id": "pm", "state": "working"}]
    conds = D.from_agents(rows, cfg)
    assert {c.key for c in conds} == {"failed:gm:crashed"}       # stalls are observations for the rules, not events
    assert conds[0].title == "gm stopped (crashed)" and conds[0].source == "agents"
    assert D.from_agents(rows, AC.load({"attention": {"failed_reasons": ["usage_limit"]}})) == []


def test_screen_and_headless_classes():
    assert D.from_screen("gm", "You've hit your usage limit · resets 5pm").key == "failed:gm:usage_limit"
    assert D.from_screen("gm", "Select login method:").key == "failed:gm:signed_out"
    assert D.from_screen("gm", "Select login method:").source == "pane:gm"
    assert D.from_screen("gm", "all good\n❯ ") is None
    assert D.from_screen("gm", "● the docs mention a usage limit reached banner\n❯ ") is None   # prose never counts
    assert D.from_headless_result("gm", {"type": "result", "is_error": True,
                                         "result": "Not logged in. Please run /login"}).key == "failed:gm:signed_out"
    assert D.from_headless_result("gm", {"type": "result", "is_error": True, "subtype": "success",
                                         "result": "Claude AI usage limit reached|1762300800"}).key \
        == "failed:gm:usage_limit"
    assert D.from_headless_result("gm", {"type": "result", "is_error": False, "result": "ok"}) is None
    assert D.from_headless_result("gm", {"type": "result", "is_error": True, "result": "some tool failed"}) is None


def _write_events(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_headless_error_from_an_older_session_is_ignored(tmp_path):
    p = tmp_path / "events.jsonl"
    old_err = {"t": 1, "gen": 1, "kind": "out", "ev": {"type": "result", "is_error": True, "session_id": "old",
                                                        "result": "Not logged in. Please run /login"}}
    start = {"t": 2, "gen": 2, "kind": "sup", "what": "start", "pid": 5}
    ok = {"t": 3, "gen": 2, "kind": "out", "ev": {"type": "result", "is_error": False, "session_id": "cur",
                                                   "result": "done"}}
    _write_events(p, [old_err, start])
    assert D.last_headless_result(p, session_id="cur") is None
    _write_events(p, [start, old_err])                              # same CLI, but another session's result
    assert D.last_headless_result(p, session_id="cur") is None
    new_err = {"t": 4, "gen": 2, "kind": "out", "ev": {**old_err["ev"], "session_id": "cur"}}
    _write_events(p, [old_err, start, new_err])
    assert D.last_headless_result(p, session_id="cur")["is_error"] is True
    _write_events(p, [old_err, start, new_err, ok])                 # a later success clears it
    assert D.last_headless_result(p, session_id="cur")["is_error"] is False
    _write_events(p, [start, new_err, {"t": 9, "gen": 3, "kind": "sup", "what": "start"}])   # the CLI restarted
    assert D.last_headless_result(p, session_id="cur") is None
    assert D.last_headless_result(tmp_path / "missing.jsonl", session_id="cur") is None


def test_observations_carry_since_and_no_threshold():
    w = [{"id": "apr_1", "agent": "gm", "blocking": True, "created_at": iso(NOW - timedelta(minutes=61))}]
    assert D.unanswered(w) == [D.Unanswered(item=w[0], since=NOW - timedelta(minutes=61))]
    rows = [{"id": "qa", "state": "stalled", "state_age_s": 700}, {"id": "pm", "state": "working"}]
    assert D.no_progress(rows, NOW, stall_s=600) == [D.NoProgress("qa", NOW - timedelta(seconds=1300))]
    assert D.unanswered([{"id": "x", "created_at": "garbage"}]) == []


def test_tick_opens_once_clears_after_streak_and_resolves_questions_at_once(store, ledger):
    c = D.Condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped")
    q = D.Condition("stuck", "gm", "question_unanswered", "stuck:gm:question:apr_1", "gm waiting 1 h",
                    source_id="apr_1", source="ledger")
    D.apply_tick(store, [c, q], NOW, cfg, live_agents={"gm"}, ledger=ledger)
    D.apply_tick(store, [c, q], NOW, cfg, live_agents={"gm"}, ledger=ledger)
    assert len(store.open_events()) == 2
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, ledger=ledger)   # streak 1: crashed open, question gone
    assert {e["dedup_key"] for e in store.open_events()} == {"failed:gm:crashed"}
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, ledger=ledger)   # streak 2: resolved
    assert store.open_events() == []
    assert store.get_by_key_any("failed:gm:crashed")["resolved_reason"] == "cleared"


def test_a_failed_scan_never_clears_anything(store):
    c = D.Condition("failed", "gm", "usage_limit", "failed:gm:usage_limit", "gm is out of usage", source="pane:gm")
    D.apply_tick(store, [c], NOW, cfg, live_agents={"gm"}, scans={"pane:gm": "ok"})
    for _ in range(5):
        D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"pane:gm": "failed"})
    assert store.get_by_key_any("failed:gm:usage_limit")["resolved_at"] is None
    for _ in range(5):                                         # not scanned at all (agent working): untouched too
        D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"agents": "ok"})
    assert store.get_by_key_any("failed:gm:usage_limit")["resolved_at"] is None
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"pane:gm": "ok"})
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"pane:gm": "ok"})
    assert store.get_by_key_any("failed:gm:usage_limit")["resolved_reason"] == "cleared"


def test_question_resolves_from_the_ledger_not_from_absence(store, ledger):
    rid = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    q = D.Condition("stuck", "gm", "question_unanswered", f"stuck:gm:question:{rid}", "x", source_id=rid,
                    source="ledger")
    D.apply_tick(store, [q], NOW, cfg, live_agents={"gm"}, scans={"ledger": "ok"}, ledger=ledger)
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"ledger": "failed"}, ledger=ledger)
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"ledger": "ok"}, ledger=ledger)
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"ledger": "ok"}, ledger=ledger)
    assert store.open_events()                                  # still pending in the ledger -> open
    ledger.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    D.apply_tick(store, [], NOW, cfg, live_agents={"gm"}, scans={"ledger": "failed"}, ledger=ledger)
    assert store.open_events() == []                            # authoritative status, not the scan
    assert store.get_by_key_any(f"stuck:gm:question:{rid}")["resolved_reason"] == "answered"


def test_rule_made_no_progress_rows_never_resolve_from_absence(store):
    D.apply_tick(store, [D.Condition("stuck", "qa", "no_progress", "stuck:qa:no_progress", "x")], NOW, cfg,
                 live_agents={"qa"})
    for _ in range(4):
        D.apply_tick(store, [], NOW, cfg, live_agents={"qa"})
    assert store.get_by_key_any("stuck:qa:no_progress")["resolved_at"] is None


def test_agent_gone_resolves(store):
    D.apply_tick(store, [D.Condition("stuck", "old", "no_progress", "stuck:old:no_progress", "x")], NOW, cfg,
                 live_agents={"old"})
    D.apply_tick(store, [], NOW, cfg, live_agents=set(), scans={"agents": "failed"})   # a failed scan proves nothing
    assert store.get_by_key_any("stuck:old:no_progress")["resolved_at"] is None
    D.apply_tick(store, [], NOW, cfg, live_agents=set())
    assert store.get_by_key_any("stuck:old:no_progress")["resolved_reason"] == "agent_gone"
