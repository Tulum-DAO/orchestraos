"""#15: read_canonical_blue must surface the seat's lineage runtime, so blue_record
carries it through real_seams_for -> make_swap_fn -> build_swap_documents and the
promoted seat gets a per-runtime resume_command (agy --conversation <cid>) instead of
resume_command=None (the silent seat-corruption class). A missing lineage row must NOT
fail-closed the swap (LEFT JOIN -> runtime None -> the builder refuses, never guesses).
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_beat import read_canonical_blue  # noqa: E402


def _db(with_lineage=True, runtime="gemini"):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "state"))
    p = os.path.join(d, "state", "orchestra-registry.db")
    c = sqlite3.connect(p)
    c.executescript(
        "CREATE TABLE lineages(root TEXT PRIMARY KEY, runtime TEXT);"
        "CREATE TABLE generations(id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, "
        "  model TEXT, session_id TEXT);"
        "CREATE TABLE canonical(root TEXT PRIMARY KEY, generation_id INTEGER);"
    )
    c.execute("INSERT INTO generations(id,root,generation,model,session_id) VALUES "
              "(50,'demo-gemini-pred2',2,'gemini-3.7-flash','cid-xyz')")
    c.execute("INSERT INTO canonical(root,generation_id) VALUES ('demo-gemini-pred2',50)")
    if with_lineage:
        c.execute("INSERT INTO lineages(root,runtime) VALUES ('demo-gemini-pred2',?)", (runtime,))
    c.commit()
    c.close()
    return d


def test_read_canonical_blue_surfaces_gemini_runtime():
    d = _db(with_lineage=True, runtime="gemini")
    blue = read_canonical_blue(d, "demo-gemini-pred2")
    assert blue["runtime"] == "gemini"
    assert blue["model"] == "gemini-3.7-flash"
    assert blue["generation"] == 2


def test_read_canonical_blue_missing_lineage_runtime_is_none_not_error():
    # LEFT JOIN: a seat with no lineage row still resolves (runtime None), never
    # fails-closed the swap — the builder then refuses a resume_command (safe).
    d = _db(with_lineage=False)
    blue = read_canonical_blue(d, "demo-gemini-pred2")
    assert blue["runtime"] is None
    assert blue["generation"] == 2
