"""Piece-3 (RED) — U12 projector liveness + U15 heartbeat debounce.

During the strangler window the projector is a NEW single point of failure for
~130 legacy readers. Two protections:

* U12 (liveness): projection age is staleness-bounded — if the newest projection
  is older than the bound (or missing entirely, i.e. the projector crashed), a
  liveness check must ALARM/PAGE, never let the fleet silently read a frozen
  view.
* U15 (debounce): the projector coalesces rapid ``runtime_state`` heartbeat
  mutations into at most one write per debounce window, so a heartbeat storm
  cannot cause a projection write-storm — while still honoring the U12 bound.

RED until ``check_liveness`` / ``Projector`` exist.
"""
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


# --- U12 liveness ----------------------------------------------------------

def test_liveness_ok_when_projection_fresh(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out), now=100.0)
    alarms = []
    ok = projector.check_liveness(str(out), now=105.0, max_age_s=60.0,
                                  alarm=alarms.append)
    assert ok is True
    assert alarms == []


def test_liveness_alarms_when_projection_stale(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    projector.project(conn, str(out), now=100.0)
    alarms = []
    ok = projector.check_liveness(str(out), now=200.0, max_age_s=60.0,
                                  alarm=alarms.append)
    assert ok is False, "a stale projection must fail the liveness check"
    assert len(alarms) == 1, "stale projection must PAGE"


def test_liveness_alarms_when_projection_missing(conn, tmp_path):
    out = tmp_path / "out"  # projector never ran => crashed
    alarms = []
    ok = projector.check_liveness(str(out), now=100.0, max_age_s=60.0,
                                  alarm=alarms.append)
    assert ok is False
    assert len(alarms) == 1, "a missing projection (crashed projector) must PAGE"


# --- U15 debounce ----------------------------------------------------------

class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_debounce_coalesces_rapid_marks(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    clk = _Clock(0.0)
    p = projector.Projector(conn, str(out), debounce_s=2.0, clock=clk)

    p.mark_dirty()
    assert p.maybe_project() is True, "first dirty pass projects immediately"

    projected = sum(1 for _ in range(100) if (p.mark_dirty() or p.maybe_project()))
    assert projected == 0, "rapid marks within the window must coalesce (U15)"

    clk.t = 3.0  # advance past the debounce window
    p.mark_dirty()
    assert p.maybe_project() is True, "a mark after the window projects again"


def test_debounce_skips_when_not_dirty(conn, tmp_path):
    _seed(conn)
    out = tmp_path / "out"
    clk = _Clock(0.0)
    p = projector.Projector(conn, str(out), debounce_s=2.0, clock=clk)
    p.mark_dirty()
    p.maybe_project()
    clk.t = 100.0
    assert p.maybe_project() is False, "no dirty state => no projection, even past the window"
