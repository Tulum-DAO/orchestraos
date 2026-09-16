"""S3 item 2 — Telegram notify channel for approvals/questionnaires (Universal
Decision-Surface Pipeline spec §2.4, build guard §3.1-c).

Every newly-surfaced row fires a Telegram ping alongside ntfy. PER-CHANNEL
dedup: a failed channel retries WITHOUT re-sending the succeeded one. Telegram's
success is tracked in a sidecar (independent of the row's notified_at, which is
ntfy's dedup marker). send/state injected — no real tmux/network.
"""
import os, sys, json
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_notify as N


@pytest.fixture
def statef(tmp_path):
    return str(tmp_path / "tg-state.json")


def test_telegram_sends_once_then_dedups(statef):
    sent = []
    ok = N.notify_telegram("approval:a1", "hi", state_path=statef,
                           send=lambda t: (sent.append(t), True)[1])
    assert ok is True and sent == ["hi"]
    ok2 = N.notify_telegram("approval:a1", "hi", state_path=statef,
                            send=lambda t: (sent.append(t), True)[1])
    assert ok2 is True and sent == ["hi"]          # deduped — no second send


def test_telegram_failure_not_stamped_retries(statef):
    calls = {"n": 0}
    def flaky(_t):
        calls["n"] += 1
        return calls["n"] > 1                        # first fails, second ok
    ok1 = N.notify_telegram("approval:a1", "hi", state_path=statef, send=flaky)
    assert ok1 is False
    st = N._load_tg_state(statef)
    assert not st.get("approval:a1", {}).get("sent_at")   # not stamped -> retriable
    ok2 = N.notify_telegram("approval:a1", "hi", state_path=statef, send=flaky)
    assert ok2 is True and calls["n"] == 2


def test_telegram_exception_is_retryable(statef):
    def boom(_t): raise RuntimeError("net down")
    ok = N.notify_telegram("approval:a1", "hi", state_path=statef, send=boom)
    assert ok is False
    assert "approval:a1" not in N._load_tg_state(statef)


def test_cron_fires_telegram_even_when_ntfy_done(tmp_path, monkeypatch):
    # per-channel independence: a row whose ntfy already fired inline (notified_at
    # set) still gets its Telegram ping, and ntfy is NOT re-sent.
    from approval_schema import ApprovalStore
    from questionnaire_schema import QuestionnaireStore
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    rid = s.create(from_agent="worker", question="Ship it?", worker_kind="dev",
                   options=["approve", "deny", "hold"])
    s.set_notified(rid)                              # ntfy done inline
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    ntfy_calls = []
    monkeypatch.setattr(N, "notify", lambda rid, store=None: ntfy_calls.append(rid))
    tg = []
    N.cron_backstop(store=s, qstore=qs,
                    tg_send=lambda t: (tg.append(t), True)[1],
                    tg_state_path=str(tmp_path / "tg.json"))
    assert ntfy_calls == []                          # notified_at set -> ntfy not re-sent
    assert len(tg) == 1 and "worker" in tg[0]        # telegram fired independently


def test_cron_telegram_deduped_across_beats(tmp_path, monkeypatch):
    from approval_schema import ApprovalStore
    from questionnaire_schema import QuestionnaireStore
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    s.create(from_agent="worker", question="Q?", worker_kind="dev",
             options=["approve", "deny", "hold"])
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    monkeypatch.setattr(N, "notify", lambda rid, store=None: None)
    tg = []
    path = str(tmp_path / "tg.json")
    N.cron_backstop(store=s, qstore=qs, tg_send=lambda t: (tg.append(t), True)[1],
                    tg_state_path=path)
    N.cron_backstop(store=s, qstore=qs, tg_send=lambda t: (tg.append(t), True)[1],
                    tg_state_path=path)
    assert len(tg) == 1                              # one Telegram per row across beats


def test_cron_prunes_answered_keys(tmp_path, monkeypatch):
    from approval_schema import ApprovalStore
    from questionnaire_schema import QuestionnaireStore
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    rid = s.create(from_agent="worker", question="Q?", worker_kind="dev",
                   options=["approve", "deny", "hold"])
    qs = QuestionnaireStore(db_path=db); qs.migrate()
    monkeypatch.setattr(N, "notify", lambda rid, store=None: None)
    path = str(tmp_path / "tg.json")
    N.cron_backstop(store=s, qstore=qs, tg_send=lambda t: True, tg_state_path=path)
    assert f"approval:{rid}" in N._load_tg_state(path)
    s.record_answer(rid, "approve", None)
    N.cron_backstop(store=s, qstore=qs, tg_send=lambda t: True, tg_state_path=path)
    assert f"approval:{rid}" not in N._load_tg_state(path)   # pruned when no longer pending
