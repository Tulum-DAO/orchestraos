"""Piece-3 (RED) — U11 foreign-write handling + U16 identity-establishing writes.

The projector overwrites legacy artifacts, so a write it did NOT author (an
unmigrated writer editing a projection directly) must be handled, never silently
clobbered:

* U11 — a stray edit to an EXISTING agent's projection is DETECTED (sha vs the
  projector's manifest), ALARMED, and the foreign bytes are QUARANTINED
  (preserved) BEFORE the projector overwrites (ob rider: preserve bytes first,
  else the alarm loses the evidence). Then projector truth is restored.

* U16 (TOP RISK) — a foreign write that ESTABLISHES a brand-new identity the DB
  lacks (a spawn/recovery writer writing straight to JSON) must ALARM + ADOPT
  (ingest via its own txn) or HARD-FAIL — NEVER silent-revert, which would
  orphan a live agent (the C1 lesson one level out). The ADOPT path reuses the
  same-txn stale-sid take-over (ob rider: an adopted identity may carry a sid a
  stale row still holds).

RED until the foreign-write handling / ADOPT path exist.
"""
import json

import pytest

from scripts.identity_store import orchestra_db, projector

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    yield c
    c.close()


def _seed(conn, root=ROOT):
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
                 (root, "T2", "claude"))
    gid = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (root, 1, "sid-1", "model-x")).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,'online')", (root, gid, root))
    conn.execute(
        "INSERT INTO runtime_state (generation_id, status, last_updated) "
        "VALUES (?,'online','t0')", (gid,))
    conn.commit()
    return gid


def _rewrite(path, mutate):
    obj = json.loads(path.read_text())
    mutate(obj)
    path.write_text(json.dumps(obj))


_NEW_SEAT = {
    "tier": "T2", "runtime": "claude", "machine": "vps",
    "generation": 1, "session_id": "new-sid", "model": "model-new",
    "status": "online",
}


# --- U11: stray edit to an EXISTING agent's projection ----------------------

def test_foreign_edit_quarantined_and_alarmed_before_overwrite(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))  # records manifest
    reg = out / "registry.json"
    _rewrite(reg, lambda o: o["agents"][ROOT].__setitem__("status", "TAMPERED"))
    tampered = reg.read_bytes()

    alarms = []
    projector.project(conn, str(out), alarm=alarms.append)

    assert "foreign-write" in [a["kind"] for a in alarms]
    qfiles = list((out / ".quarantine").glob("registry.json.*"))
    assert qfiles and any(f.read_bytes() == tampered for f in qfiles), \
        "foreign bytes must be preserved to quarantine BEFORE overwrite"
    # projector truth restored from the DB (status online)
    assert json.loads(reg.read_text())["agents"][ROOT]["status"] == "online"


# --- U16: identity-ESTABLISHING foreign write ------------------------------

def test_identity_establishing_foreign_write_adopted(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    reg = out / "registry.json"
    _rewrite(reg, lambda o: o["agents"].__setitem__("new-seat", dict(_NEW_SEAT)))

    alarms = []
    projector.project(conn, str(out), alarm=alarms.append,
                      on_foreign_identity="adopt")

    kinds = [a["kind"] for a in alarms]
    assert "foreign-identity" in kinds and "identity-adopted" in kinds
    row = conn.execute(
        "SELECT g.generation, g.session_id, g.model, l.tier "
        "FROM generations g JOIN lineages l ON l.root=g.root "
        "WHERE g.root=?", ("new-seat",)).fetchone()
    assert row is not None
    assert row["session_id"] == "new-sid" and row["model"] == "model-new"
    assert conn.execute("SELECT 1 FROM canonical WHERE root=?",
                        ("new-seat",)).fetchone() is not None
    # regenerated projection now includes the adopted identity
    assert "new-seat" in json.loads(reg.read_text())["agents"]


def test_adopt_takes_over_stale_sid(conn, tmp_path):
    """ob rider: an adopted identity may carry a sid a stale row still holds —
    the ADOPT txn must clear the stale holder in the same transaction."""
    _seed(conn)
    out = tmp_path / "out"
    conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('ghost','T2','claude')")
    ghost = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES ('ghost', 1, 'new-sid', 'm')").lastrowid
    conn.commit()
    projector.project(conn, str(out))
    reg = out / "registry.json"
    _rewrite(reg, lambda o: o["agents"].__setitem__("new-seat", dict(_NEW_SEAT)))

    projector.project(conn, str(out), on_foreign_identity="adopt")

    assert conn.execute("SELECT session_id FROM generations WHERE root='new-seat'"
                        ).fetchone()[0] == "new-sid"
    assert conn.execute("SELECT session_id FROM generations WHERE id=?",
                        (ghost,)).fetchone()[0] is None


def test_identity_establishing_hard_fails_and_never_reverts(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    reg = out / "registry.json"
    _rewrite(reg, lambda o: o["agents"].__setitem__("new-seat", dict(_NEW_SEAT)))
    foreign = reg.read_bytes()

    alarms = []
    with pytest.raises(projector.ForeignIdentityError):
        projector.project(conn, str(out), alarm=alarms.append,
                          on_foreign_identity="hard_fail")

    # DB NOT mutated (no silent adopt)
    assert conn.execute("SELECT 1 FROM lineages WHERE root='new-seat'"
                        ).fetchone() is None
    # foreign bytes preserved (NOT reverted-away)
    qfiles = list((out / ".quarantine").glob("registry.json.*"))
    assert qfiles and any(f.read_bytes() == foreign for f in qfiles)
    assert "foreign-identity" in [a["kind"] for a in alarms]


def test_incomplete_identity_cannot_adopt_and_hard_fails(conn, tmp_path):
    """If the foreign write lacks a NOT NULL field (e.g. model), it cannot be
    safely adopted — the projector HARD-FAILS rather than silent-revert."""
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    reg = out / "registry.json"
    incomplete = {k: v for k, v in _NEW_SEAT.items() if k != "model"}
    _rewrite(reg, lambda o: o["agents"].__setitem__("new-seat", incomplete))

    with pytest.raises((projector.ForeignIdentityError,
                        orchestra_db.IdentityAdoptionError)):
        projector.project(conn, str(out), on_foreign_identity="adopt")

    assert conn.execute("SELECT 1 FROM lineages WHERE root='new-seat'"
                        ).fetchone() is None
