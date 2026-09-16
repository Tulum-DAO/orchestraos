"""Spawn-path DB-first (Piece A, RED) — resolver.registry_agent_db + the
spawn_registry_resolve CLI resolve a flat-flapped canonical/provisional agent from the
identity DB so spawn-agent.sh can spawn it. Congruence DEC-1788603298 v2 conditions:

  C1  a provisional alias's null cwd resolves to a REAL existing dir (spawn-agent.sh:369
      rejects a nonexistent cwd, and the auto-register cwd default is skipped on this path)
  C2  retired <root>-gen<N> archives are EXCLUDED (never spawnable) — both the full-doc
      (stale tmux_session) and _synth_archive (no tmux_session) shapes
  C3  a JSON-null field emits '' (not the literal "None"); a missing field emits ''
  +   corrupt payload row / absent DB -> None (fail-safe to flat-miss), never raises

RED until scripts/identity_store/resolver.py grows registry_agent_db + the CLI exists.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from scripts.identity_store import orchestra_db  # noqa: E402

CLI = os.path.join(HERE, "spawn_registry_resolve.py")


def _load_resolver():
    path = os.path.join(HERE, "resolver.py")
    spec = importlib.util.spec_from_file_location("identity_resolver_spawn", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(orch, *, lineage_cwd=None, root_doc_cwd=None, alias=True, retired=True,
          corrupt=False):
    """Seed a DB with a canonical root + (optional) a live non-canonical gen (provisional
    alias <root>-g2) + (optional) a retired gen (archive <root>-gen0)."""
    (orch / "state").mkdir(parents=True, exist_ok=True)
    dbp = orch / "state" / "orchestra-registry.db"
    orchestra_db.init_db(str(dbp))
    c = orchestra_db.get_connection(str(dbp))
    try:
        c.execute("INSERT INTO lineages(root,tier,runtime,always_on,machine,cwd) "
                  "VALUES('root1','T2','claude',1,'vps',?)", (lineage_cwd,))
        c.execute("INSERT INTO generations(root,generation,session_id,model) "
                  "VALUES('root1',1,'sid1','claude-opus-4-8[1m]')")
        g1 = c.execute("SELECT id FROM generations WHERE root='root1' AND generation=1"
                       ).fetchone()["id"]
        c.execute("INSERT INTO canonical(root,generation_id,tmux_session,status) "
                  "VALUES('root1',?,'root1','online')", (g1,))
        # canonical root's live agent document (source_records)
        root_doc = {"name": "root1", "lineage_root": "root1", "tmux_session": "root1",
                    "tier": "T2", "runtime": "claude", "model": "claude-opus-4-8[1m]",
                    "cwd": root_doc_cwd, "system_prompt": None}
        payload = "" if corrupt else json.dumps(root_doc)  # '' = gm's exact transient case
        c.execute("INSERT INTO source_records(file,kind,key,ordinal,payload_json) "
                  "VALUES('registry.json','agent','root1',0,?)", (payload,))
        if alias:  # live, non-canonical gen -> provisional alias root1-g2
            c.execute("INSERT INTO generations(root,generation,session_id,model) "
                      "VALUES('root1',2,'sid2','claude-opus-4-8[1m]')")
        if retired:  # retired gen -> archive root1-gen0 (status=retired)
            c.execute("INSERT INTO generations(root,generation,session_id,model,retired_at) "
                      "VALUES('root1',0,'sid0','claude-opus-4-8[1m]','2026-09-01T00:00:00Z')")
    finally:
        c.close()
    return orch


@pytest.fixture
def orch(tmp_path):
    return tmp_path


# --------------------------------------------------------------------------
def test_provisional_alias_resolves(orch):
    _seed(orch, lineage_cwd=str(orch))  # lineage cwd = a real dir
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1-g2")
    assert rec is not None, "provisional alias must resolve from the DB"
    assert rec["tmux_session"] == "root1-g2"
    assert rec["status"] == "provisioning"


def test_c1_null_cwd_resolves_to_real_dir(orch):
    # lineage cwd None AND root doc cwd None -> resolver must still yield an EXISTING dir
    _seed(orch, lineage_cwd=None, root_doc_cwd=None)
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1-g2")
    assert rec["cwd"], "cwd must not be empty (spawn-agent.sh:369 would exit 1)"
    assert os.path.isdir(rec["cwd"]), f"cwd must be a real existing dir, got {rec['cwd']!r}"


def test_c1_prefers_lineage_cwd_when_real(orch):
    real = orch / "workdir"; real.mkdir()
    _seed(orch, lineage_cwd=str(real))
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1-g2")
    assert rec["cwd"] == str(real)


def test_c1_prefers_root_cwd_over_orch(orch):
    """gm steer-1: a Green must run in the SAME cwd as its Blue (the root), so a null-cwd
    alias falls back to the ROOT's cwd (lineage → root agent-doc) BEFORE the orch default.
    Here the lineage cwd is null but the root's agent-doc carries a real cwd → use THAT,
    not orch."""
    root_cwd = orch / "seat-home"; root_cwd.mkdir()
    _seed(orch, lineage_cwd=None, root_doc_cwd=str(root_cwd))
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1-g2")
    assert rec["cwd"] == str(root_cwd), \
        f"null-cwd alias must inherit the root's cwd, not the orch default (got {rec['cwd']!r})"


def test_c2_retired_archive_excluded(orch):
    _seed(orch, lineage_cwd=str(orch))
    r = _load_resolver()
    assert r.registry_agent_db(str(orch), "root1-gen0") is None, \
        "a retired <root>-gen<N> archive must NEVER be spawnable"


def test_degraded_canonical_own_row_bad_returns_none(orch):
    """If the CANONICAL member's OWN agent doc is the transient bad row, the build's
    {name,lineage_root} fallback lacks tmux_session -> not spawnable -> registry_agent_db
    returns None (falls through to flat/auto-register), never a record that would spawn an
    empty tmux name. The unrelated provisional alias still resolves."""
    _seed(orch, lineage_cwd=str(orch), corrupt=True)  # root1's own agent doc payload=''
    r = _load_resolver()
    assert r.registry_agent_db(str(orch), "root1") is None, \
        "a degraded canonical record (no tmux_session) must not be spawnable"
    # the alias (synthesized, not from the bad row) is unaffected
    assert r.registry_agent_db(str(orch), "root1-g2") is not None


def test_canonical_resolves(orch):
    _seed(orch, lineage_cwd=str(orch), root_doc_cwd=str(orch))
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1")
    assert rec is not None and rec["tmux_session"] == "root1"


def test_absent_db_returns_none(orch):
    r = _load_resolver()
    assert r.registry_agent_db(str(orch), "root1-g2") is None


def test_transient_corrupt_row_skipped_alias_still_resolves(orch):
    """gm msg_1464f5cf (BUG3 real root cause): a source_records agent/registry row with an
    unparseable payload_json (empty '' — a concurrent writer transiently mid-update under a
    mode=ro read) must be SKIPPED, not crash the whole registry build. registry_agent_db
    must still return the GOOD rows incl the provisional alias — never raise, never
    fail-safe-to-flat and lose it."""
    _seed(orch, lineage_cwd=str(orch), corrupt=True)  # root1 agent doc payload_json=''
    r = _load_resolver()
    alarms = []
    rec = r.registry_agent_db(str(orch), "root1-g2", alarm=lambda m: alarms.append(m))
    assert rec is not None and rec["tmux_session"] == "root1-g2", \
        "a transient bad payload row must be skipped, not lose the good alias"
    # gm msg_786044c1: the transient skip is EXPECTED churn on a live store -> it must be
    # STDERR-ONLY, never a gm msg_store alarm (which flooded ~1/min). The passed alarm
    # (gm-alerting) must NOT fire for a skipped transient row.
    assert alarms == [], f"transient-row skip must NOT fire the gm alarm (got {alarms})"


# ---- CLI (the shell entrypoint) ----
def _cli(orch, *args):
    env = dict(os.environ, ORCHESTRA_DIR=str(orch))
    return subprocess.run([sys.executable, CLI, *args], env=env,
                          capture_output=True, text=True)


def test_cli_dump_provisional(orch):
    _seed(orch, lineage_cwd=str(orch))
    p = _cli(orch, "--dump", "root1-g2")
    assert p.returncode == 0, p.stderr
    fields = dict(line.split("\t", 1) for line in p.stdout.splitlines() if "\t" in line)
    assert fields["tmux_session"] == "root1-g2"
    assert os.path.isdir(fields["cwd"])


def test_cli_c3_null_field_is_empty_not_none(orch):
    _seed(orch, lineage_cwd=str(orch), root_doc_cwd=str(orch))
    p = _cli(orch, "--dump", "root1-g2")
    fields = dict(line.split("\t", 1) for line in p.stdout.splitlines() if "\t" in line)
    # system_prompt is JSON null on the alias -> must be '' not the literal 'None'
    assert fields.get("system_prompt", "") == "", \
        f"null field must emit '' not {fields.get('system_prompt')!r}"


def test_cli_retired_exit1(orch):
    _seed(orch, lineage_cwd=str(orch))
    p = _cli(orch, "--dump", "root1-gen0")
    assert p.returncode == 1, "retired archive must exit 1 (not spawnable)"


def test_cli_unknown_exit1(orch):
    _seed(orch, lineage_cwd=str(orch))
    p = _cli(orch, "--dump", "does-not-exist")
    assert p.returncode == 1


def test_gm_acceptance_provisional_absent_from_flat(orch):
    """gm gate RED test (msg_368f2a6b): a provisional g2 in the DB but ABSENT from the
    flat registry.json must resolve — registry_agent_db(g2) returns the row (the alias
    the faithful projector / project_now also emits), NOT None."""
    _seed(orch, lineage_cwd=str(orch))
    # (no flat registry.json written -> g2 is absent from flat, present in DB)
    r = _load_resolver()
    rec = r.registry_agent_db(str(orch), "root1-g2")
    assert rec is not None and rec["tmux_session"] == "root1-g2" \
        and rec["status"] == "provisioning", "provisional alias must resolve (not None)"


def test_cli_positional_field_form(orch):
    """gm's re-verify command shape: `spawn_registry_resolve <agent_id> <field>` (positional,
    no mode flag — mirrors get_agent_field's own signature) prints that field."""
    _seed(orch, lineage_cwd=str(orch))
    p = _cli(orch, "root1-g2", "tmux_session")           # <-- gm's exact invocation shape
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "root1-g2"


def test_cli_positional_dump_form(orch):
    """Bare `<agent_id>` (one positional) dumps the record."""
    _seed(orch, lineage_cwd=str(orch))
    p = _cli(orch, "root1-g2")
    assert p.returncode == 0
    assert "tmux_session\troot1-g2" in p.stdout
