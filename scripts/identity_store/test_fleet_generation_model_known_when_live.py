"""Fleet guard (Identity Layer v1, gm msg_07fbad31): zero LIVE (status='online') LLM
canonical rows may carry an unknown model. Services carry the explicit 'n/a' constant and
are excluded by value. Scoped to machine=vps — a non-vps seat's model can't be judged
locally (no transcript/pane here), exactly as status_reconcile skips non-vps for liveness.
LIVE assertion — skips when the store is absent (hermetic CI)."""
import os

import pytest

from scripts.identity_store import orchestra_db

_DB = os.path.expanduser("~/scripts/agent-orchestra/state/orchestra-registry.db")
_UNKNOWN = (None, "", "unknown")
_LLM = ("claude", "codex", "gemini")
# KNOWN-LOUD tracked exceptions (gm ruling msg_799b33cf): agy is a gemini control-leg with
# NO authoritative model source today (the antigravity brain transcript is polluted with
# quoted model strings; there is no session-metadata model field). Left model=unknown LOUD,
# never guessed; the real fix is a gemini-meta resolver gm owns. Same shape as the non-vps
# exclusion. Remove when the gemini-meta resolver lands.
_TRACKED_UNRESOLVED = ("agy",)


def test_no_live_llm_row_has_unknown_model():
    if not os.path.exists(_DB):
        pytest.skip("no live identity store (hermetic CI)")
    conn = orchestra_db.get_connection(_DB)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.root AS root, l.runtime AS runtime, g.model AS model "
            "FROM canonical c JOIN generations g ON g.id=c.generation_id "
            "LEFT JOIN lineages l ON l.root=c.root "
            "WHERE g.retired_at IS NULL AND c.status='online' "
            "AND (l.machine IS NULL OR l.machine='vps')")]
    finally:
        conn.close()
    bad = [(r["root"], r["runtime"]) for r in rows
           if r["runtime"] in _LLM and r["model"] in _UNKNOWN
           and r["root"] not in _TRACKED_UNRESOLVED]
    assert bad == [], (
        f"{len(bad)} LIVE online LLM canonical rows carry an unknown model — run "
        f"`python3 -m scripts.identity_store.model_reconcile --apply`. first={bad[:20]}")
