"""`approval.py ack` is idempotent: the approval_resolved text tells
the agent to run ack, but a row the pipeline already self-acked returned {"acked": false} + exit 1,
which reads as a failure. Already-acked is success; a never-answered row still fails.
Runs the REAL CLI against a scratch DB; ntfy is fenced off so nothing reaches Shaw's devices."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from approval_schema import ApprovalStore  # noqa: E402


def _cli(db, *args):
    env = dict(os.environ, APPROVAL_DB_PATH=db, NTFY_TOKEN_FILE="/nonexistent/ntfy-fence")
    r = subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), *args],
                       capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def _answered_row(db):
    s = ApprovalStore(db_path=db); s.migrate()
    rid = s.create(from_agent="t-agent", question="q?", op_key="k-ack", worker_kind="pane")
    s.record_answer(rid, "approve", "")
    return s, rid


def test_fence_is_in_place():
    assert not os.path.exists("/nonexistent/ntfy-fence")


def test_ack_then_ack_again_both_succeed(tmp_path):
    db = str(tmp_path / "t.db")
    s, rid = _answered_row(db)
    assert s.get(rid)["status"] == "answered"
    rc1, out1 = _cli(db, "ack", rid, "--from", "t-agent")
    assert rc1 == 0 and out1["acked"] is True
    rc2, out2 = _cli(db, "ack", rid, "--from", "t-agent")
    assert rc2 == 0 and out2["acked"] is True and out2.get("already") is True


def test_ack_on_a_pending_row_still_fails(tmp_path):
    db = str(tmp_path / "t.db")
    s = ApprovalStore(db_path=db); s.migrate()
    rid = s.create(from_agent="t-agent", question="q?", op_key="k-pend", worker_kind="pane")
    rc, out = _cli(db, "ack", rid, "--from", "t-agent")
    assert rc == 1 and out["acked"] is False
