"""Fleet guard v2 (Identity Layer v1 item (a)) — NO canonical row may be status='online'
while its tmux_session has no live process. The phantom-canonical class (an online seat
with no process — the ios-watch-dev wedge, and the 226/266 VPS rows found 2026-09-15)
must be caught by CI, not by a failed fire / false-idle / dead-letter.

Reuses the SAME by-effect liveness the reconciler applies (tmux has-session AND pane pid
has >=1 child), via status_reconcile.reconcile(dry-run): if it finds ANY seat to flip
online->parked, the invariant is violated. Attached-client and non-local-machine seats are
excluded by the reconciler itself (a human is on it / liveness not judgeable here).

RED-first: 228 online-without-process rows when this landed; GREEN after --apply. LIVE
assertion — skips cleanly when the live store is absent (hermetic CI with no fleet).
"""
import os

import pytest

from scripts.identity_store import status_reconcile

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")


def test_zero_online_canonical_rows_without_a_live_process():
    if not os.path.exists(_DB):
        pytest.skip("no live identity store present (hermetic CI)")
    rep = status_reconcile.reconcile(_LIVE, apply=False)
    # item C (gm msg_b1429f36): the assert message must format keys the reconciler
    # ACTUALLY returns — the old `rep['per_runtime']` raised KeyError on failure and
    # MASKED the real assertion (which was red because of the alias-row leak, item A).
    assert rep["to_parked"] == [], (
        f"{len(rep['to_parked'])} canonical rows are status='online' with NO live process "
        f"(phantom-canonical) — run `python3 scripts/identity_store/status_reconcile.py "
        f"--apply`. by_legacy={rep['by_legacy']}; first={rep['to_parked'][:20]}")
