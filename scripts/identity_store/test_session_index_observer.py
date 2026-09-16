"""Writer-resume p4 (RED) — session-index reconcile_registry becomes OBSERVER-ONLY under cutover.

`reconcile_registry()` (session-index.py:590) is the */10 auto-register writer: it mints a
registry.json entry (session-index.py:619-628, write :635-637) for any live tmux session
absent from registry.json. Under the identity-store cutover with DEC-1788346974 (b)+(b1)
(no-partial-identities + alarm-only), that mint is FORBIDDEN — a raw-tmux session carries no
generation/model/runtime, so auto-registering it fabricates a PARTIAL identity (the exact
splinter the store abolishes; cf. the 16 archived gm-gen* generation=1 seats). Under cutover
the writer must:
  - NOT write registry.json (no mint),
  - emit an ALARM whose payload names the session + lists the missing _REQUIRED_ADOPT fields
    plus a one-paste adopt recipe (RED#1),
  - SKIP and take NO fleet-affecting action (b1: alarm-only, never auto-quiesce).
Flag OFF stays byte-identical (auto-registers exactly as before — INERT).

RED until reconcile_registry grows the cutover observer seam.
"""
import importlib.util
import json
import os
import subprocess as _subprocess

import pytest

_SI = os.path.join(os.path.dirname(__file__), "..", "session-index.py")


def _load():
    spec = importlib.util.spec_from_file_location("session_index_p4", _SI)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    m = _load()
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(
        {"agents": {"known-a": {"name": "known-a", "tmux_session": "known-a"}}},
        indent=2))
    monkeypatch.setattr(m, "REGISTRY_FILE", reg)
    monkeypatch.setattr(m, "ORCHESTRA_DIR", tmp_path)
    # CV4 observe is a maintenance side effect — stub it so the test is hermetic.
    monkeypatch.setattr(m, "_cv4_observe_session_index", lambda *a, **k: None)

    # live tmux: one KNOWN session (already registered) + one unknown raw-tmux ghost.
    class _R:
        returncode = 0
        stdout = "known-a|/home/testuser\nraw-ghost|/tmp/ghost\n"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _R())
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    return m, reg


def test_flag_off_auto_registers_byte_identical(sandbox):
    """INERT: flag off -> reconcile mints the unknown session exactly as before."""
    m, reg = sandbox
    added = m.reconcile_registry()
    assert added == 1
    agents = json.loads(reg.read_text())["agents"]
    assert "raw-ghost" in agents, "flag-off: legacy auto-register must still mint"
    assert agents["raw-ghost"]["auto_registered"]


def test_cutover_observer_no_mint_alarms_skips(sandbox, monkeypatch):
    """Under cutover: NO mint, registry.json untouched, ONE alarm naming the session
    and the missing _REQUIRED_ADOPT fields + a one-paste adopt recipe (RED#1)."""
    m, reg = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    before = reg.read_text()
    alarms = []
    # store_lookup=False -> the ghost is NOT a known/provisional store generation.
    added = m.reconcile_registry(alarm=alarms.append, store_lookup=lambda name: False)

    assert added == 0, "cutover: reconcile must NOT mint"
    assert reg.read_text() == before, \
        "cutover: registry.json (projector-owned) must be UNTOUCHED"
    assert len(alarms) == 1, "cutover: an absent live session must ALARM"
    ev = alarms[0]
    assert ev["kind"] == "unregistered-live-session"
    assert ev["session"] == "raw-ghost"
    assert ev.get("tmux") == "raw-ghost"
    missing = set(ev["missing_required"])
    assert {"root", "generation", "model", "runtime"} <= missing, \
        "alarm must list the missing _REQUIRED_ADOPT fields the operator must supply"
    assert "adopt_identity" in ev["adopt_recipe"], \
        "alarm must carry a one-paste adopt_identity recipe"


