"""P0 (OQ-3 blocker) — the provisional green alias MUST inherit its runtime +
lineage_root from the BLUE lineage, so a non-claude rotation spawns the correct
CLI.

By effect during the live Gemini demo fire (rab-g13, gm msg_d287decc): a forced
blue-green swap on the gemini seat demo-gemini-pred spawned a green whose
projected alias carried runtime='claude' -> spawn-agent.sh launched Claude Code
with model gemini-3.7-flash -> "model may not exist" on every input -> the green
never booted. Root cause class: the provisional-green registration did not carry
the blue lineage's runtime onto the projected `<root>-g<N>` alias, so spawn
defaulted to claude. That silently mints wrong-runtime greens on EVERY non-claude
rotation = a provider-agnostic killer.

RED-first: register a provisional green under a GEMINI blue lineage and assert the
projected alias carries runtime='gemini' + the blue's lineage_root; same for a
CLAUDE blue lineage (regression guard so the fix never breaks claude rotations).
"""
import json
import os

import pytest

from scripts.identity_store import cutover, identity_writer, orchestra_db


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def _seed_blue(orchdir, root, runtime, model):
    """Seed a live canonical BLUE lineage with the given runtime + a gen1 doc."""
    dbp = os.path.join(orchdir, "state", "orchestra-registry.db")
    orchestra_db.init_db(dbp)
    c = orchestra_db.get_connection(dbp)
    c.execute("INSERT INTO lineages (root, tier, runtime, machine, cwd, always_on) "
              "VALUES (?, 'T2', ?, 'vps', '/x', 1)", (root, runtime))
    gid = c.execute("INSERT INTO generations (root, generation, session_id, model) "
                    "VALUES (?, 1, 's1', ?)", (root, model)).lastrowid
    c.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
              "VALUES (?, ?, ?, 'online')", (root, gid, root))
    c.execute("INSERT INTO source_records (file, kind, key, ordinal, payload_json) "
              "VALUES ('registry.json','agent',?,0,?)",
              (root, json.dumps({"name": root, "system_prompt": f"prompts/{root}.md",
                                 "generation": 1, "status": "online"})))
    c.close()
    return gid


def _alias(orchdir, alias):
    with open(os.path.join(orchdir, "registry.json"), "r", encoding="utf-8") as f:
        return json.load(f)["agents"][alias]


def test_provisional_green_inherits_gemini_runtime(orchdir):
    """A provisional green under a GEMINI blue lineage projects an alias whose
    runtime is 'gemini' (so spawn-agent.sh launches the gemini CLI) and whose
    lineage_root is the BLUE root (not the alias)."""
    _seed_blue(orchdir, "gseat", runtime="gemini", model="gemini-3.7-flash")
    cutover.arm(orchdir)
    assert identity_writer.register_provisional(
        orchdir, "gseat", 2, model="gemini-3.7-flash") is True
    assert identity_writer.project_now(orchdir) is True
    a = _alias(orchdir, "gseat-g2")
    assert a["runtime"] == "gemini", f"green must spawn gemini, got {a.get('runtime')!r}"
    assert a["lineage_root"] == "gseat", f"lineage_root must be the blue root, got {a.get('lineage_root')!r}"


def test_provisional_green_inherits_claude_runtime_regression(orchdir):
    """Regression guard: a provisional green under a CLAUDE blue lineage still
    projects runtime='claude' + the blue lineage_root — the fix must not break
    claude rotations."""
    _seed_blue(orchdir, "cseat", runtime="claude", model="claude-opus-4-8[1m]")
    cutover.arm(orchdir)
    assert identity_writer.register_provisional(
        orchdir, "cseat", 2, model="claude-opus-4-8[1m]") is True
    assert identity_writer.project_now(orchdir) is True
    a = _alias(orchdir, "cseat-g2")
    assert a["runtime"] == "claude", f"got {a.get('runtime')!r}"
    assert a["lineage_root"] == "cseat", f"got {a.get('lineage_root')!r}"
