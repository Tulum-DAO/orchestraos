"""The repair that un-resets a lineage's generation counter.

gm ran 1..87, restarted at 1, and its live head is now generation 2. `generations` carries
UNIQUE(root, generation) and 3, 36, 37, 38, 39 ... are already taken by the pre-reset
history, so the restarted sequence is on a collision course: the first collision takes the
REUSE branch of _resolve_or_insert_green and a live seat adopts a retired archive row.

These prove the repair moves the label and nothing else.
"""
import json

import pytest

from scripts.identity_store import orchestra_db, repair_generation_reset as rgr

ROOT = "gm"


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "state" / "orchestra-registry.db"
    p.parent.mkdir(parents=True)
    orchestra_db.init_db(str(p))
    c = orchestra_db.get_connection(str(p))
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)", (ROOT, "T0", "claude"))
    yield c
    c.close()


def _gen(conn, root, gen, sid=None):
    return conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) VALUES (?,?,?,?)",
        (root, gen, sid, "m")).lastrowid


def _seed_reset(conn):
    """A lineage that reached 87 and then restarted at 1, 2, 3 — gm's exact shape.

    Note the history does NOT contain 1, 2 or 3: UNIQUE(root, generation) forbids a
    duplicate, and gm's reset landed on numbers that were FREE because they are among the
    32 its history never recorded. The reset reused gaps rather than colliding — which is
    precisely why it committed silently instead of failing.
    """
    for g in (24, 36, 87):              # history, with gm's real gaps
        _gen(conn, ROOT, g, sid=f"old-{g}")
    tail = [_gen(conn, ROOT, g, sid=f"new-{g}") for g in (1, 2, 3)]
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                 "VALUES (?,?,?,?)", (ROOT, tail[1], ROOT, "online"))
    conn.commit()
    return tail


def test_the_tail_is_found_by_insertion_order_not_by_number(conn):
    tail = _seed_reset(conn)
    found, high = rgr.find_reset_tail(conn, ROOT)
    assert high == 87
    # Insertion order is what defines the tail. The low-numbered HISTORY row (24) is not in
    # it, and the post-reset rows are — what makes a row post-reset is being written after
    # the lineage had already reached 87, not the size of its number.
    assert [rid for rid, _ in found] == tail


def test_renumbering_continues_past_the_high_water_mark_and_skips_taken_numbers(conn):
    _seed_reset(conn)
    moves = rgr.plan(conn, ROOT)
    assert [(m["old"], m["new"]) for m in moves] == [(1, 88), (2, 89), (3, 90)]


def test_a_healthy_lineage_is_left_alone(conn):
    for g in (1, 2, 3):
        _gen(conn, ROOT, g, sid=f"s{g}")
    conn.commit()
    assert rgr.find_reset_tail(conn, ROOT)[0] == []
    assert rgr.plan(conn, ROOT) == []


def test_apply_moves_only_the_label(conn, tmp_path, monkeypatch, capsys):
    tail = _seed_reset(conn)
    before = {r["id"]: (r["session_id"], r["model"], r["root"])
              for r in conn.execute("SELECT * FROM generations")}
    canonical_before = conn.execute(
        "SELECT generation_id FROM canonical WHERE root=?", (ROOT,)).fetchone()[0]
    conn.close()

    orch = tmp_path
    (orch / "registry.json").write_text(json.dumps({"agents": {ROOT: {"generation": 2, "tier": "T0"}}}))
    (orch / "state" / "agent-sessions.json").write_text(json.dumps({ROOT: {"generation": 2}}))

    monkeypatch.setattr(sys_argv := __import__("sys"), "argv",
                        ["repair", "--root", ROOT, "--orchestra-dir", str(orch), "--apply"])
    assert rgr.main() == 0

    c = orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))
    try:
        after = {r["id"]: (r["session_id"], r["model"], r["root"])
                 for r in c.execute("SELECT * FROM generations")}
        # Identity is untouched: same rows, same sids, same models. Only the number moved.
        assert after == before
        nums = {r["id"]: r["generation"] for r in c.execute("SELECT id, generation FROM generations")}
        assert [nums[i] for i in tail] == [88, 89, 90]
        # The canonical pointer still names the same ROW (ids never move).
        assert c.execute("SELECT generation_id FROM canonical WHERE root=?",
                         (ROOT,)).fetchone()[0] == canonical_before
    finally:
        c.close()

    # Flat files follow the head (89), so DB-first and flat do not disagree afterwards.
    assert json.loads((orch / "registry.json").read_text())["agents"][ROOT]["generation"] == 89
    assert json.loads((orch / "state" / "agent-sessions.json").read_text())[ROOT]["generation"] == 89
    assert "backup:" in capsys.readouterr().out


def test_dry_run_writes_nothing(conn, tmp_path, monkeypatch, capsys):
    _seed_reset(conn)
    conn.close()
    orch = tmp_path
    (orch / "registry.json").write_text(json.dumps({"agents": {ROOT: {"generation": 2}}}))
    monkeypatch.setattr(__import__("sys"), "argv",
                        ["repair", "--root", ROOT, "--orchestra-dir", str(orch)])
    assert rgr.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "generation 1 -> 88" in out

    c = orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))
    try:
        assert sorted(r[0] for r in c.execute(
            "SELECT generation FROM generations WHERE root=?", (ROOT,))) == [1, 2, 3, 24, 36, 87]
    finally:
        c.close()
    assert json.loads((orch / "registry.json").read_text())["agents"][ROOT]["generation"] == 2


def test_a_lineage_that_already_recovered_is_refused_not_renumbered(conn):
    """The gemini-gm shape, and the bug this guard exists for.

    Rows in insertion order ran 10, 11, 1, 12: the lineage had ALREADY recovered to 12, so
    the anomaly (1) sits in the MIDDLE rather than at the live end. Appending past the
    high-water mark moved that retired row to 13 — exactly the number the next rotation
    mints — manufacturing the collision the repair exists to prevent. I did that to the live
    fleet before this guard existed and had to revert it by hand.

    There is no free integer between 11 and 12, so the anomaly cannot be expressed as a
    renumber at all. It is reported and left alone.
    """
    for g in (10, 11):
        _gen(conn, ROOT, g, sid=f"old-{g}")
    anomaly = _gen(conn, ROOT, 1, sid="anomaly")          # the reset
    recovered = _gen(conn, ROOT, 12, sid="recovered")     # lineage carried on past it
    conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                 "VALUES (?,?,?,?)", (ROOT, recovered, ROOT, "online"))
    conn.commit()

    # The reset is still DETECTED — silence would be its own bug.
    assert [rid for rid, _ in rgr.find_reset_tail(conn, ROOT)[0]] == [anomaly]
    # ...but refused, because the head is not in the tail.
    assert rgr.plan(conn, ROOT) == []
    assert ROOT not in rgr.affected_roots(conn)
    assert rgr.recovered_roots(conn) == [(ROOT, [anomaly])]

    # And above all: the number the next rotation will mint stays FREE.
    taken = {g for (g,) in conn.execute("SELECT generation FROM generations WHERE root=?", (ROOT,))}
    assert 13 not in taken