def test_alarm_dedup_and_repage_ceiling(sandbox, tmp_path, monkeypatch):
    """RED#2 (H9): the out-of-lock */10 reconciler runs as a fresh process each pass,
    so dedup state must be DURABLE. Two consecutive passes over the SAME unadopted
    session => ONE page; after the re-page ceiling elapses it may page again."""
    m, reg = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    ledger = tmp_path / "state" / "unreg-alarms.json"
    pages = []
    clock = [1000.0]

    def throttle(ev):
        return m._throttled_alarm(ev, page=pages.append, ledger_path=ledger,
                                  now=clock[0], ceiling_s=86400)

    # pass 1 -> the anomaly pages once.
    m.reconcile_registry(alarm=throttle, store_lookup=lambda n: False)
    assert len(pages) == 1
    # pass 2, same session, within the ceiling window -> deduped (no new page).
    m.reconcile_registry(alarm=throttle, store_lookup=lambda n: False)
    assert len(pages) == 1, "H9: re-page ceiling must dedup within the window"
    # advance past the ceiling -> re-page allowed (still unresolved).
    clock[0] += 86401
    m.reconcile_registry(alarm=throttle, store_lookup=lambda n: False)
    assert len(pages) == 2, "after the ceiling elapses, one re-page is allowed"


def test_alarm_ledger_is_per_session(sandbox, tmp_path, monkeypatch):
    """Dedup keys on the SESSION: a second distinct anomaly pages independently."""
    m, _ = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    ledger = tmp_path / "state" / "unreg-alarms.json"
    pages = []
    ev_a = {"session": "ghost-a"}
    ev_b = {"session": "ghost-b"}
    assert m._throttled_alarm(ev_a, page=pages.append, ledger_path=ledger, now=1.0) is True
    assert m._throttled_alarm(ev_a, page=pages.append, ledger_path=ledger, now=2.0) is False
    assert m._throttled_alarm(ev_b, page=pages.append, ledger_path=ledger, now=3.0) is True
    assert [p["session"] for p in pages] == ["ghost-a", "ghost-b"]


def _build_store_with_provisional(root_dir):
    """Migrate a store (canonical a1@gen3) + add a live NON-canonical generation
    (a1@gen4) -> projects as the provisional alias `a1-g4`. Returns nothing; the
    DB lands at root_dir/state/orchestra-registry.db (the prod path)."""
    from scripts.identity_store import migrate, orchestra_db
    src = root_dir / "src"
    (src / "state" / "agents").mkdir(parents=True, exist_ok=True)
    registry = {
        "version": 1, "last_updated": "t0",
        "machines": {"vps": {"hostname": "srv"}},
        "agents": {"a1": {"name": "a1", "tier": "T2", "machine": "vps", "cwd": "/x",
                          "runtime": "claude", "model": "claude-opus-4-8[1m]",
                          "tmux_session": "a1", "always_on": True,
                          "system_prompt": "prompts/a1.md", "status": "online",
                          "generation": 3, "session_id": "s1", "lineage_root": "a1"}},
        "_retired_agents": {}, "_provisional": {}, "_canonical": {"a1": "x"},
    }
    (src / "registry.json").write_text(json.dumps(registry, indent=2))
    (src / "state" / "agent-sessions.json").write_text(json.dumps(
        {"a1": {"session_id": "s1", "model": "claude-opus-4-8[1m]", "generation": 3,
                "status": "online", "tmux_session": "a1"}}))
    (src / "state" / "agents" / "a1.json").write_text(json.dumps(
        {"agent_id": "a1", "status": "online", "task": "t"}))
    db = root_dir / "state" / "orchestra-registry.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    orchestra_db.init_db(str(db))
    conn = orchestra_db.get_connection(str(db))
    try:
        migrate.migrate(conn,
                        registry_path=str(src / "registry.json"),
                        sessions_path=str(src / "state" / "agent-sessions.json"),
                        agents_dir=str(src / "state" / "agents"))
        conn.execute(
            "INSERT INTO generations (root, generation, session_id, model, spawned_at) "
            "VALUES ('a1', 4, NULL, 'claude-opus-4-8[1m]', 't-spawn')")
    finally:
        conn.close()


