"""RED tests — shared tmux_consolidate helper (orchestra-builder approved interface,
msg_3b8fe585). Closes the recurring D4 gap in BOTH directions: promote_successor sets
the tmux_session FIELD but does not RENAME the actual tmux session, so the reconciler
re-crosses the sid back. This standalone helper renames the ACTUAL session, restamps the
3-store, and RE-VERIFIES the canonical sid still holds (fail-closed if not).

Interface: consolidate_session(old_name, new_name, expected_sid) -> {ok, renamed, verified}
Pure over injected seams (tmux/restamp/resolve) so ordering + idempotency + fail-closed
are proven hermetically.
"""
from scripts.lineage_daemon import tmux_consolidate as TC


def _seams(**over):
    calls = {"rename": [], "restamp": []}
    d = dict(
        session_exists_fn=lambda n: n == "old",       # only the old session exists
        rename_fn=lambda o, n: calls["rename"].append((o, n)) or True,
        restamp_fn=lambda n: calls["restamp"].append(n),
        resolve_sid_fn=lambda n: "sid-canon",         # 3-store maps new_name -> expected sid
    )
    d.update(over)
    return d, calls


def test_rename_restamp_verify_happy():
    seams, calls = _seams()
    r = TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert r == {"ok": True, "renamed": True, "verified": True, "reason": None}
    assert calls["rename"] == [("old", "new")]
    assert calls["restamp"] == ["new"]


def test_rename_before_restamp_ordering():
    order = []
    seams, _ = _seams(
        rename_fn=lambda o, n: order.append("rename") or True,
        restamp_fn=lambda n: order.append("restamp"))
    TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert order == ["rename", "restamp"]


def test_idempotent_already_renamed_no_double_rename():
    """A prior partial run already renamed old->new (old gone, new present). Do NOT
    rename again; still restamp + verify. renamed=False, ok=True."""
    seams, calls = _seams(session_exists_fn=lambda n: n == "new")
    r = TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert r["ok"] is True and r["renamed"] is False and r["verified"] is True
    assert calls["rename"] == []
    assert calls["restamp"] == ["new"]


def test_no_session_fails_closed():
    """Neither old nor new session exists -> nothing to consolidate -> fail-closed."""
    seams, calls = _seams(session_exists_fn=lambda n: False)
    r = TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert r["ok"] is False and r["renamed"] is False
    assert r["reason"] == "no-session"
    assert calls["rename"] == [] and calls["restamp"] == []


def test_rename_failure_fails_closed_no_restamp():
    seams, calls = _seams(rename_fn=lambda o, n: False)
    r = TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert r["ok"] is False and r["renamed"] is False
    assert r["reason"] == "rename-failed"
    assert calls["restamp"] == []                      # never restamp a failed rename


def test_sid_verify_mismatch_fails_closed():
    """The load-bearing D4 catch: after rename+restamp the canonical sid must STILL be
    expected_sid. If resolve returns a different sid (reconciler re-crossed) -> ok=False."""
    seams, _ = _seams(resolve_sid_fn=lambda n: "sid-WRONG")
    r = TC.consolidate_session("old", "new", "sid-canon", **seams)
    assert r["ok"] is False and r["verified"] is False
    assert r["reason"] == "sid-verify-failed"
