"""F2 (RED) — G7 live finding: promoted gen's TYPED session_id is None after a
provisional->canonical swap, so UNIQUE(session_id) protection + stale-holder clears
do not cover the new gen's sid.

Live evidence (gm G7): gen6.session_id == None after the live swap+snapshot while
gen5 correctly held its sid. Root cause: rotate_agent registers the successor as a
PROVISIONAL generation at Step-3 (session_id NULL); at promote the swap's
``_resolve_or_insert_green`` finds that EXISTING (root,generation) row and returns its
id WITHOUT writing green's session_id — even though the green dict carries the resolved
sid (promote sets sess_entry["session_id"]=sid before the swap). So the positive
attribution never lands on the typed column.

FIX: the swap must attribute green's session_id to the (possibly pre-existing) green
generation row inside the swap txn, reusing the same-txn stale-holder take-over (U8).
After the swap the typed column == the live sid; a second gen claiming the same sid
trips UNIQUE (the protection is load-bearing again).

RED until execute_swap attributes the sid to an existing green row.
"""
import pytest

from scripts.identity_store import orchestra_db

ROOT = "orchestra-builder"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "orchestra-registry.db"
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    blue_id = c.execute(
        "INSERT INTO generations (root, generation, session_id, model) "
        "VALUES (?,?,?,?)", (ROOT, 5, "blue-sid-5", "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, blue_id, ROOT))
    c.commit()
    yield c, blue_id
    c.close()


def _gen_sid(conn, root, generation):
    row = conn.execute("SELECT session_id FROM generations WHERE root=? AND generation=?",
                       (root, generation)).fetchone()
    return row["session_id"] if row else None


def test_swap_attributes_sid_to_preexisting_provisional_green(conn):
    """The live path: a PROVISIONAL green row already exists (session_id NULL, as
    rotate_agent registered at Step-3). The swap must write green's sid onto it."""
    c, blue_id = conn
    # rotate_agent Step-3: provisional non-canonical generation, session_id NULL
    c.execute("INSERT INTO generations (root, generation, session_id, model, spawned_at) "
              "VALUES (?,6,NULL,?,'t-spawn')", (ROOT, "m"))
    c.commit()
    # promote resolves the successor sid and passes it in green
    orchestra_db.execute_swap(
        c, ROOT, green={"generation": 6, "session_id": "green-sid-6", "model": "m"},
        blue_generation_id=blue_id, now="t2")
    assert _gen_sid(c, ROOT, 6) == "green-sid-6", \
        "F2: the promoted gen's TYPED session_id must be the live sid, not None"


def test_swap_sets_sid_when_green_row_is_new(conn):
    """Regression guard: the fresh-insert path (no pre-existing row) still sets the
    sid (this already worked via _GREEN_OPTIONAL_COLS)."""
    c, blue_id = conn
    orchestra_db.execute_swap(
        c, ROOT, green={"generation": 7, "session_id": "green-sid-7", "model": "m"},
        blue_generation_id=blue_id, now="t2")
    assert _gen_sid(c, ROOT, 7) == "green-sid-7"


def test_attributed_sid_is_unique_load_bearing(conn):
    """After attribution the UNIQUE(session_id) protection is load-bearing again:
    a second generation cannot claim the promoted gen's sid."""
    c, blue_id = conn
    c.execute("INSERT INTO generations (root, generation, session_id, model, spawned_at) "
              "VALUES (?,6,NULL,?,'t-spawn')", (ROOT, "m"))
    c.commit()
    orchestra_db.execute_swap(
        c, ROOT, green={"generation": 6, "session_id": "green-sid-6", "model": "m"},
        blue_generation_id=blue_id, now="t2")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO generations (root, generation, session_id, model) "
                  "VALUES (?,99,'green-sid-6','m')", (ROOT,))


def test_swap_takes_over_stale_sid_holder(conn):
    """If a stale generation still holds the sid (post-crash/resume — exactly when
    attribution matters), the swap clears it and assigns to green in the SAME txn."""
    c, blue_id = conn
    # a stale corpse holding the sid green will claim
    c.execute("INSERT INTO generations (root, generation, session_id, model) "
              "VALUES (?,4,'green-sid-6','m')", (ROOT,))
    c.execute("INSERT INTO generations (root, generation, session_id, model, spawned_at) "
              "VALUES (?,6,NULL,?,'t-spawn')", (ROOT, "m"))
    c.commit()
    orchestra_db.execute_swap(
        c, ROOT, green={"generation": 6, "session_id": "green-sid-6", "model": "m"},
        blue_generation_id=blue_id, now="t2")
    assert _gen_sid(c, ROOT, 6) == "green-sid-6", "green claims the sid"
    assert _gen_sid(c, ROOT, 4) is None, "the stale holder was cleared in-txn"
