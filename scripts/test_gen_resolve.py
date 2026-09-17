"""gen-resolve RED matrix — rotation primitives resolve predecessor
generation from orchestra-registry.db (DB-FIRST), never the flat projection.

11-case matrix from the consensus spec (.workspace/proposals/
rotation-gen-resolve-db-spec.md). Hermetic: tmp_path stores only; the
conftest prod-fingerprint guard applies.
"""
import json
import os
import sqlite3

import pytest

from scripts import gen_resolve

ROOT = "seatx"


@pytest.fixture
def orch(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    return tmp_path


def _seed_db(orch, gen=7, root=ROOT):
    dbp = str(orch / "state" / "orchestra-registry.db")
    c = sqlite3.connect(dbp)
    c.executescript(
        "CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT,"
        " machine TEXT);"
        "CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " root TEXT, generation INTEGER, session_id TEXT, retired_at TEXT);"
        "CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER,"
        " tmux_session TEXT, status TEXT);")
    c.execute("INSERT INTO lineages VALUES (?, 'T2', 'claude', 'vps')", (root,))
    gid = c.execute(
        "INSERT INTO generations (root, generation, session_id) VALUES (?,?,?)",
        (root, gen, "sid-x")).lastrowid
    c.execute("INSERT INTO canonical VALUES (?, ?, ?, 'online')",
              (root, gid, root))
    c.commit()
    c.close()
    return dbp


# --- core resolver ----------------------------------------------------------

def test_red1_flat_null_db_present_resolves_db(orch):
    """RED 1: flat=None + DB gen=N => N (the pm-molevera class, closed)."""
    _seed_db(orch, gen=7)
    assert gen_resolve.resolve_predecessor_generation(
        ROOT, None, orchestra_dir=str(orch)) == 7


def test_red2_null_in_both_returns_none(orch):
    """RED 2: no DB row + flat None => None (caller keeps fail-closed;
    NEVER fabricate)."""
    _seed_db(orch, gen=7, root="other-seat")
    assert gen_resolve.resolve_predecessor_generation(
        ROOT, None, orchestra_dir=str(orch)) is None


def test_red3_db_absent_falls_back_to_flat(orch):
    """RED 3 (+agy note): DB file absent/unreadable => flat value unchanged."""
    assert gen_resolve.resolve_predecessor_generation(
        ROOT, 4, orchestra_dir=str(orch)) == 4
    assert gen_resolve.resolve_predecessor_generation(
        ROOT, None, orchestra_dir=str(orch)) is None


def test_red6_disagreement_db_wins(orch):
    """RED 6: flat=3 + DB=4 => 4 (DB-FIRST, projection never outranks truth)."""
    _seed_db(orch, gen=4)
    assert gen_resolve.resolve_predecessor_generation(
        ROOT, 3, orchestra_dir=str(orch)) == 4


def test_red10_disagreement_fires_alarm(orch):
    """RED 10 (r-a-b): flat!=DB post-cutover = projector-coherence INCIDENT —
    the alarm seam fires (incident-grade), not just a log line."""
    _seed_db(orch, gen=4)
    fired = []
    gen_resolve.resolve_predecessor_generation(
        ROOT, 3, orchestra_dir=str(orch), alarm=lambda msg: fired.append(msg))
    assert fired and "3" in fired[0] and "4" in fired[0]


def test_agreement_no_alarm(orch):
    """Negative control: flat==DB => no alarm."""
    _seed_db(orch, gen=4)
    fired = []
    gen_resolve.resolve_predecessor_generation(
        ROOT, 4, orchestra_dir=str(orch), alarm=lambda m: fired.append(m))
    assert not fired


def test_red11_readonly_never_writes(orch):
    """RED 11 companion: the resolver's connection cannot write the store."""
    dbp = _seed_db(orch, gen=7)
    before = open(dbp, "rb").read()
    gen_resolve.resolve_predecessor_generation(ROOT, None, orchestra_dir=str(orch))
    assert open(dbp, "rb").read() == before


# --- promote_successor call-sites ------------------------------------------

def test_red7_successor_gen_derivation_uses_db(orch, monkeypatch):
    """RED 7/9 (agy+r-a-b CRITICAL-PATH): the :1277 successor-gen derivation —
    the complete.py:780 autonomous shape omits generation=, flat is null, DB
    has N => derived successor gen MUST be N+1, never None."""
    _seed_db(orch, gen=7)
    import scripts.promote_successor as ps
    monkeypatch.setattr(ps, "ORCHESTRA_DIR", str(orch), raising=False)
    pred = {"session_id": "sid-x", "generation": None}
    got = ps.derive_successor_generation(ROOT, pred, explicit=None)
    assert got == 8


def test_red7b_explicit_generation_still_wins(orch, monkeypatch):
    """rotate_agent:630 shape: explicit generation= passes through untouched."""
    _seed_db(orch, gen=7)
    import scripts.promote_successor as ps
    monkeypatch.setattr(ps, "ORCHESTRA_DIR", str(orch), raising=False)
    got = ps.derive_successor_generation(ROOT, {"generation": None}, explicit=9)
    assert got == 9


def test_red2b_successor_gen_null_both_returns_none(orch, monkeypatch):
    """null-in-both at the derivation site => None (caller's fail-closed
    machinery handles it; no fabricated +1 off a phantom)."""
    import scripts.promote_successor as ps
    monkeypatch.setattr(ps, "ORCHESTRA_DIR", str(orch), raising=False)
    assert ps.derive_successor_generation(
        ROOT, {"generation": None}, explicit=None) is None


# --- rotate_agent call-sites ------------------------------------------------

def test_red5_rotate_agent_resolves_not_fabricates(orch, monkeypatch):
    """RED 5: rotate_agent predecessor read: flat-null + DB gen=7 => 7
    (successor alias -g8), and null-both => REFUSAL (default-1 is dead)."""
    _seed_db(orch, gen=7)
    import scripts.rotate_agent as ra
    monkeypatch.setattr(ra, "ORCHESTRA_DIR", orch, raising=False)
    assert ra.resolve_pred_generation_or_refuse(ROOT, {"generation": None}) == 7


def test_red8_rotate_agent_null_both_refuses(orch, monkeypatch):
    """RED 8 (r-a-b override of agy's flag): refusal is ABSOLUTE — no seed
    flag exists; null-in-both raises."""
    import scripts.rotate_agent as ra
    monkeypatch.setattr(ra, "ORCHESTRA_DIR", orch, raising=False)
    with pytest.raises(Exception):
        ra.resolve_pred_generation_or_refuse(ROOT, {"generation": None})
