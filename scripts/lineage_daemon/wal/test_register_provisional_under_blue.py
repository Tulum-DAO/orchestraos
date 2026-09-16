"""GOAL / OQ-3 (rotation lane, gm msg_fc5e661d): the provisional GREEN must register as a
provisional generation UNDER the blue root — so project_faithful's _alias_payload emits the
<root>-g<N> alias INHERITING the blue lineage's runtime + lineage_root — NOT as a standalone
root that resolve_runtime defaults to claude (which spawned Claude Code for a gemini seat).

RED-first: seed a blue lineage with runtime='gemini', register a provisional green via
real_seams.make_register_provisional_fn, and assert (a) the generations row lands under the
BLUE root (not a standalone <alias> lineage) and (b) the projected alias inherits runtime=
gemini + lineage_root=<blue root>.
"""
import os
import sys

import pytest

sys.path.insert(0, "scripts")
sys.path.insert(0, os.path.abspath("."))
from identity_store import orchestra_db, cutover, identity_writer  # noqa: E402
from lineage_daemon.wal import real_seams  # noqa: E402

BLUE = "demo-blue-gem"


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "wal").mkdir()
    dbp = os.path.join(str(tmp_path), "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
              f"VALUES ('{BLUE}','T2','gemini','vps','/x',1)")
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    f"VALUES ('{BLUE}',1,'sid-blue','gemini-3.7-flash')").lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              f"VALUES ('{BLUE}',?,'{BLUE}','online')", (gid,))
    c.close()
    cutover.arm(str(tmp_path))
    return str(tmp_path)


def test_green_registers_under_blue_root_not_standalone(orchdir):
    reg = real_seams.make_register_provisional_fn(orchdir)
    reg(BLUE, f"{BLUE}-g2", generation=2, model="gemini-3.7-flash")
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    c = orchestra_db.get_connection(dbp)
    # (a) a provisional gen-2 lands UNDER the blue root
    under_blue = c.execute(
        "SELECT 1 FROM generations WHERE root=? AND generation=2", (BLUE,)).fetchone()
    # (b) NO standalone lineage/generation named after the alias
    standalone_lin = c.execute(
        "SELECT 1 FROM lineages WHERE root=?", (f"{BLUE}-g2",)).fetchone()
    standalone_gen = c.execute(
        "SELECT 1 FROM generations WHERE root=?", (f"{BLUE}-g2",)).fetchone()
    c.close()
    assert under_blue is not None, "provisional green gen-2 must exist UNDER the blue root"
    assert standalone_lin is None, "green must NOT create a standalone lineage <alias>"
    assert standalone_gen is None, "green must NOT create a standalone generation <alias>"


def test_projected_alias_inherits_blue_runtime(orchdir):
    """REGRESSION GUARD (premise refuted by effect): the sanctioned path ALREADY inherits
    runtime. The full alias lives in registry.json['agents'][alias] (_alias_payload's
    12-field DP-B2 shape) — NOT the sparse registry.json['_provisional'][alias]
    ({generation,lineage_root} only), which an earlier version of this test wrongly read.
    A green under a gemini blue lineage MUST project with runtime=gemini so spawn-agent.sh
    spawns antigravity, not claude."""
    reg = real_seams.make_register_provisional_fn(orchdir)
    reg(BLUE, f"{BLUE}-g2", generation=2, model="gemini-3.7-flash")
    identity_writer.project_now(orchdir)
    import json
    reg_json = json.load(open(os.path.join(orchdir, "registry.json")))
    alias = (reg_json.get("agents") or {}).get(f"{BLUE}-g2")
    assert alias is not None, "registry.json['agents'] must carry the <root>-g2 alias"
    assert alias.get("runtime") == "gemini", \
        f"alias must INHERIT blue runtime=gemini, got {alias.get('runtime')!r}"
    assert alias.get("lineage_root") == BLUE, \
        f"alias lineage_root must be the BLUE root, got {alias.get('lineage_root')!r}"
