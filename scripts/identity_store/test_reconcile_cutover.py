"""Writer-resume p6 (RED) — reconcile_fleet_stores degrades to report-only under cutover.

The fleet-store reconciler exists to repair cross-store DRIFT between registry.json /
agent-sessions.json / state/agents/*.json. Under cutover there is ONE store (the DB),
so cross-store drift is IMPOSSIBLE and its three direct writes are obsolete (spec §3).
It must degrade to report-only under the flag — never write a projector-owned artifact —
while staying byte-identical (fix writes) when the flag is off. No store import needed;
the reconciler simply stops writing.

RED until reconcile forces fix=False under cutover.
"""
import contextlib
import importlib.util
import json
import os

import pytest

_RECONCILE = os.path.join(os.path.dirname(__file__), "..", "reconcile_fleet_stores.py")


def _load():
    spec = importlib.util.spec_from_file_location("reconcile_p6", _RECONCILE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    m = _load()
    from pathlib import Path
    reg = tmp_path / "registry.json"
    sess = tmp_path / "state" / "agent-sessions.json"
    agents = tmp_path / "state" / "agents"
    agents.mkdir(parents=True)
    # generation DRIFT: registry=1, sessions=2 -> reconciler would bump reg/state to 2
    reg.write_text(json.dumps({"agents": {"a1": {"generation": 1, "tmux_session": "a1",
                                                 "session_id": "old"}}}))
    sess.write_text(json.dumps({"a1": {"generation": 2, "session_id": "old"}}))
    (agents / "a1.json").write_text(json.dumps({"generation": 1}))
    monkeypatch.setattr(m, "REGISTRY_PATH", reg)
    monkeypatch.setattr(m, "SESSIONS_PATH", sess)
    monkeypatch.setattr(m, "AGENTS_DIR", agents)
    # HERMETICITY: point the cutover-dir global at the sandbox so _cutover_active()
    # reads THIS tmp dir (flag-less), NOT the live armed flag at the real ORCH_DIR.
    monkeypatch.setattr(m, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(m, "registry_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(m, "get_live_tmux_sessions", lambda: set())
    monkeypatch.delenv("IDENTITY_STORE_CUTOVER", raising=False)
    return m, reg


def test_reconcile_fixes_drift_when_flag_off(sandbox):
    """INERT: flag off -> the reconciler fixes drift exactly as before."""
    m, reg = sandbox
    res = m.reconcile(fix=True)
    assert res["fixed"] is True
    assert json.loads(reg.read_text())["agents"]["a1"]["generation"] == 2, \
        "flag-off: registry drift must be fixed (byte-identical legacy behavior)"


def test_reconcile_report_only_under_cutover(sandbox, monkeypatch):
    """Under cutover the DB is the single truth — the reconciler must NOT write the
    projector-owned artifacts; it degrades to report-only (fixed=False)."""
    m, reg = sandbox
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    res = m.reconcile(fix=True)
    assert res["fixed"] is False, "cutover: reconciler must degrade to report-only"
    assert json.loads(reg.read_text())["agents"]["a1"]["generation"] == 1, \
        "cutover: registry.json (projector-owned) must be UNTOUCHED"


def test_cutover_active_seam(sandbox, monkeypatch):
    m, _ = sandbox
    assert m._cutover_active() is False
    monkeypatch.setenv("IDENTITY_STORE_CUTOVER", "1")
    assert m._cutover_active() is True
