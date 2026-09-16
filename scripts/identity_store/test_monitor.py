"""Phase-2 item-3 (RED) — U12 projector-liveness monitor ARM path.

The projector is a SPOF for ~130 readers from the first dependent read, so its
liveness monitor must be ARMABLE BEFORE the cutover switch — armed independently
of (and ahead of) the flag flip, so a stale/missing projection PAGES from the
very first projection onward. A DISARMED monitor never pages (no false alarms
before arm); an ARMED monitor pages on stale OR missing projection.

RED until ``scripts/identity_store/monitor`` exists.
"""
import pytest

from scripts.identity_store import cutover, monitor, orchestra_db, projector

ROOT = "orchestra-builder"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _projections(orchdir, now):
    """Seed a DB + produce a FAITHFUL projection set at time ``now``; return its dir.

    Under cutover the regenerator daemon runs ``project_faithful`` (header-free
    artifacts + a ``.projection-meta.json`` SIDECAR), so the monitor must check the
    faithful sidecar — not ``project()``'s embedded header (the bonus defect: the
    armed monitor delegated to strangler ``check_liveness`` which reads a header the
    faithful artifacts do not have, and would page spuriously every tick)."""
    import os
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?,?,?)",
              (ROOT, "T2", "claude"))
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?,?,?,?)", (ROOT, 1, "s1", "m")).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?,?,?,'online')", (ROOT, gid, ROOT))
    c.execute("INSERT INTO runtime_state (generation_id, status, last_updated) "
              "VALUES (?,'online','t0')", (gid,))
    c.commit()
    proj = os.path.join(orchdir, "projections")
    projector.project_faithful(c, proj, now=now)
    c.close()
    return proj


# --- arm flag --------------------------------------------------------------

def test_monitor_disarmed_by_default(orchdir):
    assert monitor.is_armed(orchdir) is False


def test_monitor_arm_disarm_toggles(orchdir):
    monitor.arm(orchdir)
    assert monitor.is_armed(orchdir) is True
    monitor.disarm(orchdir)
    assert monitor.is_armed(orchdir) is False


def test_monitor_is_armable_before_cutover(orchdir):
    # the whole point: arm the SPOF monitor BEFORE flipping the switch
    assert cutover.is_active(orchdir) is False
    monitor.arm(orchdir)
    assert monitor.is_armed(orchdir) is True
    assert cutover.is_active(orchdir) is False


# --- check behavior --------------------------------------------------------

def test_disarmed_monitor_never_pages(orchdir):
    proj = _projections(orchdir, now=100.0)
    alarms = []
    # even a very stale projection: disarmed => no page (no false alarms pre-arm)
    res = monitor.check(orchdir, proj, now=100000.0, max_age_s=60.0,
                        alarm=alarms.append)
    assert res["armed"] is False and res["checked"] is False
    assert alarms == []


def test_armed_monitor_healthy_when_fresh(orchdir):
    proj = _projections(orchdir, now=100.0)
    monitor.arm(orchdir)
    alarms = []
    res = monitor.check(orchdir, proj, now=105.0, max_age_s=60.0,
                        alarm=alarms.append)
    assert res["armed"] and res["checked"] and res["healthy"] is True
    assert alarms == []


def test_armed_monitor_pages_when_stale(orchdir):
    proj = _projections(orchdir, now=100.0)
    monitor.arm(orchdir)
    alarms = []
    res = monitor.check(orchdir, proj, now=1000.0, max_age_s=60.0,
                        alarm=alarms.append)
    assert res["healthy"] is False
    assert len(alarms) == 1


def test_armed_monitor_pages_when_projection_missing(orchdir):
    import os
    monitor.arm(orchdir)
    alarms = []
    res = monitor.check(orchdir, os.path.join(orchdir, "never-projected"),
                        now=100.0, max_age_s=60.0, alarm=alarms.append)
    assert res["healthy"] is False
    assert len(alarms) == 1
