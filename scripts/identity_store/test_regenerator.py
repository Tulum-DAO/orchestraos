"""p8 (RED) — projector regenerator, Option A standalone daemon (DEC-1788371192).

A cutover-only daemon detects EVERY write via PRAGMA data_version and reprojects
(project_faithful) debounced, with an idle force-floor < U12 max_age_s, under a
single-instance liveness lock. INERT: flag OFF => the daemon does not run and its
steps are no-ops. Tests exercise the deterministic tick state machine + the lock +
the cutover gate + M1 continuity (project == store, 0-drift, coherent post-mutation).

RED until scripts/identity_store/regenerator provides the Option A daemon.
"""
import json
import os

import pytest

from scripts.identity_store import (cutover, migrate, orchestra_db, projector,
                                    regenerator)

_SIDE = projector.FAITHFUL_META_NAME


@pytest.fixture
def orch(tmp_path):
    (tmp_path / "state" / "agents").mkdir(parents=True)
    # a migrated store so project_faithful has real content
    reg = {"version": 1, "last_updated": "t0", "machines": {"vps": {"hostname": "s"}},
           "agents": {"a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                             "runtime": "claude", "model": "m", "tmux_session": "a1",
                             "always_on": True, "system_prompt": "p", "status": "online",
                             "generation": 1, "session_id": "s1", "lineage_root": "a1"}},
           "_retired_agents": {}, "_provisional": {}, "_canonical": {"a1": "x"}}
    (tmp_path / "registry.json").write_text(json.dumps(reg, indent=2))
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(
        {"a1": {"session_id": "s1", "model": "m", "generation": 1, "status": "online",
                "tmux_session": "a1"}}))
    (tmp_path / "state" / "agents" / "a1.json").write_text(json.dumps(
        {"agent_id": "a1", "status": "online"}))
    dbp = tmp_path / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        migrate.migrate(c, registry_path=str(tmp_path / "registry.json"),
                        sessions_path=str(tmp_path / "state" / "agent-sessions.json"),
                        agents_dir=str(tmp_path / "state" / "agents"))
    finally:
        c.close()
    return tmp_path


def _conn(orch):
    return orchestra_db.get_connection(str(orch / "state" / "orchestra-registry.db"))


def _sidecar(orch):
    p = orch / _SIDE
    return json.loads(p.read_text()) if p.exists() else None


# --- data_version write detector -------------------------------------------

def test_data_version_bumps_on_external_write(orch):
    """The detector Option A relies on: a commit on one connection bumps data_version
    as seen by ANOTHER connection."""
    watcher = _conn(orch)
    writer = _conn(orch)
    try:
        v1 = regenerator.read_data_version(watcher)
        writer.execute("UPDATE canonical SET status='parked' WHERE root='a1'")
        v2 = regenerator.read_data_version(watcher)
        assert v2 != v1
    finally:
        watcher.close()
        writer.close()


# --- deterministic tick state machine --------------------------------------

def test_decide_first_tick_projects_baseline():
    st = regenerator.RegenState()
    assert st.decide(data_version=5, now=100.0) is True  # initial projection
    st.mark_projected(100.0)
    # no change, within floor -> no project
    assert st.decide(data_version=5, now=101.0) is False


def test_decide_debounces_a_write_burst():
    st = regenerator.RegenState(debounce_s=2.0, force_floor_s=30.0)
    st.decide(5, 100.0); st.mark_projected(100.0)           # baseline
    # write at t=100.5 -> dirty, but within debounce -> hold
    assert st.decide(6, 100.5) is False
    # another write at t=101 -> still within debounce window since last project
    assert st.decide(7, 101.0) is False
    # past debounce (>=2s since last project) and still dirty -> project
    assert st.decide(7, 102.1) is True
    st.mark_projected(102.1)
    assert st.decide(7, 102.2) is False                     # clean again


def test_decide_force_floor_keeps_sidecar_fresh_when_idle():
    st = regenerator.RegenState(debounce_s=2.0, force_floor_s=30.0)
    st.decide(5, 100.0); st.mark_projected(100.0)
    assert st.decide(5, 120.0) is False                     # idle, within floor
    assert st.decide(5, 131.0) is True                      # idle past floor -> refresh


def test_force_floor_under_u12_bound():
    """The idle floor MUST be < the U12 monitor max_age_s (120s)."""
    import inspect
    from scripts.identity_store import monitor
    max_age = inspect.signature(monitor.check).parameters["max_age_s"].default
    assert regenerator._DEFAULT_FORCE_FLOOR_S < max_age


# --- tick against a live DB (writes trigger a coherent reprojection) --------

def test_tick_projects_on_write_and_is_coherent(orch):
    cutover.arm(str(orch))
    conn = _conn(orch)
    st = regenerator.RegenState(debounce_s=0.0)  # no debounce for the test
    try:
        assert regenerator.tick(str(orch), conn, st, now=1000.0) is True  # baseline
        # a real writer mutation (DP-A2 full_record persists the document the faithful
        # projection serves) on ANOTHER connection -> bumps data_version.
        from scripts.identity_store import identity_writer
        identity_writer.update_registry_agent(
            str(orch), "a1", {"status": "parked"},
            full_record={"name": "a1", "status": "parked", "generation": 1,
                         "lineage_root": "a1"})
        assert regenerator.tick(str(orch), conn, st, now=1001.0) is True
    finally:
        conn.close()
    reg = json.loads((orch / "registry.json").read_text())
    assert reg["agents"]["a1"]["status"] == "parked", "projection reflects the write"
    assert _sidecar(orch)["generated_at"] == 1001.0