def test_provisional_window_no_alarm_via_default_lookup(tmp_path, monkeypatch):
    """RED#3 (provisional-window): the default store lookup must key on `generations`
    INCLUDING non-canonical rows. A live provisional successor `a1-g4` mid-rotation
    must NOT alarm; a truly-unknown `raw-ghost` MUST."""
    _build_store_with_provisional(tmp_path)
    m = _load()
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"agents": {"a1": {"name": "a1", "tmux_session": "a1"}}}))
    monkeypatch.setattr(m, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setattr(m, "REGISTRY_FILE", reg)
    monkeypatch.setattr(m, "_cv4_observe_session_index", lambda *a, **k: None)
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")

    class _R:
        returncode = 0
        # a1 = canonical (already registered), a1-g4 = provisional (in store, NOT
        # in registry), raw-ghost = truly unknown.
        stdout = "a1|/x\na1-g4|/x\nraw-ghost|/tmp/ghost\n"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _R())
    alarms = []
    m.reconcile_registry(alarm=alarms.append)  # DEFAULT store_lookup (DB-backed)
    sessions = [a["session"] for a in alarms]
    assert "a1-g4" not in sessions, \
        "provisional alias must NOT alarm (lookup keys on non-canonical generations)"
    assert "raw-ghost" in sessions, "a truly-unknown session must alarm"


def test_sync_window_grace_suppresses_alarm(sandbox, monkeypatch):
    """RED#4 (SYNC ordering): rotate_agent registers the provisional to the store
    (Step-3) BEFORE spawning the pane (Step-4). The observer adds a defensive grace
    so any spawn/store-write skew cannot fire a false anomaly: a JUST-spawned session
    (age < grace) is not alarmed yet; once it ages past grace it alarms if still absent."""
    m, reg = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")

    # raw-ghost spawned 5s ago -> inside the sync window -> must NOT alarm yet.
    fresh = []
    m.reconcile_registry(alarm=fresh.append, store_lookup=lambda n: False,
                         session_age=lambda n: 5.0)
    assert fresh == [], "sync window: a just-spawned session must not alarm"

    # aged past the grace window and still absent -> now it alarms.
    aged = []
    m.reconcile_registry(alarm=aged.append, store_lookup=lambda n: False,
                         session_age=lambda n: 10_000.0)
    assert [a["session"] for a in aged] == ["raw-ghost"]


def test_orphaned_rotation_pane_alarms_with_visibility(sandbox, monkeypatch):
    """RED#5 (orphaned-pane): a crashed/rolled-back rotation whose `<root>-g<N>` pane
    SURVIVED (rollback pruned its provisional store gen) SHOULD alarm — and be flagged
    as an orphaned rotation pane (desired visibility), distinct from a raw-tmux spawn."""
    m, reg = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")

    class _R:
        returncode = 0
        # a1-g4 = surviving provisional alias (its store gen was pruned on rollback);
        # raw-ghost = a plain raw-tmux spawn.
        stdout = "a1-g4|/x\nraw-ghost|/tmp/ghost\n"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _R())
    alarms = []
    # both absent from the store, both aged past the sync grace.
    m.reconcile_registry(alarm=alarms.append, store_lookup=lambda n: False,
                         session_age=lambda n: 10_000.0)
    by = {a["session"]: a for a in alarms}
    assert "a1-g4" in by and "raw-ghost" in by, "orphaned pane must alarm (visibility)"
    assert by["a1-g4"]["orphan_rotation"] is True, \
        "a surviving <root>-g<N> pane absent from the store = orphaned rotation"
    assert by["raw-ghost"]["orphan_rotation"] is False, \
        "a plain raw-tmux spawn is not an orphaned rotation pane"
