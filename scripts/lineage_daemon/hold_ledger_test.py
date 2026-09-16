"""H9 HOLD ledger + reconciler (WS3 v2, DEC-1786724046)."""

from scripts.lineage_daemon.hold_ledger import (
    add_hold, resolve_hold, reconcile, open_holds, mark_promoted,
    RE_PAGE_AGE_S, PARK_PROPOSAL_AGE_S,
)

_HEALTHY = lambda c: True
_UNHEALTHY = lambda c: False


def test_add_and_open_holds():
    led = add_hold({}, canary="gm", successor="gm-g7",
                   status="hold:successor-unconfirmed", reason="shallow", now=0)
    assert len(open_holds(led)) == 1
    assert open_holds(led)[0]["canary"] == "gm"


def test_add_does_not_mutate_input():
    orig = {}
    add_hold(orig, canary="a", successor="a-g2", status="s", reason="r", now=0)
    assert orig == {}


def test_resolve_hold():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    led = resolve_hold(led, "a", "a-g2")
    assert open_holds(led) == []


def test_reconcile_repage_at_4h():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    out = reconcile(led, now=RE_PAGE_AGE_S, predecessor_healthy=_HEALTHY)
    kinds = [a["kind"] for a in out["actions"]]
    assert "re_page" in kinds
    # repaged_at stamped so it doesn't re-page every beat
    out2 = reconcile(out["ledger"], now=RE_PAGE_AGE_S + 60, predecessor_healthy=_HEALTHY)
    assert "re_page" not in [a["kind"] for a in out2["actions"]]


def test_reconcile_park_proposal_at_12h_when_predecessor_healthy():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    out = reconcile(led, now=PARK_PROPOSAL_AGE_S, predecessor_healthy=_HEALTHY)
    kinds = [a["kind"] for a in out["actions"]]
    assert "park_successor_proposal" in kinds


def test_reconcile_defers_when_predecessor_unhealthy():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    out = reconcile(led, now=PARK_PROPOSAL_AGE_S, predecessor_healthy=_UNHEALTHY)
    kinds = [a["kind"] for a in out["actions"]]
    assert "escalation_deferred" in kinds
    assert "park_successor_proposal" not in kinds


def test_reconcile_ignores_resolved():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    led = resolve_hold(led, "a", "a-g2")
    out = reconcile(led, now=PARK_PROPOSAL_AGE_S, predecessor_healthy=_HEALTHY)
    assert out["actions"] == []


def test_fresh_hold_no_action():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    out = reconcile(led, now=60, predecessor_healthy=_HEALTHY)  # 1 min old
    assert out["actions"] == []


# --- D8 audit marker: mark_promoted stamps promoted_at on the matching open hold --

def test_mark_promoted_stamps_promoted_at_and_sid():
    led = add_hold({}, canary="pm-x", successor="pm-x-g2", status="s", reason="r",
                   now=100)
    led = mark_promoted(led, "pm-x", "pm-x-g2", now=250, promoted_by="sid-abc")
    h = open_holds(led)[0]
    assert h["promoted_at"] == 250
    assert h["promoted_by"] == "sid-abc"
    # marking promoted does NOT resolve the hold (retire still owes resolve_hold)
    assert h["resolved"] is False


def test_mark_promoted_does_not_mutate_input():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    before = [dict(h) for h in led["holds"]]
    mark_promoted(led, "a", "a-g2", now=9, promoted_by="sid-1")
    assert led["holds"] == before          # pure: original untouched


def test_mark_promoted_only_matching_open_hold():
    led = add_hold({}, canary="a", successor="a-g2", status="s", reason="r", now=0)
    led = add_hold(led, canary="b", successor="b-g2", status="s", reason="r", now=0)
    led = mark_promoted(led, "a", "a-g2", now=5, promoted_by="sid-a")
    by_canary = {h["canary"]: h for h in led["holds"]}
    assert by_canary["a"].get("promoted_at") == 5
    assert by_canary["b"].get("promoted_at") is None   # untouched


# --- A.3 rework: set_hold_status transition + reconcile double-skip -----------

def test_set_hold_status_transitions_open_hold():
    from scripts.lineage_daemon.hold_ledger import set_hold_status
    led = add_hold({}, canary="pm-x", successor="pm-x-g2",
                   status="promoted:awaiting-progress", reason="r", now=0)
    led = set_hold_status(led, "pm-x", "pm-x-g2", "watch:awaiting-effect-post-retire")
    h = open_holds(led)[0]
    assert h["status"] == "watch:awaiting-effect-post-retire"
    assert h["resolved"] is False


def test_reconcile_skips_both_watch_statuses_even_when_old():
    from scripts.lineage_daemon.hold_ledger import WATCH_STATUSES
    for st in WATCH_STATUSES:
        led = add_hold({}, canary="pm-x", successor="pm-x-g2", status=st,
                       reason="r", now=0)
        # WAY past both re_page (4h) and park (12h) thresholds
        out = reconcile(led, now=PARK_PROPOSAL_AGE_S + 10 * 3600,
                        predecessor_healthy=_HEALTHY)
        assert out["actions"] == [], f"{st} must not re_page/park (completion owns it)"
        # and the hold is untouched (not repaged)
        assert open_holds(out["ledger"])[0].get("repaged_at") is None
