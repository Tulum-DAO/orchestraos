"""Piece-3 — U1: a concurrent reader sees fully-old-or-fully-new, never a mix.

Now that there are projections to observe, U1 is testable: while swaps loop and
the projector regenerates registry.json, a reader hammering that file must always
see a COMPLETE, internally-consistent snapshot — the canonical agent's
``generation`` N always paired with its ``session_id`` ``sid-N`` (the swap writes
both together; the projector reads one DB snapshot), never gen from one cycle
with a sid from another, and never a partial/parse-failed file.

This guards the os.replace atomicity delivered in piece-3a. The negative control
proves the assertion has teeth: a NON-atomic writer IS observable as a torn read.
"""
import json
import threading
import time

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
        "VALUES (?,?,?,?)", (root, 1, "sid-1", "model-1")).lastrowid
    conn.execute(
        "INSERT INTO canonical (root, generation_id, tmux_session, status) "
        "VALUES (?,?,?,'online')", (root, gid, root))
    conn.execute(
        "INSERT INTO runtime_state (generation_id, status, last_updated) "
        "VALUES (?,'online','t0')", (gid,))
    conn.commit()
    return gid


def test_concurrent_reader_sees_consistent_snapshot(conn, tmp_path):
    blue = _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out))
    reg = out / "registry.json"

    stop = threading.Event()
    errors = []
    reads = [0]

    def reader():
        while not stop.is_set():
            try:
                obj = json.loads(reg.read_text())
            except ValueError as e:            # partial/torn file
                errors.append(("parse", repr(e)))
                continue
            agent = obj.get("agents", {}).get(ROOT)
            if not agent:
                continue
            gen, sid = agent.get("generation"), agent.get("session_id")
            # fully-old-or-fully-new: gen N must carry sid-N, never a cross
            if sid is None or int(sid.split("-")[1]) != gen:
                errors.append(("mismatch", gen, sid))
            reads[0] += 1

    t = threading.Thread(target=reader)
    t.start()
    try:
        gen = 1
        for _ in range(150):
            gen += 1
            orchestra_db.execute_swap(
                conn, ROOT,
                green={"generation": gen, "session_id": f"sid-{gen}",
                       "model": f"model-{gen}"},
                blue_generation_id=blue, now=f"t{gen}")
            blue = conn.execute(
                "SELECT generation_id FROM canonical WHERE root=?",
                (ROOT,)).fetchone()[0]
            projector.project(conn, str(out))
    finally:
        stop.set()
        t.join(timeout=5)

    assert errors == [], f"reader saw torn/mixed snapshots: {errors[:5]}"
    assert reads[0] > 0, "reader observed no projection"


def test_nonatomic_write_tears_reader_negative_control(tmp_path):
    """Control: a NON-atomic writer (truncate then write with a gap) IS
    observable as a torn read — proving os.replace atomicity is what prevents
    the mix above, and that the parse assertion has teeth."""
    target = tmp_path / "registry.json"
    target.write_text(json.dumps({"agents": {}, "_projection": {"snapshot_id": "a"}}))
    payload = json.dumps(
        {"agents": {str(i): i for i in range(3000)},
         "_projection": {"snapshot_id": "b"}})
    torn = []

    def slow_writer():
        with open(target, "w") as fh:      # truncates immediately (non-atomic)
            fh.write(payload[:len(payload) // 2])
            fh.flush()
            time.sleep(0.2)
            fh.write(payload[len(payload) // 2:])

    w = threading.Thread(target=slow_writer)
    w.start()
    time.sleep(0.05)                       # read during the gap
    for _ in range(30):
        try:
            json.loads(target.read_text())
        except ValueError:
            torn.append(True)
            break
        time.sleep(0.01)
    w.join()

    assert torn, "a non-atomic writer must be observable as a torn read"
