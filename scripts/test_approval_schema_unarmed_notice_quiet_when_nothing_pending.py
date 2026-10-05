"""ApprovalStore.migrate() printed 'DDL PENDING (unarmed): <id> cols=[]' on EVERY store open
when the gate was unarmed, even after init had already applied the columns — two lines per
`approval.py get`, flooding any script that polls. The notice is for columns that are
actually missing; with nothing pending it must be silent."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_schema import GATED_MIGRATIONS, ApprovalStore


def test_no_pending_notice_when_gated_columns_already_exist(monkeypatch, capsys):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    monkeypatch.setenv("HOME", tempfile.mkdtemp())          # no ~/runtime sentinels
    # Derived, not hardcoded: this test is about "nothing pending", so it must arm whatever
    # the gated set CURRENTLY holds. The literal list it used to carry silently became a
    # different test the moment a migration was added.
    monkeypatch.setenv("APPROVAL_DDL_ARMED",
                       ",".join(m["id"] for m in GATED_MIGRATIONS))
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
