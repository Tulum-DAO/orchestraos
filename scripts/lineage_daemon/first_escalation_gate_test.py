"""Tests — first_escalation_gate (DEC-1787808620 A.3 rework; gm pre-arm).
Salvage of first_rollback_gate @820807ec9 (s/rollback/escalate/).
"""
from scripts.lineage_daemon.first_escalation_gate import first_escalation_gate


def test_first_escalation_holds_notifies_and_drops_sentinel(tmp_path):
    fired = []
    r = first_escalation_gate("pm-x", runtime_dir=str(tmp_path),
                              notify_fn=lambda root, ctx: fired.append(root))
    assert r["proceed"] is False and r["reason"] == "first-escalation-human-gate"
    assert fired == ["pm-x"]
    assert (tmp_path / "FIRST_ESCALATION_GATED_pm-x").exists()


def test_second_holds_until_cleared(tmp_path):
    fired = []
    first_escalation_gate("pm-x", runtime_dir=str(tmp_path),
                          notify_fn=lambda root, ctx: fired.append(root))
    r2 = first_escalation_gate("pm-x", runtime_dir=str(tmp_path),
                               notify_fn=lambda root, ctx: fired.append(root))
    assert r2["proceed"] is False and r2["reason"] == "awaiting-human-clear"
    assert fired == ["pm-x"]


def test_cleared_lineage_proceeds(tmp_path):
    (tmp_path / "escalation_armed").write_text("pm-x\n")
    r = first_escalation_gate("pm-x", runtime_dir=str(tmp_path),
                              notify_fn=lambda root, ctx: None)
    assert r["proceed"] is True and r["reason"] == "cleared"


def test_cleared_is_per_lineage(tmp_path):
    (tmp_path / "escalation_armed").write_text("other\n")
    r = first_escalation_gate("pm-x", runtime_dir=str(tmp_path),
                              notify_fn=lambda root, ctx: None)
    assert r["proceed"] is False


def test_notify_failure_still_holds(tmp_path):
    def boom(root, ctx):
        raise RuntimeError("tg down")
    r = first_escalation_gate("pm-x", runtime_dir=str(tmp_path), notify_fn=boom)
    assert r["proceed"] is False
