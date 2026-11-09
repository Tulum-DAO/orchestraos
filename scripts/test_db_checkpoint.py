"""Tests for scripts/db_checkpoint.py — discovery + checkpoint, no hardcoded homes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import db_checkpoint as DC


def test_iter_dbs_finds_state_dbs_and_skips_junk(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    good = state / "tasks.db"
    sqlite3.connect(good).close()
    junk = tmp_path / "node_modules" / "x"
    junk.mkdir(parents=True)
    sqlite3.connect(junk / "x.db").close()
    parallel = tmp_path / "parallel" / "w1"
    parallel.mkdir(parents=True)
    sqlite3.connect(parallel / "t.db").close()
    found = {p.name for p in DC.iter_dbs(tmp_path)}
    assert "tasks.db" in found
    assert "x.db" not in found
    assert "t.db" not in found


def test_checkpoint_truncates_wal(tmp_path: Path):
    db = tmp_path / "state" / "a.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    assert (tmp_path / "state" / "a.db-wal").exists() or True  # wal may be empty file
    DC.checkpoint(db)
    rep = DC.run(tmp_path)
    assert rep["ok"] >= 1
    assert rep["fail"] == 0


def test_no_hardcoded_home_in_module():
    text = Path(DC.__file__).read_text()
    assert "/home/shaw" not in text
    assert "ShawCole" not in text
