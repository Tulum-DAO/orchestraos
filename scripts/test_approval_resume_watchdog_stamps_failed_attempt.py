"""RED-first: a fire_resume that RAISES must still stamp last_attempt_at.

By effect 2026-09-16 (fresh install under `orchestra up`): the pane path raised before any
stamp, so every 60 s tick re-entered the row, and _fire_pane_resume's "first try only"
durable msg_store send (gated on resumed_at/last_attempt_at being empty) fired AGAIN each
tick — duplicate "Approval ... resolved" rows in the seat's inbox, one per minute.
Hermetic: temp store, fire_resume stubbed to raise, no tmux, no gateway.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_resume as ar
from approval_schema import ApprovalStore


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = ApprovalStore(db_path=path)
    s.migrate()
    return s


def test_watchdog_stamps_last_attempt_when_fire_resume_raises(monkeypatch):
    store = _tmp_store()
    rid = store.create(from_agent="hello", question="Ship?", worker_kind="pane", options=["approve", "deny"])
    store.record_answer(rid, "approve", None)
    calls = []

    def boom(row, s, allow_noncanonical=False):
        calls.append(row["id"])
        raise FileNotFoundError("scripts/agent-status.py")

    monkeypatch.setattr(ar, "fire_resume", boom)
    ar.watchdog(store=store)
    assert calls == [rid]
    row = store.get(rid)
    assert row["last_attempt_at"], "a failed attempt must be stamped so the next beat throttles it"
    assert row["resume_attempts"] == 0, "a crash is a cheap failure, not a real delivery attempt"
    # second tick inside the beat window: throttled, fire_resume NOT re-entered
    ar.watchdog(store=store)
    assert calls == [rid]