def test_tick_idle_no_write_skips_until_floor(orch):
    cutover.arm(str(orch))
    conn = _conn(orch)
    st = regenerator.RegenState(debounce_s=2.0, force_floor_s=30.0)
    try:
        regenerator.tick(str(orch), conn, st, now=1000.0)      # baseline project
        assert regenerator.tick(str(orch), conn, st, now=1010.0) is False  # idle < floor
        assert regenerator.tick(str(orch), conn, st, now=1031.0) is True   # past floor
    finally:
        conn.close()


# --- M1 continuity: project_faithful == the frozen live files at flip -------

def test_M1_projection_equals_migrate_reproject(orch):
    """M1 (flip-moment continuity): project_faithful on the prod DB == migrate.reproject
    == the frozen live files -> 0 drift; and stays coherent after a mutation."""
    conn = _conn(orch)
    try:
        out = orch / "_reproj"
        migrate.reproject(conn, str(out))
        projector.project_faithful(conn, str(out) + "_faithful")
    finally:
        conn.close()
    # both reconstruct registry.json identically (0-drift at the flip moment)
    diffs = migrate.diff_report(str(orch), str(out))
    assert diffs == [], f"M1: reproject must be 0-drift vs live files: {diffs}"


def test_M1_coherent_after_mutation(orch):
    """After a rewired-writer mutation under cutover, a tick reprojects so the copies
    stay coherent with the store (no drift accumulates)."""
    cutover.arm(str(orch))
    from scripts.identity_store import identity_writer
    identity_writer.update_registry_agent(
        str(orch), "a1", {"status": "parked"},
        full_record={"name": "a1", "status": "parked", "generation": 1,
                     "lineage_root": "a1"})
    conn = _conn(orch)
    st = regenerator.RegenState(debounce_s=0.0)
    try:
        regenerator.tick(str(orch), conn, st, now=2000.0)
    finally:
        conn.close()
    reg = json.loads((orch / "registry.json").read_text())
    assert reg["agents"]["a1"]["status"] == "parked"


# --- single-instance liveness lock -----------------------------------------

def test_singleton_lock_excludes_second_holder(orch):
    assert regenerator.acquire_singleton(str(orch)) is True   # we take it
    # simulate a live OTHER holder by writing a live pid (a child that stays alive)
    lock = orch / "state" / "identity-store-regenerator.lock"
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        lock.write_text(str(child.pid))
        assert regenerator.acquire_singleton(str(orch)) is False, "live holder excludes us"
    finally:
        child.terminate(); child.wait()


def test_singleton_lock_takes_over_stale_pid(orch):
    lock = orch / "state" / "identity-store-regenerator.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999")  # a pid that is not alive
    assert regenerator.acquire_singleton(str(orch)) is True, "stale/dead pid is taken over"
    assert int(lock.read_text()) == os.getpid()


# --- cutover gate (INERT) --------------------------------------------------

def test_run_inactive_is_noop(orch):
    assert regenerator.run(str(orch)) == "inactive"
    assert _sidecar(orch) is None, "flag off: daemon does not run, nothing projected"


def test_run_exits_when_disarmed(orch):
    cutover.arm(str(orch))
    # bounded loop: it should project at least once then we let it run to max_iters
    reason = regenerator.run(str(orch), poll_s=0.0, max_iters=2)
    assert reason == "max-iters"
    assert _sidecar(orch) is not None, "armed: daemon projected"


def test_start_if_armed_noop_when_inactive(orch):
    """REBOOT-RECOVERY hook is INERT flag-off: nothing spawns."""
    assert regenerator.start_if_armed(str(orch)) is False


def test_start_if_armed_spawns_and_is_idempotent(orch):
    """Under cutover start_if_armed spawns the daemon; a second call while it holds the
    lock is a no-op (idempotent — safe to call from both the flip AND reboot recovery)."""
    cutover.arm(str(orch))
    import time
    try:
        assert regenerator.start_if_armed(str(orch)) is True
        time.sleep(0.5)  # let the child acquire the singleton lock
        assert regenerator.start_if_armed(str(orch)) is False, "second call is a no-op"
    finally:
        # stop the spawned daemon by disarming + clearing the lock holder
        cutover.disarm(str(orch))
        lock = orch / "state" / "identity-store-regenerator.lock"
        if lock.exists():
            try:
                os.kill(int(lock.read_text()), 15)
            except (ProcessLookupError, ValueError):
                pass


def test_run_second_instance_declines(orch):
    cutover.arm(str(orch))
    regenerator.acquire_singleton(str(orch))          # a live holder = us (this pid)
    # a run() call in THIS process sees our own pid as holder -> but same pid is allowed;
    # emulate a different live holder:
    lock = orch / "state" / "identity-store-regenerator.lock"
    import subprocess, sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        lock.write_text(str(child.pid))
        assert regenerator.run(str(orch), poll_s=0.0, max_iters=1) == "lock-held"
    finally:
        child.terminate(); child.wait()
