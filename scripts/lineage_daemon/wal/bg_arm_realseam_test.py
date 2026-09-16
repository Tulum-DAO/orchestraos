"""REAL-seam regression for bg_arm — the fake-only gap the 235+ unit tests missed.

The unit tests inject PURE FAKE seams, so they never hit the real generations
NOT-NULL constraint. On the live first fire, bg_arm._register_then_project_then_spawn
called register_provisional(root, green_alias) with NO generation -> the real
identity_writer.register_provisional INSERTed generation=NULL ->
sqlite3.IntegrityError. The green generation is available in obs['green']['generation']
(blue+1) but was never threaded into the register seam.

These tests stand up a REAL scratch identity DB (lineage + blue gen1 + cutover active)
and drive the arm's SOLO->PREWARMING beat through the REAL register_provisional +
project_now seams, asserting the green (gen2) provisional row is registered — RED
before the threading fix, GREEN after.
"""
import os
import sys

import pytest

sys.path.insert(0, "scripts")
from identity_store import orchestra_db, cutover, identity_writer  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal import real_seams  # noqa: E402

ROOT = "bg-drill-victim"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "wal").mkdir()
    return str(tmp_path)


def _seed_blue_gen1(orchdir):
    """A real identity DB: lineage + canonical blue gen1, cutover ARMED."""
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
              f"VALUES ('{ROOT}','T2','claude','vps','/x',1)")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    f"VALUES ('{ROOT}',1,'sid-blue','claude-opus-4-8[1m]')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              f"VALUES ('{ROOT}',?,'{ROOT}','online')", (gid,))
    c.close()
    cutover.arm(orchdir)
    return gid


def _real_register_seams(orchdir, wal_dir, *, spawn_log):
    """A seams object using the REAL register_provisional + project_now seams (the
    ones that hit the DB), with spawn/verify/hydrate/reap as harmless fakes so the
    test isolates the register defect. project_now is faked True (no projector here)."""
    class _S:
        register_provisional = staticmethod(
            real_seams.make_register_provisional_fn(orchdir))
        # project_now faked: the real one needs a live projector; not under test here.
        project_now = staticmethod(lambda root: True)
        swap = staticmethod(lambda *a, **k: None)

        def spawn(self, root, green_alias):
            spawn_log.append((root, green_alias))

        def verify(self, root, green_alias):
            return False

        def hydrate(self, root, green_alias, since_seq):
            pass

        def reap(self, root, blue):
            pass
    return _S()


def _obs(blue_gen=1):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": 0.72, "death": None,
            "ceiling_calibrated": True, "blue_generation_id": 1,
            "green": {"generation": blue_gen + 1, "model": "claude-opus-4-8[1m]"}}


def test_solo_prewarm_registers_green_gen2_via_real_seam(orchdir):
    """RED before fix (NULL generation IntegrityError), GREEN after: the SOLO
    prewarm beat registers the green as a REAL provisional generation = blue+1."""
    _seed_blue_gen1(orchdir)
    wal = os.path.join(orchdir, "state", "wal")
    (open(os.path.join(wal, f"{ROOT}.bg_enabled"), "w")).close()
    spawn_log = []
    seams = _real_register_seams(orchdir, wal, spawn_log=spawn_log)
    arm = BgArm(wal, ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: cutover.is_active(orchdir))

    arm.beat(_obs())   # SOLO -> PREWARMING: register_provisional(green gen2) must succeed

    # the green provisional generation (2) is a REAL row in the DB (NOT NULL satisfied)
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    conn = orchestra_db.get_connection(dbp)
    row = conn.execute(
        "SELECT generation FROM generations WHERE root=? AND generation=2",
        (ROOT,)).fetchone()
    conn.close()
    assert row is not None, "green gen2 must be registered as a real provisional row"
    assert row["generation"] == 2
    # SECOND fake-only gap (same class): the Green alias must be the PROJECTED
    # provisional name `{root}-g{N}` (bg-drill-victim-g2), NOT the literal
    # `-g-green` — spawn-agent/verify/hydrate key on the registered alias, so the
    # literal would spawn/probe an unregistered name (auto-register-refuse).
    assert spawn_log == [(ROOT, f"{ROOT}-g2")]


def test_register_seam_receives_the_green_generation(orchdir):
    """Directly pin the contract the bug violated: the register seam is called WITH
    the green generation (not None), so the real DB insert never sees NULL."""
    _seed_blue_gen1(orchdir)
    wal = os.path.join(orchdir, "state", "wal")
    (open(os.path.join(wal, f"{ROOT}.bg_enabled"), "w")).close()

    captured = {}
    real_reg = real_seams.make_register_provisional_fn(orchdir)

    class _S:
        def register_provisional(self, root, green_alias, generation=None, model=None):
            captured["generation"] = generation
            return real_reg(root, green_alias, generation=generation, model=model)
        project_now = staticmethod(lambda root: True)
        swap = staticmethod(lambda *a, **k: None)
        def spawn(self, root, green_alias): pass
        def verify(self, root, green_alias): return False
        def hydrate(self, root, green_alias, since_seq): pass
        def reap(self, root, blue): pass

    arm = BgArm(wal, ROOT, seams=_S(), blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: cutover.is_active(orchdir))
    arm.beat(_obs(blue_gen=1))
    assert captured.get("generation") == 2, (
        "the arm must thread obs['green']['generation'] (=2) into register_provisional; "
        "None is the bug that INSERTed a NULL generation on the live fire")
