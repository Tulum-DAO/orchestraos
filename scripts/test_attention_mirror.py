import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attention_store as S  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    return str(tmp_path / "tasks.db")


@pytest.fixture
def ledger(db):
    st = ApprovalStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def store(db):
    st = S.AttentionStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def unarmed_store(tmp_path, monkeypatch):
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    st = S.AttentionStore(db_path=str(tmp_path / "u.db")); st.migrate()
    return st


def test_every_pending_completion_card_gets_one_done_event_and_stays_pending(ledger, store):
    a = ledger.create(from_agent="gm", question="Voice panel live", worker_kind="pane",
                      options=["accept", "respond"], kind="completion",
                      evidence={"tests": "12 passed", "urls": [], "verified": True})
    done = ledger.create(from_agent="gm", question="Old", worker_kind="pane",
                         options=["accept", "respond"], kind="completion", evidence={})
    ledger.record_answer(done, "accept", None, surface="watch", answered_by="operator")
    ledger.create(from_agent="gm", question="Not a completion", worker_kind="pane")
    plan = S.mirror_completions(store, ledger, dry_run=True)
    assert [p["source_id"] for p in plan] == [a] and store.list_events() == []
    S.mirror_completions(store, ledger)
    S.mirror_completions(store, ledger)                     # idempotent
    evs = store.list_events()
    assert len(evs) == 1 and evs[0]["source_id"] == a and evs[0]["kind"] == "done"
    assert evs[0]["created_at"] == ledger.get(a)["created_at"]
    assert ledger.get(a)["status"] == "pending"            # never dropped from /pending-approvals
    assert json.loads(evs[0]["evidence"])["tests"] == "12 passed"
    assert S.mirror_completions(store, ledger, dry_run=True) == []   # nothing left to plan


def test_mirror_unarmed_is_a_noop(ledger, unarmed_store):
    assert S.mirror_completions(unarmed_store, ledger) == []


def test_cli_dry_run_then_apply(db, ledger, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_DIR", str(Path(db).parent.parent))
    monkeypatch.setattr(S.approval_config, "db_path", lambda: Path(db))
    ledger.create(from_agent="gm", question="Shipped", worker_kind="pane",
                  options=["accept", "respond"], kind="completion", evidence={})
    assert S.main(["mirror-completions", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would mirror" in out and out.strip().endswith("1 cards")
    assert S.main(["mirror-completions"]) == 0
    assert capsys.readouterr().out.strip().endswith("1 cards")
    assert S.main(["mirror-completions", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "0 cards"


def test_cli_migrate_creates_the_tables_and_seeds_the_install_persons_defaults(db, monkeypatch, capsys):
    import notify_policy
    from attention_rules import RuleStore
    monkeypatch.setattr(S.approval_config, "db_path", lambda: Path(db))
    monkeypatch.setattr(notify_policy, "install_user_and_zone", lambda raw=None: ("alex", "UTC"))
    assert S.main(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "attention_events" in out and "default rules seeded for alex" in out
    st = S.AttentionStore(db_path=db); st.migrate()
    assert len(RuleStore(st).list("alex")) == 2
    assert S.main(["migrate"]) == 0
    assert "none (already present)" in capsys.readouterr().out
    assert len(RuleStore(st).list("alex")) == 2
