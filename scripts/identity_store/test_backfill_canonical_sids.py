"""F2-retroactive (RED) — one-time idempotent backfill of typed session_id for
canonical generations promoted BEFORE the F2 fix.

G7's live gen6 (all-model-parity) was promoted before F2 landed, so its TYPED
generations.session_id is None RIGHT NOW while its real sid (854c06ef) lives only in
the document. The CURRENT live canonical gen therefore has no UNIQUE(session_id)
protection. Backfill: for every CANONICAL, non-retired generation whose typed
session_id IS NULL but whose live document carries a sid, attribute it (U8 take-over).
Idempotent; archived gens out of scope.

RED until orchestra_db.backfill_canonical_session_ids exists.
"""
import json
import sqlite3

import pytest

from scripts.identity_store import orchestra_db

ROOT = "all-model-parity"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    # gen6 canonical, typed session_id NULL (the pre-F2 promoted state)
    g6 = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                   "VALUES (?,6,NULL,'m')", (ROOT,)).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, g6, ROOT))
    # the document carries the real sid (agent-sessions.json session doc)
    c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
              "VALUES ('agent-sessions.json','session',?,0,?)",
              (ROOT, json.dumps({"session_id": "854c06ef", "generation": 6})))
    c.commit()
    yield c, g6
    c.close()


def _gen_sid(conn, root, generation):
    return conn.execute("SELECT session_id FROM generations WHERE root=? AND generation=?",
                        (root, generation)).fetchone()["session_id"]


def test_backfill_attributes_gen6_typed_sid(conn):
    c, _ = conn
    res = orchestra_db.backfill_canonical_session_ids(c)
    assert (ROOT, 6, "854c06ef") in res["attributed"]
    assert _gen_sid(c, ROOT, 6) == "854c06ef", \
        "gen6's typed session_id must be backfilled from the document"


def test_backfill_restores_unique_protection(conn):
    c, _ = conn
    orchestra_db.backfill_canonical_session_ids(c)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO generations (root, generation, session_id, model) "
                  "VALUES (?,99,'854c06ef','m')", (ROOT,))


def test_backfill_is_idempotent(conn):
    c, _ = conn
    orchestra_db.backfill_canonical_session_ids(c)
    second = orchestra_db.backfill_canonical_session_ids(c)
    assert second["attributed"] == [], "a second run attributes nothing (already backfilled)"
    assert _gen_sid(c, ROOT, 6) == "854c06ef"


def test_backfill_idempotent_with_multi_gen_per_root_conflict(tmp_path):
    """THE live churn (gm G7): a migration archive lineage (`<root>__retired-genN`,
    itself canonical+non-retired in the table) whose DOCUMENT sid points at a LIVE
    lineage's sid must NOT steal it — the take-over used to clear the live holder,
    corrupting it and ping-ponging on re-run. Backfill must SKIP the conflict, leave the
    live holder intact, and be idempotent (run2 attributes 0)."""
    p = tmp_path / "db.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    try:
        # a LIVE lineage that already holds its sid (NOT a backfill target)
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('gm-gen5','T2','claude')")
        live = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                         "VALUES ('gm-gen5',5,'sid-A','m')").lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES ('gm-gen5',?,'gm-gen5','online')", (live,))
        # a migration ARCHIVE lineage: canonical+non-retired, typed sid NULL, whose
        # registry doc sid == the LIVE lineage's sid (the contamination source)
        c.execute("INSERT INTO lineages (root, tier, runtime) "
                  "VALUES ('gm__retired-gen5','T2','claude')")
        arch = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                         "VALUES ('gm__retired-gen5',1,NULL,'m')").lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES ('gm__retired-gen5',?,'gm__retired-gen5','online')", (arch,))
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES ('registry.json','agent','gm__retired-gen5',0,?)",
                  (json.dumps({"session_id": "sid-A"}),))
        c.commit()

        r1 = orchestra_db.backfill_canonical_session_ids(c)
        # the live holder is UNTOUCHED; the archive is SKIPPED as a conflict
        assert _gen_sid(c, "gm-gen5", 5) == "sid-A", "live holder must not be cleared"
        assert _gen_sid(c, "gm__retired-gen5", 1) is None, "archive must not steal the sid"
        assert ("gm__retired-gen5", "sid-A", "gm-gen5") in r1["conflicts"]
        # IDEMPOTENT: a second run changes NOTHING
        r2 = orchestra_db.backfill_canonical_session_ids(c)
        assert r2["attributed"] == [], "run2 must attribute 0 (no churn)"
        assert _gen_sid(c, "gm-gen5", 5) == "sid-A"
    finally:
        c.close()


def test_backfill_takes_over_stale_holder(tmp_path):
    """A sid held by a STALE row (retired) IS taken over — the live canonical target
    should own it. (Only VALID canonical non-retired holders are protected.)"""
    p = tmp_path / "db.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    try:
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('r','T2','claude')")
        # stale retired holder of sid-S
        c.execute("INSERT INTO generations (root, generation, session_id, model, retired_at) "
                  "VALUES ('r',1,'sid-S','m','t-ret')")
        live = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                         "VALUES ('r',2,NULL,'m')").lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES ('r',?,'r','online')", (live,))
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES ('agent-sessions.json','session','r',0,?)",
                  (json.dumps({"session_id": "sid-S"}),))
        c.commit()
        res = orchestra_db.backfill_canonical_session_ids(c)
        assert _gen_sid(c, "r", 2) == "sid-S", "live canonical target takes over the stale sid"
        assert ("r", 2, "sid-S") in res["attributed"]
    finally:
        c.close()


def test_backfill_skips_gen_with_no_doc_sid(conn):
    """A canonical gen whose document has NO sid stays None (nothing to attribute)."""
    c, _ = conn
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('nosid','T2','claude')")
    g = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                  "VALUES ('nosid',1,NULL,'m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('nosid',?,'nosid','online')", (g,))
    c.commit()
    orchestra_db.backfill_canonical_session_ids(c)
    assert _gen_sid(c, "nosid", 1) is None


def test_backfill_skips_retired_gen(conn):
    """Archived/retired gens are out of scope even if their doc carries a sid."""
    c, _ = conn
    c.execute("INSERT INTO generations (root, generation, session_id, model, retired_at) "
              "VALUES (?,5,NULL,'m','t-ret')", (ROOT,))
    c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
              "VALUES ('agent-sessions.json','session',?,0,?)",
              (f"{ROOT}-gen5", json.dumps({"session_id": "old5"})))
    c.commit()
    orchestra_db.backfill_canonical_session_ids(c)
    assert _gen_sid(c, ROOT, 5) is None, "retired gen must not be backfilled"


def test_backfill_falls_back_to_registry_doc_sid(tmp_path):
    """When the session doc lacks a sid but the registry agent doc has one, use it."""
    p = tmp_path / "db.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    try:
        c.execute("INSERT INTO lineages (root, tier, runtime) VALUES ('r','T2','claude')")
        g = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                      "VALUES ('r',1,NULL,'m')").lastrowid
        c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                  "VALUES ('r',?,'r','online')", (g,))
        c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
                  "VALUES ('registry.json','agent','r',0,?)",
                  (json.dumps({"session_id": "reg-sid"}),))
        c.commit()
        orchestra_db.backfill_canonical_session_ids(c)
        assert _gen_sid(c, "r", 1) == "reg-sid"
    finally:
        c.close()
