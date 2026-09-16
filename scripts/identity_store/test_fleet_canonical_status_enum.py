"""Fleet guard (Identity Layer v1 item (b)) — the status VOCABULARY is closed.

Two invariants, checked against the LIVE store (skips cleanly when absent):
  A. every canonical.status is in status_vocab.STATUS_ENUM {online, parked, provisional,
     retired} — the 15-string zoo can never reason-block a detector again.
  B. no canonical row points at a RETIRED generation (a retired gen must not be canonical).

RED-first: ~113 non-enum rows (the legacy zoo) + 1 retired-gen canonical when this landed.
GREEN after `status_reconcile.py --apply`. Known held anomalies live in EXPECTED_* (a
documented exception, removed as gm resolves each) — mirrors the lineage guard's pattern.
"""
import os
import sqlite3

import pytest

from scripts.identity_store import status_vocab

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")

# Canonical rows pointing at a retired generation. gm ruled the 56 status='retired' rows +
# ios-watch-dev-g17 (dead -gN alias) retired by effect (msg_7e2e9f20); exception set now empty.
EXPECTED_RETIRED_GEN = set()


def _conn():
    return sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)


def test_every_canonical_status_is_in_the_enum():
    if not os.path.exists(_DB):
        pytest.skip("no live identity store present (hermetic CI)")
    c = _conn()
    try:
        bad = [(r[0], r[1]) for r in c.execute(
            "SELECT c.root, c.status FROM canonical c "
            "JOIN generations g ON g.id = c.generation_id WHERE g.retired_at IS NULL")
            if r[1] not in status_vocab.STATUS_ENUM]
    finally:
        c.close()
    assert bad == [], (
        f"{len(bad)} canonical rows carry a non-enum status (the zoo) — run "
        f"`python3 -m scripts.identity_store.status_reconcile --apply`. first={bad[:15]}")


def test_no_canonical_row_points_at_a_retired_generation():
    if not os.path.exists(_DB):
        pytest.skip("no live identity store present (hermetic CI)")
    c = _conn()
    try:
        bad = [r[0] for r in c.execute(
            "SELECT c.root FROM canonical c JOIN generations g ON g.id = c.generation_id "
            "WHERE g.retired_at IS NOT NULL")]
    finally:
        c.close()
    unexpected = sorted(set(bad) - EXPECTED_RETIRED_GEN)
    assert unexpected == [], (
        f"canonical rows point at a RETIRED generation (should not be canonical): "
        f"{unexpected}")


def test_no_canonical_row_carries_retired_status():
    """gm semantics (msg_7e2e9f20): a canonical row exists only for online/parked/
    provisional seats — 'retired' is a GENERATION fact, not a canonical status."""
    if not os.path.exists(_DB):
        pytest.skip("no live identity store present (hermetic CI)")
    c = _conn()
    try:
        bad = [r[0] for r in c.execute(
            "SELECT c.root FROM canonical c JOIN generations g ON g.id = c.generation_id "
            "WHERE c.status = 'retired' AND g.retired_at IS NULL")]
    finally:
        c.close()
    assert bad == [], (
        f"{len(bad)} canonical rows carry status='retired' with a live generation "
        f"(dead historical seats never retired off canonical): {bad[:15]}")
