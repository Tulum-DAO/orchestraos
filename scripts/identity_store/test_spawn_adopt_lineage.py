"""A seat's lineage fields survive a new generation, and a new generation of a retired lineage never brings an old
one back as a 'parked' seat (spawn_adopt.py; "test" belongs to the whole lineage). Real sqlite engine via
orchestra_db.init_db and the spawn_adopt CLI, exactly as test_spawn_adopt.py drives it; temp data dirs only."""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import identity_writer, orchestra_db  # noqa: E402
from scripts.identity_store.test_spawn_adopt import _canon, _db, _retire_canonical, _run, orch  # noqa: E402,F401


def _doc(orch, root):
    c = _db(orch)
    try:
        r = c.execute("SELECT payload_json FROM source_records WHERE file='registry.json' AND kind='agent' AND key=?",
                      (root,)).fetchone()
    finally:
        c.close()
    return json.loads(r[0]) if r else None


def _make_test_seat(orch, root):
    """A first spawn, as `orchestra spawn --test` leaves it: the seat document says test, integrator, worker."""
    r = _run(orch, [root, "--runtime", "claude", "--model", "m", "--tier", "T2", "--reports-to", "integrator",
                    "--role", "worker"])
    assert r.returncode == 0, r.stderr
    doc = dict(_doc(orch, root), test=True)
    assert identity_writer.update_registry_agent(str(orch), root, doc, full_record=doc)
    c = _db(orch)
    try:                                       # a resumable generation: what the archive rule calls 'parked'
        c.execute("UPDATE generations SET resume_command='claude --resume s1' WHERE root=?", (root,))
        c.commit()
    finally:
        c.close()


def _gen1(orch, root):
    c = _db(orch)
    try:
        return c.execute("SELECT * FROM generations WHERE root=? AND generation=1", (root,)).fetchone()
    finally:
        c.close()


def test_reestablish_keeps_the_lineage_fields_and_retires_the_old_generation(orch):
    _make_test_seat(orch, "qa-demo")
    c = _db(orch)
    try:
        c.execute("UPDATE generations SET retired_at=? WHERE root='qa-demo'", (orchestra_db._utcnow(),))
        c.execute("DELETE FROM canonical WHERE root='qa-demo'")
        c.commit()
    finally:
        c.close()
    r = _run(orch, ["qa-demo", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["reestablished"] is True and out["generation"] == 2
    doc = _doc(orch, "qa-demo")
    assert doc["test"] is True and doc["reports_to"] == "integrator" and doc["role"] == "worker"
    assert doc["generation"] == 2
    assert _gen1(orch, "qa-demo")["resume_command"] is None
    agents = json.loads((orch / "registry.json").read_text())["agents"]
    assert agents["qa-demo"]["test"] is True and agents["qa-demo"]["reports_to"] == "integrator"
    old = agents.get("qa-demo-gen1")
    assert old is None or old["status"] == "retired"                      # never 'parked'


def test_respawn_of_a_retired_canonical_keeps_the_fields_and_retires_the_old_generation(orch):
    _make_test_seat(orch, "qa-demo")
    _retire_canonical(orch, "qa-demo")
    r = _run(orch, ["qa-demo", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out.get("respawned") is True and out["generation"] == 2
    doc = _doc(orch, "qa-demo")
    assert doc["test"] is True and doc["reports_to"] == "integrator" and doc["role"] == "worker"
    assert _gen1(orch, "qa-demo")["resume_command"] is None
    agents = json.loads((orch / "registry.json").read_text())["agents"]
    assert agents["qa-demo-gen1"]["status"] == "retired"


def test_a_real_seat_carries_no_test_flag(orch):
    r = _run(orch, ["dev-demo", "--runtime", "claude", "--model", "m", "--tier", "T2"])
    assert r.returncode == 0, r.stderr
    _retire_canonical(orch, "dev-demo")
    assert _run(orch, ["dev-demo", "--runtime", "claude", "--model", "m", "--tier", "T2"]).returncode == 0
    assert "test" not in _doc(orch, "dev-demo")


def test_retire_predecessors_is_off_without_the_cutover(tmp_path):
    (tmp_path / "state").mkdir()
    orchestra_db.init_db(str(tmp_path / "state" / "orchestra-registry.db"))
    assert identity_writer.retire_predecessors(str(tmp_path), "qa-demo", 2) == 0
