"""Fleet cross-file guard (Identity Layer v1 item b, gm msg_b1429f36).

M1 proves each projected file equals its faithful projection PER FILE — it is blind to a
registry-vs-sessions SPLIT for the same identity. The archived-predecessor bug was exactly
that: registry.json said a swap-retired archive was 'retired' while agent-sessions.json said
'parked'.

Scope = SWAP-RETIRED archives only (the item-b class): a retired generation whose ROOT is a
live canonical seat and for which NO standalone canonical row of its own exists — i.e. the
pure `<root>-gen<N>` PROJECTION the archive rule governs. (A migrated seat that carries its
own canonical row is a different class — a canonical PARKED seat whose session DOC may still
hold a legacy status; that is item-(a) session-doc reconcile, not this guard.) For each such
archive present in BOTH artifacts, registry status must equal sessions status.

LIVE assertion — skips cleanly when the store/artifacts are absent (hermetic CI).
"""
import json
import os

import pytest

from scripts.identity_store import orchestra_db

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")
_REGISTRY = os.path.join(_LIVE, "registry.json")
_SESSIONS = os.path.join(_LIVE, "state", "agent-sessions.json")


def _swap_archive_keys(conn):
    """`<root>-gen<N>` for every swap-retired predecessor: a retired generation whose
    root is a live canonical seat, excluding any key that is itself a canonical row."""
    canon = {r["root"] for r in conn.execute("SELECT root FROM canonical")}
    keys = []
    for g in conn.execute("SELECT root, generation FROM generations "
                          "WHERE retired_at IS NOT NULL"):
        if g["root"] in canon:
            key = f"{g['root']}-gen{g['generation']}"
            if key not in canon:
                keys.append(key)
    return keys


def test_swap_archive_status_agrees_across_registry_and_sessions():
    if not (os.path.exists(_DB) and os.path.exists(_REGISTRY)
            and os.path.exists(_SESSIONS)):
        pytest.skip("live store/projections absent (hermetic CI)")
    conn = orchestra_db.get_connection(_DB)
    try:
        archive_keys = _swap_archive_keys(conn)
    finally:
        conn.close()
    with open(_REGISTRY) as fh:
        reg_agents = json.load(fh).get("agents", {})
    with open(_SESSIONS) as fh:
        sessions = json.load(fh)

    mismatches = []
    for key in archive_keys:
        rrow, srow = reg_agents.get(key), sessions.get(key)
        if not isinstance(rrow, dict) or not isinstance(srow, dict):
            continue
        if rrow.get("status") != srow.get("status"):
            mismatches.append((key, rrow.get("status"), srow.get("status")))

    assert mismatches == [], (
        f"{len(mismatches)} swap-retired archives disagree between registry.json and "
        f"agent-sessions.json — the projector must apply ONE resumable-archive rule "
        f"('parked' if resume_command else 'retired') to both. first={mismatches[:20]}")
