"""Readers-DB-first (Piece 1, RED) — the resolver core: build a DB-derived agent
meta map from the source_records session documents (the project_faithful path, NOT
the typed canonical<->generations JOIN — congruence DEC-1788554471 C1), and union it
DB-wins-per-key over the flat agent-sessions.json (C2) so a brand-new agent that is
correct in the DB but missing from the git-tracked flat file (foreign-branch checkout
flap) still resolves, while nothing the flat/live set knows is ever dropped.

RED until scripts/identity_store/resolver.py exists.
"""
import importlib.util
import json
import os
import sqlite3

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

from scripts.identity_store import orchestra_db, migrate  # noqa: E402


def _load_resolver():
    """Import the module under test (created by the GREEN step)."""
    path = os.path.join(HERE, "resolver.py")
    spec = importlib.util.spec_from_file_location("identity_resolver", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(orch, sessions_map, registry_agents=None):
    """Write flat files + migrate them into a real orchestra-registry.db, so
    source_records carries a `session` doc per sessions_map key."""
    (orch / "state" / "agents").mkdir(parents=True, exist_ok=True)
    agents = registry_agents or {
        k: {"name": k, "tier": "T2", "machine": "vps", "cwd": "/x",
            "runtime": "claude", "model": "m", "tmux_session": v.get("tmux_session", k),
            "always_on": True, "system_prompt": "p",
            "status": v.get("status", "online"),
            "generation": v.get("generation", 1),
            "session_id": v.get("session_id", k + "-sid"),
            "lineage_root": v.get("lineage_root", k)}
        for k, v in sessions_map.items()}
    reg = {"version": 1, "last_updated": "t0",
           "machines": {"vps": {"hostname": "s"}}, "agents": agents,
           "_retired_agents": {}, "_provisional": {}, "_canonical": {}}
    (orch / "registry.json").write_text(json.dumps(reg, indent=2))
    (orch / "state" / "agent-sessions.json").write_text(json.dumps(sessions_map))
    dbp = orch / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        migrate.migrate(c, registry_path=str(orch / "registry.json"),
                        sessions_path=str(orch / "state" / "agent-sessions.json"),
                        agents_dir=str(orch / "state" / "agents"))
    finally:
        c.close()
    return dbp


@pytest.fixture
def orch(tmp_path):
    return tmp_path


# --------------------------------------------------------------------------
def test_build_meta_db_from_session_docs(orch):
    """The DB-derived meta comes from source_records session docs and preserves
    succeeded_by / lineage_root / status verbatim (not derivable from the JOIN)."""
    _seed(orch, {
        "foo": {"tmux_session": "foo", "status": "online", "lineage_root": "foo"},
        "foo-gen41": {"tmux_session": "foo-gen41", "status": "retired",
                      "lineage_root": "foo", "succeeded_by": "foo"},
    })
    r = _load_resolver()
    meta = r.build_agent_meta_db(str(orch))
    assert set(meta) == {"foo", "foo-gen41"}
    assert meta["foo-gen41"]["succeeded_by"] == "foo"      # succession preserved
    assert meta["foo-gen41"]["lineage_root"] == "foo"
    assert meta["foo"]["tmux_session"] == "foo"


def test_union_db_wins_but_never_drops_flat_keys(orch):
    """Union DB-wins-per-key: DB overrides shared keys, flat supplies keys the DB
    lacks (never a whole-map replacement that could shrink below the live set)."""
    _seed(orch, {"foo": {"tmux_session": "foo-DB", "status": "online"}})
    flat = {"foo": {"tmux_session": "foo-STALE", "status": "online"},
            "only-in-flat": {"tmux_session": "only-in-flat", "status": "online"}}
    r = _load_resolver()
    merged = r.load_meta_db_first(str(orch), flat, alarm=lambda m: None)
    assert merged["foo"]["tmux_session"] == "foo-DB"        # DB wins
    assert "only-in-flat" in merged                          # flat key preserved


def test_new_agent_missing_from_flat_is_added_from_db(orch):
    """The actual bug: a live agent correct in the DB but absent from a
    stale/foreign-checked-out flat file must still appear (flap-immune)."""
    _seed(orch, {"scratch": {"tmux_session": "scratch", "status": "online"}})
    flat = {}  # foreign checkout wiped it
    r = _load_resolver()
    merged = r.load_meta_db_first(str(orch), flat, alarm=lambda m: None)
    assert "scratch" in merged and merged["scratch"]["tmux_session"] == "scratch"


def test_empty_or_absent_db_falls_back_to_flat(orch):
    """Wholly-empty/absent DB => flat is returned unchanged (fail-safe), alarmed."""
    # no _seed => no DB file
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    alarms = []
    r = _load_resolver()
    merged = r.load_meta_db_first(str(orch), flat, alarm=lambda m: alarms.append(m))
    assert merged == flat
    assert alarms, "absent DB under cutover must fire the fail-loud alarm"


def test_per_key_skew_db_wins_silently(orch):
    """DB-vs-flat per-key disagreement: DB wins, and it is SILENT. Per-key skew is the
    DESIGNED normal path (the flat file flaps on the shared tree; the DB is truth), so
    it must NOT alert gm — alarming per-key floods the inbox on every resolve (gm gate
    spot-fix 2026-09-04, steer #2). Only DB-integrity problems (absent/empty/torn — see
    test_empty_or_absent_db_falls_back_to_flat) alarm."""
    _seed(orch, {"foo": {"tmux_session": "foo-DB", "status": "online"}})
    flat = {"foo": {"tmux_session": "foo-FLAT", "status": "online"}}
    alarms = []
    r = _load_resolver()
    merged = r.load_meta_db_first(str(orch), flat, alarm=lambda m: alarms.append(m))
    assert merged["foo"]["tmux_session"] == "foo-DB"   # DB still wins
    assert alarms == [], "per-key DB-wins skew must be SILENT (normal path, not an incident)"


def test_readonly_never_writes(orch):
    """The resolver opens the DB read-only (mode=ro) and never mutates it."""
    dbp = _seed(orch, {"foo": {"tmux_session": "foo", "status": "online"}})
    before = os.path.getmtime(dbp)
    r = _load_resolver()
    r.build_agent_meta_db(str(orch))
    # a read-only mode=ro connection cannot write; assert the seam is ro by trying
    conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM canonical")
    conn.close()
    assert os.path.getmtime(dbp) == before  # resolver did not touch it
