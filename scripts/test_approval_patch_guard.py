"""`approval.py patch` is guarded: only the author (or its current generation) or gm may change a pending
human_task card, only the allow-listed fields change, and the UPDATE itself re-checks the row is still a pending
human_task (a card that was answered between the check and the write stays as answered).

Hermetic: tmp tasks.db via APPROVAL_DB_PATH, HOME a tmp dir, ntfy fenced, TMUX unset (the CLI's interposer guard is
fail-open with no pane signal and is exercised in-process with a planted caller). A tmp registry beside the db
carries the generation evidence; the no-registry case (names compared literally) has its own test.
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
    # patch runs the interposer guard: no inherited pane identity (test_approval.py's _no_inherited_tmux_identity)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


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
                          "--from", caller, *extra], capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("caller", ["postcall-dev", "postcall-dev-g4", "gm", "gm-g12"])
def test_author_its_generation_and_gm_may_patch(store, caller):
    rid = _task(store)
    assert store.update_fields(rid, caller=caller, summary="moved to Friday") is True
    assert store.get(rid)["summary"] == "moved to Friday"


@pytest.mark.parametrize("caller", ["some-other-seat", "some-other-seat-g1", "postcall-dev-g3", "postcall-dev-g99",
                                    "gm-g11", "gm-g99", "operator", "shaw"])
def test_anyone_else_may_not_patch_and_the_row_is_untouched(store, caller):
    rid = _task(store)
    before = store.get(rid)
    assert store.update_fields(rid, caller=caller, summary="hijacked") is False
    assert store.get(rid) == before


def test_the_cli_refuses_a_stranger_and_says_why(store, db):
    rid = _task(store)
    r = _patch(db, rid, "some-other-seat", "--summary", "hijacked")
    assert r.returncode == 4 and r.stdout.strip() == ""
    assert "REFUSED" in r.stderr and "some-other-seat may not patch a card authored by postcall-dev" in r.stderr
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


def test_no_registry_compares_names_literally(tmp_path, monkeypatch):
    """With no registry beside the db there is no evidence, so only the exact author name or gm passes."""
    d = tmp_path / "bare"; d.mkdir()
    s = ApprovalStore(db_path=str(d / "tasks.db")); s.migrate()
    rid = _task(s)
    assert s.update_fields(rid, caller="postcall-dev-g4", summary="x") is False
    assert s.update_fields(rid, caller="gm-g12", summary="x") is False
    assert s.update_fields(rid, caller="postcall-dev", summary="by author") is True
    assert s.update_fields(rid, caller="gm", summary="by gm") is True


def test_without_a_caller_the_in_process_path_still_patches(store):
    """reshape_commitment resolves its row by author + op_key and passes no caller: unchanged by the guard."""
    rid = store.create(from_agent="postcall-dev", question="Call Dana back", worker_kind="pane",
                       op_key="cmt:dana", options=["done", "cant", "snooze"], kind="human_task",
                       block_task="Call Dana back")
    assert store.update_fields(rid, summary="plain") is True
    assert store.reshape_commitment("postcall-dev", "cmt:dana", feature="calls", block_task="Call Dana back Friday",
                                    summary="moved", blocks_what=None) == rid
    row = store.get(rid)
    assert row["summary"] == "moved" and row["question"] == "Call Dana back Friday"


def test_a_stale_generation_is_told_why(store, db):
    import seat_identity
    ok, why = seat_identity.may_patch(seat_identity.registry_beside(db), "postcall-dev-g3", "postcall-dev")
    assert not ok and "current canonical generation" in why
    ok, why = seat_identity.may_patch(seat_identity.registry_beside(db), "some-other-seat", "postcall-dev")
    assert not ok and "some-other-seat" in why and "postcall-dev" in why and "patch" in why


def _main(db, monkeypatch, identities, caller, *extra):
    """Run the CLI in-process with a planted pane identity (what the interposer guard would resolve)."""
    import approval
    monkeypatch.setenv("APPROVAL_DB_PATH", db)
    monkeypatch.setattr(approval, "_caller_identities", lambda: identities)
    return approval.main(["patch", "--id", extra[0], "--from", caller, *extra[1:]])


def test_the_cli_refuses_a_seat_that_borrows_the_authors_name(store, db, monkeypatch, capsys):
    rid = _task(store)
    rc = _main(db, monkeypatch, {"some-other-seat"}, "postcall-dev", rid, "--summary", "borrowed")
    err = capsys.readouterr().err
    assert rc == 4 and "interposer" in err and "only the author of" in err
    assert "--delegated-by" not in err, "patch has no --delegated-by: the generic advice must not appear at all"
    assert store.get(rid)["summary"] != "borrowed"


def test_the_cli_lets_the_seat_that_is_the_author_patch(store, db, monkeypatch, capsys):
    rid = _task(store)
    rc = _main(db, monkeypatch, {"postcall-dev"}, "postcall-dev", rid, "--summary", "mine")
    assert rc == 0, capsys.readouterr().err
    assert store.get(rid)["summary"] == "mine"


def test_the_cli_tells_a_stale_generation_why(store, db, monkeypatch, capsys):
    """The REFUSED line itself carries the specific reason (the _patch_refusal caller= wiring)."""
    rid = _task(store)
    rc = _main(db, monkeypatch, None, "postcall-dev-g3", rid, "--summary", "late")
    err = capsys.readouterr().err
    assert rc == 4 and "REFUSED" in err and "current canonical generation" in err
