"""Readers-DB-first (Piece 2, RED) — message-router.load_agent_meta() resolves the
agent meta map DB-FIRST under cutover, so resolve_delivery_target (unchanged, pure)
routes over flap-immune identity. Covers the acceptance by effect:

  R1  a live agent correct in the DB but missing from a stale flat file -> direct-live
  R4  survives a foreign checkout that wipes BOTH flat files (DB is truth)
  R6  INERT (fd/import): flag OFF imports nothing new + opens no DB + byte-identical
  R7  retired-predecessor forwarding preserved (succeeded_by carried by session docs)
  R8  partial DB: a live session in the flat file but missing from the DB still resolves
  R11 ambiguous-lineage still fires under DB-derived meta
  R12 the legacy superseded_by synonym is preserved (would vanish under the typed JOIN)

RED until message-router.load_agent_meta() grows the cutover DB-first branch.
"""
import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from scripts.identity_store import orchestra_db, migrate  # noqa: E402


def _seed_db(orch, sessions_map):
    (orch / "state" / "agents").mkdir(parents=True, exist_ok=True)
    agents = {
        k: {"name": k, "tier": "T2", "machine": "vps", "cwd": "/x",
            "runtime": "claude", "model": "m",
            "tmux_session": v.get("tmux_session", k), "always_on": True,
            "system_prompt": "p", "status": v.get("status", "online"),
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


def _fresh_router(orch, *, cutover):
    """Import a FRESH message-router bound to `orch`; env decides the cutover gate."""
    os.environ["ORCHESTRA_DIR"] = str(orch)
    os.environ.pop("IDENTITY_STORE_CUTOVER", None)
    if cutover:
        os.environ["IDENTITY_STORE_CUTOVER"] = "1"
    for m in [m for m in sys.modules if m in ("mr_under_test",)]:
        del sys.modules[m]
    spec = importlib.util.spec_from_file_location(
        "mr_under_test", os.path.join(REPO, "scripts", "message-router.py"))
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    return mr


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.get(k) for k in ("ORCHESTRA_DIR", "IDENTITY_STORE_CUTOVER")}
    sys.modules.pop("scripts.identity_store.resolver", None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --------------------------------------------------------------------------
def test_r1_new_agent_missing_from_flat_resolves_direct_live(tmp_path):
    _seed_db(tmp_path, {"scratch": {"tmux_session": "scratch", "status": "online"}})
    # simulate a foreign-branch checkout that wiped scratch from the flat file
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({}))
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    assert "scratch" in meta, "DB-first meta must carry the live agent the flat file lost"
    s, _fwd, reason = mr.resolve_delivery_target("scratch", {"scratch"}, meta)
    assert (s, reason) == ("scratch", "direct-live")


def test_r4_survives_foreign_checkout_of_both_flat_files(tmp_path):
    _seed_db(tmp_path, {"foo": {"tmux_session": "foo", "status": "online"}})
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({}))
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {}}))
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    assert "foo" in meta


def test_r6_inert_flag_off_imports_nothing_and_reads_flat(tmp_path):
    _seed_db(tmp_path, {"foo": {"tmux_session": "foo-DB", "status": "online"}})
    flat = {"foo": {"tmux_session": "foo-FLAT", "status": "online"}}
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(flat))
    sys.modules.pop("scripts.identity_store.resolver", None)
    mr = _fresh_router(tmp_path, cutover=False)
    meta = mr.load_agent_meta()
    assert meta == flat, "flag OFF must return the flat file byte-for-byte"
    assert "scripts.identity_store.resolver" not in sys.modules, \
        "flag OFF must NOT import the DB resolver (INERT: imports nothing new)"


def test_r7_retired_predecessor_forwards_to_live_head(tmp_path):
    _seed_db(tmp_path, {
        "foo": {"tmux_session": "foo", "status": "online", "lineage_root": "foo"},
        "foo-gen41": {"tmux_session": "foo-gen41", "status": "retired",
                      "lineage_root": "foo", "succeeded_by": "foo"},
    })
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    s, fwd, reason = mr.resolve_delivery_target("foo-gen41", {"foo"}, meta)
    assert s == "foo", f"retired predecessor must forward to the live head (got {reason})"


def test_r8_partial_db_flat_only_session_still_resolves(tmp_path):
    # DB knows only `foo`; `legacy` is live and in the flat file but absent from the DB
    _seed_db(tmp_path, {"foo": {"tmux_session": "foo", "status": "online"}})
    flat = json.loads((tmp_path / "state" / "agent-sessions.json").read_text())
    flat["legacy"] = {"tmux_session": "legacy", "status": "online"}
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps(flat))
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    assert "legacy" in meta, "union must not drop a flat/live key the DB lacks"
    s, _f, reason = mr.resolve_delivery_target("legacy", {"foo", "legacy"}, meta)
    assert (s, reason) == ("legacy", "direct-live")


def test_r11_ambiguous_lineage_still_fires(tmp_path):
    _seed_db(tmp_path, {
        "twinroot": {"tmux_session": "twinroot", "status": "retired",
                     "lineage_root": "twinroot"},
        "twin-a": {"tmux_session": "twin-a", "status": "online",
                   "lineage_root": "twinroot"},
        "twin-b": {"tmux_session": "twin-b", "status": "online",
                   "lineage_root": "twinroot"},
    })
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    s, _f, reason = mr.resolve_delivery_target(
        "twinroot", {"twin-a", "twin-b"}, meta)
    assert reason == "ambiguous-lineage"


def test_r12_superseded_by_synonym_preserved(tmp_path):
    _seed_db(tmp_path, {
        "bar": {"tmux_session": "bar", "status": "online", "lineage_root": "bar"},
        "bar-old": {"tmux_session": "bar-old", "status": "retired",
                    "lineage_root": "bar", "superseded_by": "bar"},
    })
    mr = _fresh_router(tmp_path, cutover=True)
    meta = mr.load_agent_meta()
    assert meta["bar-old"].get("superseded_by") == "bar", \
        "legacy superseded_by synonym must survive the DB source (absent under the JOIN)"
    s, _f, reason = mr.resolve_delivery_target("bar-old", {"bar"}, meta)
    assert s == "bar"
