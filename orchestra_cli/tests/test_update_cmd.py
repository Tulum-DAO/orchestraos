"""`orchestra update done`: an agent posts a finished piece of work to the person's Updates list."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from orchestra_cli import update_cmd as U  # noqa: E402


def _stores(db):
    import attention_store as S
    from approval_schema import ApprovalStore
    a = S.AttentionStore(db_path=db); a.migrate()
    ledger = ApprovalStore(db_path=db); ledger.migrate()
    return SimpleNamespace(attn=a, ledger=ledger)


@pytest.fixture
def armed_db(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "orchestra.toml"))
    (tmp_path / "orchestra.toml").write_text("")
    monkeypatch.setattr(U, "_notify", lambda rid: None)
    return _stores(str(data / "state" / "tasks.db"))


@pytest.fixture
def unarmed_db(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return str(data / "state" / "tasks.db")


def test_done_writes_one_event_and_a_linked_card(armed_db, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev-1")
    rc = U.main_done(["Picker fix shipped", "--summary", "bundle index-X.js", "--evidence-test", "12 passed",
                      "--evidence-url", "Preview=https://example.test/p", "--verified"])
    assert rc == 0
    eid = capsys.readouterr().out.strip()
    ev = armed_db.attn.get(eid)
    assert ev["kind"] == "done" and ev["agent"] == "dev-1" and ev["source_id"].startswith("apr_")
    assert ev["body"] == "bundle index-X.js" and ev["title"] == "Picker fix shipped"
    card = armed_db.ledger.get(ev["source_id"])
    assert card["kind"] == "completion" and card["status"] == "pending"
    import json
    assert json.loads(card["evidence"]) == {"tests": "12 passed", "verified": True,
                                            "urls": [{"label": "Preview", "url": "https://example.test/p"}]}


def test_a_retry_with_the_same_key_posts_nothing_new(armed_db, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "gm")
    assert U.main_done(["Shipped", "--key", "k1"]) == 0
    first = capsys.readouterr().out.strip()
    assert U.main_done(["Shipped", "--key", "k1"]) == 0
    assert capsys.readouterr().out.strip() == first
    assert len(armed_db.attn.list_events()) == 1
    assert len([r for r in armed_db.ledger.pending_to_notify() if r["kind"] == "completion"]) == 1


def test_legacy_cards_off_writes_only_the_event(armed_db, monkeypatch, capsys, tmp_path):
    (tmp_path / "orchestra.toml").write_text("[attention]\nlegacy_completion_cards = false\n")
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "gm")
    assert U.main_done(["Shipped"]) == 0
    ev = armed_db.attn.get(capsys.readouterr().out.strip())
    assert ev["source_id"] is None and armed_db.ledger.pending_to_notify() == []


def test_refuses_to_post_as_someone_else(armed_db, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "dev-1")
    assert U.main_done(["x", "--from", "gm"]) == 4
    assert U.main_done(["x", "--from", "dev-1-gen3"]) == 0          # its own rotated name is itself


def test_unarmed_exit_3_and_says_how(unarmed_db, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "gm")
    assert U.main_done(["x"]) == 3 and "m20261104_attention" in capsys.readouterr().err


def test_busy_ledger_exit_1(armed_db, monkeypatch, capsys):
    import attention_store as S
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "gm")

    def busy(*a, **k):
        raise S.AttentionBusy("locked")
    monkeypatch.setattr(S.AttentionStore, "add_done", busy)
    assert U.main_done(["x", "--key", "k"]) == 1 and "run it again" in capsys.readouterr().err


def test_no_identity_exit_2(monkeypatch):
    monkeypatch.delenv("ORCHESTRA_AGENT_ID", raising=False); monkeypatch.delenv("TMUX", raising=False)
    assert U.main_done(["x"]) == 2


def test_empty_text_exit_2(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_AGENT_ID", "gm")
    assert U.main_done(["   "]) == 2


def test_the_verb_is_registered_on_the_orchestra_cli():
    import subprocess
    out = subprocess.run([sys.executable, "-m", "orchestra_cli", "update", "done", "--help"], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "--evidence-test" in out.stdout and "--key" in out.stdout
