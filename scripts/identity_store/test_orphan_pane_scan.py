"""RED-first — orphan_pane_scan.py (Leg B, gm msg_b76ddb60): the raw-spawn
BACKSTOP. Leg A only guards spawns that go through spawn-agent.sh; a raw
tmux+claude spawn bypasses everything and comes up with zero identity rows.
This scanner catches the route-around: a live AGENT-CLI pane whose tmux session
resolves to no identity-store row -> ALERT (gm chip), debounced, and HARD
no-auto-kill (a false positive killing a live seat is worse than the orphan).

Seams: tmux/process enumeration is INJECTED (pure-input classifier); the
identity store is the REAL sqlite engine via orchestra_db.init_db (real-engine
fixture rule). Alert emission is an injected sink so tests never touch
msg_store.
"""
import os
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import orchestra_db  # noqa: E402
from scripts.identity_store import orphan_pane_scan as ops  # noqa: E402


@pytest.fixture
def db(tmp_path):
    dbp = tmp_path / "state" / "orchestra-registry.db"
    dbp.parent.mkdir(parents=True)
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        orchestra_db.adopt_identity(c, {
            "root": "gm", "generation": 49, "model": "m", "tier": "T1",
            "runtime": "claude"})
        orchestra_db.adopt_identity(c, {
            "root": "ios-watch-dev", "generation": 12, "model": "m",
            "tier": "T2", "runtime": "claude"})
    finally:
        c.close()
    return str(dbp)


def _pane(name, cmd):
    return {"session": name, "child_cmdline": cmd}


CLAUDE = "/home/x/.nvm/versions/node/v22/bin/claude --dangerously-skip-permissions"


def test_registered_root_and_gen_aliases_are_not_orphans(db):
    panes = [_pane("gm", CLAUDE),
             _pane("ios-watch-dev", CLAUDE),
             _pane("ios-watch-dev-g12", CLAUDE),
             _pane("ios-watch-dev-gen12", CLAUDE)]
    assert ops.find_orphans(panes, db) == []


def test_unregistered_agent_cli_pane_is_an_orphan(db):
    orphans = ops.find_orphans([_pane("rogue-rider", CLAUDE)], db)
    assert [o["session"] for o in orphans] == ["rogue-rider"]


def test_service_panes_are_ignored(db):
    """Non-agent-CLI processes (gateways, proxies, node services) never flag."""
    panes = [_pane("watch-gateway", "python3 watch_gateway.py"),
             _pane("dashboard", "node dashboard-proxy.js"),
             _pane("scratch-shell", "-bash")]
    assert ops.find_orphans(panes, db) == []


def test_gen_alias_of_registered_root_with_wrong_gen_still_matches_root(db):
    """A -gN alias whose N isn't in generations still belongs to a KNOWN lineage
    (provisional/mid-rotation shapes) — lineage membership is enough; the scanner
    hunts UNKNOWN lineages, not generation drift."""
    assert ops.find_orphans([_pane("ios-watch-dev-g13", CLAUDE)], db) == []


def test_ignore_list_suppresses_named_sessions(db):
    orphans = ops.find_orphans([_pane("shaw-scratch", CLAUDE)], db,
                               ignore={"shaw-scratch"})
    assert orphans == []


def test_alerts_are_debounced_per_session(db, tmp_path):
    sink = []
    state = tmp_path / "orphan-scan-state"
    ops.alert_orphans([{"session": "rogue-rider", "child_cmdline": CLAUDE}],
                      state_dir=str(state), send=sink.append, now=1000.0)
    ops.alert_orphans([{"session": "rogue-rider", "child_cmdline": CLAUDE}],
                      state_dir=str(state), send=sink.append, now=1500.0)
    assert len(sink) == 1, "second scan within TTL must not re-alert"
    ops.alert_orphans([{"session": "rogue-rider", "child_cmdline": CLAUDE}],
                      state_dir=str(state), send=sink.append,
                      now=1000.0 + ops.ALERT_TTL_S + 1)
    assert len(sink) == 2, "after TTL the still-live orphan re-alerts"


