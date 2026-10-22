"""GET /agents lineage fields (ios-watch-dev msg_cba15fe1, gm msg_b7f14aab): root + lineage_role
(canonical | green | retired) + lineage_state for a green, from the identity DB and the blue-green
state files, never from a seat's name. Absent = draw as today."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402
from scripts.identity_store.orchestra_db import SCHEMA  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    (tmp_path / "state" / "wal").mkdir(parents=True)
    db = sqlite3.connect(tmp_path / "state" / "orchestra-registry.db")
    db.executescript(SCHEMA)
    for root in ("builder", "builder-g9", "solo", "other-g2"):
        db.execute("INSERT INTO lineages(root, tier, runtime, machine, cwd) VALUES (?,?,?,?,?)",
                   (root, "T1", "claude", "vps", "/x"))
    gens = [  # id, root, generation, session_id, retired_at
        (1, "builder", 7, "sid-blue-old", "2026-10-22T09:00:00Z"),   # the retired blue
        (2, "builder", 8, "sid-canonical", None),                    # canonical now
        (3, "builder-g9", 1, "sid-green", None),                     # a waiting green's placeholder
        (4, "solo", 1, "sid-solo", None),
        (5, "other-g2", 1, "sid-stale", None),
    ]
    for g in gens:
        db.execute("INSERT INTO generations(id, root, generation, session_id, model, retired_at) VALUES (?,?,?,?,?,?)",
                   (*g[:4], "m", g[4]))
    for root, gid, sess, status in (("builder", 2, "builder", "online"), ("builder-g9", 3, "builder-g9", "online"),
                                    ("solo", 4, "solo", "parked"), ("other-g2", 5, "other-g2", "online")):
        db.execute("INSERT INTO canonical VALUES (?,?,?,?)", (root, gid, sess, status))
    db.commit()
    db.close()
    wal = tmp_path / "state" / "wal"
    (wal / "builder.bg.json").write_text(json.dumps(
        {"state": "READY", "root": "builder",
         "meta": {"green_pane_id": "%40", "green_session_id": "sid-green", "green_spawned_at": 1000.0}}))
    (wal / "other.bg.json").write_text(json.dumps(          # a stale link: SOLO keeps an old pane id
        {"state": "SOLO", "root": "other", "meta": {"green_pane_id": "%41"}}))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    # pane -> (session, session_created epoch)
    monkeypatch.setattr(G, "_pane_sessions", lambda: {"%40": ("builder-g9", 1001), "%41": ("other-g2", 900)})
    return tmp_path


REG = {"builder": {"session_id": "sid-canonical"}, "builder-gen7": {"session_id": "sid-blue-old"},
       "solo": {"session_id": "sid-solo"}, "other-g2": {"session_id": "sid-stale"}}


def _rows(*sessions):
    return [{"id": s, "tmux_session": s} for s in sessions]


def test_canonical_green_retired_and_unknown(box):
    rows = _rows("builder", "builder-g9", "builder-gen7", "solo", "nobody")
    G._stamp_lineage(rows, REG)
    got = {r["id"]: (r.get("root"), r.get("lineage_role"), r.get("lineage_state")) for r in rows}
    assert got["builder"] == ("builder", "canonical", None)
    assert got["builder-g9"] == ("builder", "green", "ready")         # from the bg pane link, not the name
    assert got["builder-gen7"] == ("builder", "retired", None)
    assert got["solo"] == ("solo", "canonical", None)                 # parked is still the seat
    assert got["nobody"] == (None, None, None)                        # unknown: absent, draw as today


def test_a_stale_green_link_is_never_a_green(box):
    rows = _rows("other-g2")
    G._stamp_lineage(rows, REG)
    assert rows[0].get("lineage_role") == "canonical" and rows[0].get("root") == "other-g2"


def test_no_identity_db_adds_nothing_and_never_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_pane_sessions", lambda: {})
    rows = _rows("builder")
    G._stamp_lineage(rows, REG)
    assert "lineage_role" not in rows[0] and "root" not in rows[0]


def test_values_are_strings_or_absent_never_null_or_other_types(box):
    rows = _rows("builder", "builder-g9", "builder-gen7", "nobody")
    G._stamp_lineage(rows, REG)
    for r in rows:
        for k in ("root", "lineage_role", "lineage_state"):
            assert k not in r or (isinstance(r[k], str) and r[k])


def test_a_provisional_canonical_pointer_is_not_a_seat_yet(box):
    db = sqlite3.connect(box / "state" / "orchestra-registry.db")
    db.execute("UPDATE canonical SET status='provisional' WHERE root='solo'")
    db.commit()
    db.close()
    rows = _rows("solo")
    G._stamp_lineage(rows, REG)
    assert "lineage_role" not in rows[0]


def _green_file(box, **meta):
    (box / "state" / "wal" / "builder.bg.json").write_text(json.dumps({"state": "READY", "root": "builder", "meta": meta}))


def test_a_reused_pane_id_whose_session_id_differs_is_not_the_green(box):
    _green_file(box, green_pane_id="%40", green_session_id="sid-someone-else", green_spawned_at=1000.0)
    rows = _rows("builder-g9")
    G._stamp_lineage(rows, REG)
    assert rows[0].get("lineage_role") != "green"


def test_a_pane_older_than_the_green_spawn_is_not_the_green(box):
    _green_file(box, green_pane_id="%40", green_spawned_at=5000.0)       # no session id; session created 1001
    rows = _rows("builder-g9")
    G._stamp_lineage(rows, REG)
    assert rows[0].get("lineage_role") != "green"


def test_a_green_link_nothing_can_verify_is_not_used(box):
    _green_file(box, green_pane_id="%40")                                  # no session id, no spawn time
    rows = _rows("builder-g9")
    G._stamp_lineage(rows, REG)
    assert rows[0].get("lineage_role") != "green"


def test_two_roots_claiming_one_session_say_nothing(box):
    (box / "state" / "wal" / "solo.bg.json").write_text(json.dumps(
        {"state": "READY", "root": "solo", "meta": {"green_pane_id": "%40", "green_session_id": "sid-green", "green_spawned_at": 1000.0}}))
    rows = _rows("builder-g9")
    G._stamp_lineage(rows, REG)
    assert "lineage_role" not in rows[0] and "root" not in rows[0]


def test_an_empty_root_is_never_written(box):
    db = sqlite3.connect(box / "state" / "orchestra-registry.db")
    db.execute("INSERT INTO lineages(root, tier, runtime, machine, cwd) VALUES ('', 'T1', 'claude', 'vps', '/x')")
    db.execute("INSERT INTO generations(id, root, generation, session_id, model) VALUES (9, '', 1, 'sid-empty', 'm')")
    db.execute("INSERT INTO canonical VALUES ('', 9, 'empty-seat', 'online')")
    db.commit()
    db.close()
    rows = _rows("empty-seat")
    G._stamp_lineage(rows, REG)
    assert "root" not in rows[0] and "lineage_role" not in rows[0]
