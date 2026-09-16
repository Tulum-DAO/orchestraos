"""H10 rotation lock + H11 reap reconciler (WS3 v2, DEC-1786724046)."""

from scripts.lineage_daemon import rotation_lock as rl
from scripts.lineage_daemon.reap_reconciler import reconcile_reap


# --- H10 rotation lock ---

def test_can_rotate_clear_when_empty():
    assert rl.can_rotate({}, canary="a", lineage_root="a", now=0)["ok"] is True


def test_same_lineage_blocks():
    locks = rl.acquire({}, canary="a", lineage_root="a", successor="a-g2", now=0)
    r = rl.can_rotate(locks, canary="a", lineage_root="a", now=10)
    assert r["ok"] is False and "lineage" in r["reason"]


def test_verifier_of_inflight_blocks():
    # rotation of X uses gm as escalation-target; gm cannot itself rotate meanwhile.
    locks = rl.acquire({}, canary="x", lineage_root="x", successor="x-g2", now=0,
                       escalation_target="gm")
    r = rl.can_rotate(locks, canary="gm", lineage_root="gm", now=10)
    assert r["ok"] is False and "escalation-target" in r["reason"]


def test_party_to_inflight_blocks():
    locks = rl.acquire({}, canary="a", lineage_root="a", successor="a-g2", now=0)
    r = rl.can_rotate(locks, canary="a-g2", lineage_root="other", now=10)
    assert r["ok"] is False and "party" in r["reason"]


def test_release_frees_the_lock():
    locks = rl.acquire({}, canary="a", lineage_root="a", successor="a-g2", now=0)
    locks = rl.release(locks, canary="a", lineage_root="a")
    assert rl.can_rotate(locks, canary="a", lineage_root="a", now=10)["ok"] is True


def test_stale_lock_reclaimable():
    locks = rl.acquire({}, canary="a", lineage_root="a", successor="a-g2", now=0)
    # a lock older than TTL is ignored by can_rotate + dropped by reap_stale
    r = rl.can_rotate(locks, canary="a", lineage_root="a", now=rl.LOCK_TTL_S + 1)
    assert r["ok"] is True
    assert rl.reap_stale(locks, now=rl.LOCK_TTL_S + 1)["rotations"] == []


def test_different_lineage_concurrent_ok():
    locks = rl.acquire({}, canary="a", lineage_root="a", successor="a-g2", now=0)
    r = rl.can_rotate(locks, canary="b", lineage_root="b", now=10)
    assert r["ok"] is True


# --- H11 reap reconciler ---

def test_reap_ok_no_artifact():
    out = reconcile_reap({"delivered_to": "pm", "ok": True},
                         agent_id="worker", handoff={}, now=0)
    assert out["artifacts"] == []
    assert out["retry"] is None


def test_reap_failure_produces_durable_artifact_and_retry():
    handoff = {"open_loops": ["reply-to-pm-abc", "reply-to-gm-xyz"]}
    out = reconcile_reap(
        {"delivered_to": "pm-clients", "ok": False, "error": "msg_store down"},
        agent_id="worker", handoff=handoff, now=123)
    assert len(out["artifacts"]) == 1
    art = out["artifacts"][0]
    assert art["to"] == "gm"
    assert art["type"] == "reap_delivery_failed"
    assert art["undelivered_open_loops"] == handoff["open_loops"]
    assert "msg_store down" in art["body"]
    assert out["retry"]["owner"] == "pm-clients"
    assert out["retry"]["attempts"] == 1
