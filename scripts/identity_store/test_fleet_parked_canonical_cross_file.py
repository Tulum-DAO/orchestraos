"""Fleet cross-file guard (Identity Layer v1, gm ruling msg_61b4581b — the quiescent
session-doc class). Item-(a) status_reconcile updated a parked seat's typed canonical.status
AND its registry agent doc, but NOT its agent-sessions.json SESSION document — so the
projector re-emits the session doc's legacy status field ('quiescent'/'killed'/'online'/...)
while registry says 'parked'. M1 (per-file) is blind to the split.

This asserts that for every PARKED canonical seat present in agent-sessions.json, the session
row's status equals the registry row's status (both 'parked'). Scoped to parked-canonical
members (online seats are a separate, live class). LIVE — skips when store/artifacts absent."""
import json
import os

import pytest

from scripts.identity_store import orchestra_db

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")
_REGISTRY = os.path.join(_LIVE, "registry.json")
_SESSIONS = os.path.join(_LIVE, "state", "agent-sessions.json")


def test_parked_canonical_status_agrees_across_registry_and_sessions():
    if not (os.path.exists(_DB) and os.path.exists(_REGISTRY)
            and os.path.exists(_SESSIONS)):
        pytest.skip("live store/projections absent (hermetic CI)")
    conn = orchestra_db.get_connection(_DB)
    try:
        parked = {r["root"] for r in conn.execute(
            "SELECT root FROM canonical WHERE status='parked'")}
    finally:
        conn.close()
    with open(_SESSIONS) as fh:
        sessions = json.load(fh)

    mismatches = []
    for root in parked:
        srow = sessions.get(root)
        if not isinstance(srow, dict):
            continue
        if srow.get("status") != "parked":
            mismatches.append((root, srow.get("status")))

    assert mismatches == [], (
        f"{len(mismatches)} parked canonical seats have a session-doc status != 'parked' "
        f"(the legacy quiescent-zoo residue item-a left in the session document) — run the "
        f"session-doc reconcile. first={mismatches[:20]}")