def test_scan_never_kills(db):
    """The module must not even import a kill capability: no tmux kill-session,
    no os.kill / killpg usage anywhere in the scanner source."""
    src = open(os.path.join(_HERE, "orphan_pane_scan.py")).read()
    for forbidden in ("kill-session", "os.kill", "killpg", "SIGKILL", "SIGTERM"):
        assert forbidden not in src, f"scanner must be chip-only, found {forbidden!r}"


# --- Resurrection detection (gm msg_880c11d3, approved RED-first) -------------
# The 09-08 incident: arturo-restore-dev was retired (canonical dropped,
# retired_at stamped) then raw-respawned; the scan stayed silent because the
# LINEAGE rows still existed. Rule: a live agent-CLI pane whose lineage has NO
# canonical row, or whose canonical generation carries retired_at, or whose
# canonical generation has a NULL sid past a grace window -> flag with reason.

def _retire(dbp, root):
    c = orchestra_db.get_connection(dbp)
    try:
        row = c.execute("SELECT generation_id FROM canonical WHERE root=?",
                        (root,)).fetchone()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE generations SET retired_at='2026-09-07T15:44:56Z' "
                  "WHERE id=?", (row["generation_id"],))
        c.execute("DELETE FROM canonical WHERE root=?", (root,))
        c.execute("COMMIT")
    finally:
        c.close()


def test_live_pane_of_canonical_less_lineage_flags_resurrection(db):
    """Retired shape A: canonical dropped, lineage/generation rows remain —
    exactly the arturo-restore-dev 16:10Z respawn. Must flag."""
    _retire(db, "ios-watch-dev")
    out = ops.find_resurrections([_pane("ios-watch-dev", CLAUDE)], db)
    assert [(o["session"], o["reason"]) for o in out] == [
        ("ios-watch-dev", "no-canonical")]


def test_canonical_pointing_at_retired_generation_flags(db):
    """Retired shape B: canonical re-inserted over a retired_at generation (the
    post-adopt contradictory state gen461 landed in). Must flag."""
    c = orchestra_db.get_connection(db)
    try:
        row = c.execute("SELECT generation_id FROM canonical WHERE root='gm'"
                        ).fetchone()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE generations SET retired_at='2026-09-07T15:44:56Z' "
                  "WHERE id=?", (row["generation_id"],))
        c.execute("COMMIT")
    finally:
        c.close()
    out = ops.find_resurrections([_pane("gm", CLAUDE)], db)
    assert [(o["session"], o["reason"]) for o in out] == [
        ("gm", "canonical-retired")]


def test_healthy_canonical_seat_with_sid_never_flags(db):
    c = orchestra_db.get_connection(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE generations SET session_id='sid-x' WHERE root='gm'")
        c.execute("COMMIT")
    finally:
        c.close()
    assert ops.find_resurrections([_pane("gm", CLAUDE)], db) == []


def test_null_sid_flags_only_after_grace(db, tmp_path):
    """A legit fresh spawn has sid NULL until attribution — must NOT flag inside
    the grace window (else every new spawn chips), MUST flag after it."""
    fresh = dict(_pane("gm", CLAUDE), pane_created=1000.0)
    assert ops.find_resurrections([fresh], db,
                                  now=1000.0 + 60) == []
    out = ops.find_resurrections([fresh], db,
                                 now=1000.0 + ops.SID_GRACE_S + 1)
    assert [(o["session"], o["reason"]) for o in out] == [("gm", "null-sid")]


def test_unknown_lineage_stays_an_orphan_not_a_resurrection(db):
    """Separation of concerns: find_resurrections only judges KNOWN lineages;
    unknown ones remain find_orphans' business."""
    assert ops.find_resurrections([_pane("rogue-rider", CLAUDE)], db) == []


def test_service_and_ignored_panes_never_flag_resurrection(db):
    _retire(db, "ios-watch-dev")
    assert ops.find_resurrections(
        [_pane("ios-watch-dev", "python3 watch_gateway.py")], db) == []
    assert ops.find_resurrections(
        [_pane("ios-watch-dev", CLAUDE)], db, ignore={"ios-watch-dev"}) == []
