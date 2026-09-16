"""Writer-resume p5 — the shell identity writers route through the store under cutover.

p5a (this file, first): ``identity_writer.adopt_identity`` — the flag-gated U16 ADOPT
wrapper the SANCTIONED spinup path calls. spinup-orchestra-builder-v2.sh (:44/:52) mints
a COMPLETE identity (root/generation/model/tier/runtime), so under cutover it ADOPTS the
identity into the store instead of writing registry.json/agent-sessions.json directly.
Fail-closed: a missing _REQUIRED_ADOPT field RAISES (never a partial identity, DEC-1788346974
(b)). INERT: inactive -> returns False so the caller does its byte-identical legacy json.dump.

RED until identity_writer grows the adopt_identity wrapper.
"""
import json
import os

import pytest

from scripts.identity_store import (cutover, identity_writer, orchestra_db,
                                    shell_writers)

_IDENT = {"root": "orchestra-builder-2", "generation": 2,
          "model": "claude-opus-4-8[1m]", "tier": "T2", "runtime": "claude",
          "machine": "vps", "session_id": "sid-x"}


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    orchestra_db.init_db(os.path.join(str(tmp_path), "state", "orchestra-registry.db"))
    return str(tmp_path)


def _conn(orchdir):
    return orchestra_db.get_connection(
        os.path.join(orchdir, "state", "orchestra-registry.db"))


def test_adopt_inactive_returns_false(orchdir):
    """INERT: flag off -> the adopt op is a no-op returning False (caller writes JSON)."""
    assert identity_writer.adopt_identity(orchdir, dict(_IDENT)) is False


def test_adopt_active_inserts_full_identity(orchdir):
    """Under cutover: adopt the COMPLETE identity -> generation + canonical rows exist."""
    cutover.arm(orchdir)
    assert identity_writer.adopt_identity(orchdir, dict(_IDENT)) is True
    conn = _conn(orchdir)
    try:
        row = conn.execute(
            "SELECT generation, model FROM generations WHERE root=?",
            ("orchestra-builder-2",)).fetchone()
        assert row is not None and row["generation"] == 2
        assert row["model"] == "claude-opus-4-8[1m]"
        assert conn.execute("SELECT 1 FROM canonical WHERE root=?",
                            ("orchestra-builder-2",)).fetchone() is not None
    finally:
        conn.close()


def test_adopt_fail_closed_on_missing_required(orchdir):
    """(b) no partial identities: a missing _REQUIRED_ADOPT field HARD-FAILS."""
    cutover.arm(orchdir)
    bad = dict(_IDENT)
    bad.pop("model")
    with pytest.raises(orchestra_db.IdentityAdoptionError):
        identity_writer.adopt_identity(orchdir, bad)


# --- p5c: state-snapshot managed per-agent state write --------------------

def _seed_canonical(orchdir, root="a1"):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    c = orchestra_db.get_connection(dbp)
    try:
        c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
                  "VALUES (?, 'T2', 'claude', 'vps', '/x', 1)", (root,))
        gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                        "VALUES (?, 1, 's1', 'm')", (root,)).lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES (?, ?, ?, 'online')", (root, gid, root))
    finally:
        c.close()


def test_managed_state_write_inactive_returns_false(orchdir):
    """INERT: flag off -> the seam declines (False) so the shell does its json.dump."""
    _seed_canonical(orchdir)
    sf = os.path.join(orchdir, "state", "agents", "a1.json")
    assert shell_writers.managed_state_write(
        orchdir, "a1", sf, {"status": "online"}) is False


def test_managed_state_write_routes_agents_path_to_store(orchdir):
    """Under cutover, a state/agents/*.json target is projector-owned: persist the
    FULL blob to source_records (DP-A2) and take NO direct file write."""
    _seed_canonical(orchdir)
    cutover.arm(orchdir)
    sf = os.path.join(orchdir, "state", "agents", "a1.json")
    assert shell_writers.managed_state_write(
        orchdir, "a1", sf, {"status": "online", "x": 1}) is True
    assert not os.path.exists(sf), \
        "cutover: projector owns state/agents/*.json — no direct write"
    c = _conn(orchdir)
    try:
        row = c.execute(
            "SELECT payload_json FROM source_records WHERE file='state/agents' "
            "AND kind='state_agent' AND key='a1.json'").fetchone()
    finally:
        c.close()
    assert row is not None and json.loads(row["payload_json"])["x"] == 1


def test_managed_state_write_unmanaged_path_returns_false(orchdir):
    """A state/ ROOT file (not under state/agents/) is NOT a managed projection —
    the seam declines so the legacy json.dump still runs."""
    _seed_canonical(orchdir)
    cutover.arm(orchdir)
    sf = os.path.join(orchdir, "state", "a1.json")
    assert shell_writers.managed_state_write(
        orchdir, "a1", sf, {"status": "online"}) is False
