"""ApprovalStore.migrate() printed 'DDL PENDING (unarmed): <id> cols=[]' on EVERY store open
when the gate was unarmed, even after init had already applied the columns — two lines per
`approval.py get`, flooding any script that polls. The notice is for columns that are
actually missing; with nothing pending it must be silent."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import ApprovalStore


def test_no_pending_notice_when_gated_columns_already_exist(monkeypatch, capsys):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    monkeypatch.setenv("HOME", tempfile.mkdtemp())          # no ~/runtime sentinels
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "m20260825_answer_attribution,m20260825_human_task")
    ApprovalStore(db_path=path).migrate()                    # armed once: columns applied
    monkeypatch.delenv("APPROVAL_DDL_ARMED")
    capsys.readouterr()
    ApprovalStore(db_path=path).migrate()                    # unarmed, nothing pending
    err = capsys.readouterr().err
    assert "DDL PENDING" not in err, err


def test_pending_notice_still_prints_when_a_column_is_missing(monkeypatch, capsys):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    monkeypatch.setenv("HOME", tempfile.mkdtemp())
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    ApprovalStore(db_path=path).migrate()
    err = capsys.readouterr().err
    assert "DDL PENDING (unarmed): m20260825_human_task" in err
