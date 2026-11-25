"""`approval.py patch` is guarded: only the author (or its current generation) or gm may change a pending
human_task card, only the allow-listed fields change, and the UPDATE itself re-checks the row is still a pending
human_task (a card that was answered between the check and the write stays as answered).

Hermetic: tmp tasks.db via APPROVAL_DB_PATH, HOME a tmp dir, ntfy fenced; no registry beside the tmp db, so a
caller's name is taken as its own root (the same evidence rule retire uses).
"""
import os, sqlite3, subprocess, sys

import pytest
from approval_schema import ApprovalStore

HERE = os.path.dirname(os.path.abspath(__file__))
NO_NTFY = "/nonexistent/ntfy-token-for-tests"


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN_FILE", NO_NTFY)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)


REG_DDL = """
CREATE TABLE lineages (root TEXT PRIMARY KEY);
CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, generation INTEGER NOT NULL,
  retired_at TEXT, UNIQUE(root, generation));
CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER NOT NULL, tmux_session TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online');
"""


def _registry(d):
    """postcall-dev: g3 retired, g4 canonical. gm: g11 retired, g12 canonical. some-other-seat: g1 canonical."""
    c = sqlite3.connect(os.path.join(d, "orchestra-registry.db"))
    c.executescript(REG_DDL)
    for root, gens, cur in (("postcall-dev", (3, 4), 4), ("gm", (11, 12), 12), ("some-other-seat", (1,), 1)):
        for n in gens:
            gid = c.execute("INSERT INTO generations(root, generation, retired_at) VALUES (?,?,?)",
                            (root, n, None if n == cur else "x")).lastrowid
            if n == cur:
                c.execute("INSERT INTO canonical VALUES (?,?,?, 'online')", (root, gid, root))
    c.commit(); c.close()


@pytest.fixture
def db(tmp_path):
    _registry(str(tmp_path))
    return str(tmp_path / "tasks.db")


@pytest.fixture
def store(db):
    s = ApprovalStore(db_path=db); s.migrate(); return s


def _task(s, author="postcall-dev"):
    return s.create(from_agent=author, question="Call Dana back", worker_kind="pane",
                    options=["done", "cant", "snooze"], kind="human_task", block_task="Call Dana back")


def _patch(db, rid, caller, *extra):
    env = dict(os.environ, APPROVAL_DB_PATH=db)
    return subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "patch", "--id", rid,
                          "--from", caller, *extra], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("caller", ["postcall-dev", "postcall-dev-g4", "gm", "gm-g12"])
def test_author_its_generation_and_gm_may_patch(store, caller):
    rid = _task(store)
    assert store.update_fields(rid, caller=caller, summary="moved to Friday") is True
    assert store.get(rid)["summary"] == "moved to Friday"


@pytest.mark.parametrize("caller", ["some-other-seat", "some-other-seat-g1", "postcall-dev-g3", "gm-g11", "operator"])
def test_anyone_else_may_not_patch_and_the_row_is_untouched(store, caller):
    rid = _task(store)
    before = store.get(rid)
    assert store.update_fields(rid, caller=caller, summary="hijacked") is False
    assert store.get(rid) == before


def test_the_cli_refuses_a_stranger_and_says_why(store, db):
    rid = _task(store)
    r = _patch(db, rid, "some-other-seat", "--summary", "hijacked")
    assert r.returncode == 4 and r.stdout.strip() == ""
    assert "REFUSED" in r.stderr and "some-other-seat" in r.stderr and "postcall-dev" in r.stderr
    assert store.get(rid)["summary"] != "hijacked"


def test_the_cli_lets_the_author_reschedule(store, db):
    rid = _task(store)
    r = _patch(db, rid, "postcall-dev", "--evidence", '{"due_ts":"2026-11-28T15:00:00Z","due_source":"call"}')
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    assert "2026-11-28" in store.get(rid)["evidence"]


def test_only_allow_listed_fields_change(store):
    rid = _task(store)
    assert store.update_fields(rid, caller="gm", status="answered", from_agent="gm", summary="s") is True
    row = store.get(rid)
    assert row["status"] == "pending" and row["from_agent"] == "postcall-dev" and row["summary"] == "s"


def test_a_row_answered_between_the_check_and_the_write_is_not_changed(store, db, monkeypatch):
    rid = _task(store)
    real_conn = store._conn
    state = {"flipped": False}

    class Hooked:
        """A connection whose first UPDATE runs after another writer answered the card."""
        def __init__(self, c): self._c = c
        def __getattr__(self, name): return getattr(self._c, name)
        def execute(self, sql, *a, **kw):
            if sql.lstrip().startswith("UPDATE approval_requests SET") and not state["flipped"]:
                state["flipped"] = True
                other = sqlite3.connect(db)
                other.execute("UPDATE approval_requests SET status='answered', answer='done' WHERE id=?", [rid])
                other.commit(); other.close()
            return self._c.execute(sql, *a, **kw)

    monkeypatch.setattr(store, "_conn", lambda: Hooked(real_conn()))
    assert store.update_fields(rid, caller="gm", summary="late") is False
    assert state["flipped"]
    monkeypatch.undo()
    row = store.get(rid)
    assert row["status"] == "answered" and row["summary"] != "late"
