"""F3 — read-your-writes seam: register-then-project BEFORE any legacy reader.

G7 re-run finding (gm ruling msg_2e109091): rotate_agent wrote the provisional
generation to the DB then synchronously invoked spawn-agent.sh — a LEGACY
registry.json READER — inside the projector daemon's debounce window. Stale
read -> spawn auto-register fallback -> U16 refusal (correct!) -> rotation
aborted. The register->spawn handoff has an INHERENT read-your-writes
requirement, unlike lag-tolerant dashboard/monitor readers.

Fix (a): `identity_writer.project_now` — a synchronous force-project helper —
called by rotate_agent (and r-a-b's async arm at prewarm, the same race on the
async path) immediately after an identity write that a spawn will read.
FAIL-CLOSED: a project_now failure must ABORT the rotation before spawn
(never spawn into an unprojected state).
"""
import json
import os

import pytest

from scripts.identity_store import cutover, identity_writer, orchestra_db

ROOT = "a1"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed(orchdir):
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
              "VALUES ('a1','T2','claude','vps','/x',1)")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES ('a1',1,'s1','m')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES ('a1',?,'a1','online')", (gid,))
    c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
              "VALUES ('registry.json','agent','a1',0,?)",
              (json.dumps({"name": "a1", "system_prompt": "prompts/a1.md",
                           "generation": 1, "status": "online"}),))
    c.close()
    return gid


def _registry(orchdir):
    with open(os.path.join(orchdir, "registry.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# --- the RED that reproduces F3 --------------------------------------------

def test_register_then_project_now_makes_alias_immediately_readable(orchdir):
    """The F3 race, closed: register_provisional + project_now => the
    <root>-g<N> alias is readable in registry.json IMMEDIATELY (no daemon,
    no debounce wait) — exactly what spawn-agent.sh reads next."""
    _seed(orchdir)
    cutover.arm(orchdir)
    assert identity_writer.register_provisional(orchdir, ROOT, 2, model="m") is True
    # the F3 gap: WITHOUT project_now the alias is NOT on disk yet
    assert identity_writer.project_now(orchdir) is True
    reg = _registry(orchdir)
    assert "a1-g2" in reg["agents"], "provisional alias must be spawn-readable"
    assert reg["agents"]["a1-g2"]["status"] == "provisioning"


def test_project_now_inactive_is_noop_false(orchdir):
    """Flag-off => INERT no-op (False), byte-identical legacy behavior."""
    _seed(orchdir)
    assert identity_writer.project_now(orchdir) is False
    assert not os.path.exists(os.path.join(orchdir, "registry.json"))


def test_project_now_failure_raises_for_fail_closed_abort(orchdir, monkeypatch):
    """FAIL-CLOSED (gm req i): a projection failure RAISES so the rotation
    caller aborts BEFORE spawn — never spawn into an unprojected state."""
    _seed(orchdir)
    cutover.arm(orchdir)
    identity_writer.register_provisional(orchdir, ROOT, 2, model="m")
    from scripts.identity_store import projector

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(projector, "project_faithful", boom)
    with pytest.raises(Exception):
        identity_writer.project_now(orchdir)
