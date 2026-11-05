import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attention_store as S  # noqa: E402
import approval_schema  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)


@pytest.fixture
def store(tmp_path, armed):
    st = S.AttentionStore(db_path=str(tmp_path / "tasks.db"))
    st.migrate()
    return st


@pytest.fixture
def ledger(tmp_path, armed):
    st = ApprovalStore(db_path=str(tmp_path / "tasks.db"))
    st.migrate()
    return st


def test_unarmed_creates_nothing_and_reads_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # no sentinel
    monkeypatch.setattr(S, "_pending_said", False)
    st = S.AttentionStore(db_path=str(tmp_path / "t.db")); st.migrate()
    assert st.armed is False and st.rev() == 0 and st.list_events() == []
    assert "DDL PENDING (unarmed): m20261104_attention" in capsys.readouterr().err
    with pytest.raises(S.AttentionNotArmed):
        st.add_done("gm", "Shipped X")
    tables = {r[0] for r in sqlite3.connect(str(tmp_path / "t.db")).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("attention_") for t in tables)


def test_a_removed_arm_reads_as_unarmed_even_with_the_tables(tmp_path, monkeypatch):
    """Rollback step 1 is removing the sentinel: the tables stay, and the code must ignore them."""
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    st = S.AttentionStore(db_path=str(tmp_path / "t.db")); st.migrate()
    st.add_done("gm", "Shipped X", key="k")
    monkeypatch.delenv("APPROVAL_DDL_ARMED")
    monkeypatch.setenv("HOME", str(tmp_path))
    st2 = S.AttentionStore(db_path=str(tmp_path / "t.db")); st2.migrate()
    assert st2.armed is False and st2.list_events() == [] and st2.rev() == 0


def test_migrate_creates_the_tables_and_the_rules_epoch_once(store):
    c = sqlite3.connect(store.db_path)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"attention_events", "attention_seen", "attention_meta", "attention_rules",
            "attention_rule_fires"} <= tables
    epoch = store.meta("rules_epoch")
    assert epoch
    store.migrate()
    assert store.meta("rules_epoch") == epoch


def test_done_is_resolved_at_birth_and_deduped_only_by_key(store):
    a = store.add_done("gm", "Shipped X", key="k1", body="b", now=T0)
    assert store.add_done("gm", "Shipped X", key="k1", now=T0 + timedelta(seconds=30)) == a   # a retry
    assert store.add_done("gm", "Shipped X", key="k2", now=T0 + timedelta(seconds=30)) != a   # a second note
    assert store.get(a)["resolved_at"] == store.get(a)["created_at"]
    assert store.get(a)["resolved_reason"] == "done_note" and store.get(a)["id"].startswith("evt_")


def test_one_row_per_condition_forever_and_reopen_makes_it_unread(store):
    e1 = store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped", now=T0)
    assert store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped", now=T0) == e1
    store.mark_seen("alex", [e1], "web", now=T0)
    store.resolve(e1, "cleared", now=T0 + timedelta(seconds=120))
    e2 = store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped",
                              now=T0 + timedelta(days=30))
    row = store.get(e1)
    assert e2 == e1 and row["occurrences"] == 2 and row["resolved_at"] is None and row["reopened_at"]
    assert store.seen_map("alex", [e1]) == {}


def test_refreshing_an_open_condition_is_not_a_visible_change(store):
    """observed_at / clear_streak are bookkeeping: the /events digest must not ping every tick."""
    e1 = store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped", now=T0)
    r = store.rev()
    store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "gm stopped", now=T0 + timedelta(minutes=1))
    assert store.note_absent(e1) == 1
    assert store.rev() == r
    assert store.get(e1)["observed_at"] == (T0 + timedelta(minutes=1)).isoformat()


def test_open_condition_zeroes_the_clear_streak(store):
    e1 = store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "x", now=T0)
    store.note_absent(e1)
    store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "x", now=T0)
    assert store.get(e1)["clear_streak"] == 0


def test_link_card_upgrades_the_mirror_row_and_never_duplicates(store, ledger):
    card = ledger.create(from_agent="gm", question="Done X", worker_kind="pane",
                         options=["accept", "respond"], kind="completion", evidence={})
    S.mirror_completions(store, ledger)                              # detector got there first
    eid = store.add_done("gm", "Done X (rich)", key="k", body="details", source_id=card)
    assert [e["id"] for e in store.list_events()] == [eid] and store.get(eid)["body"] == "details"


def test_missed_call_is_a_type_with_a_writer_and_resolved_at_birth(store):
    e = store.record_missed_call("vc_1", "gm", "gm tried to call you", now=T0)
    assert store.record_missed_call("vc_1", "gm", "gm tried to call you", now=T0) == e
    row = store.get(e)
    assert row["kind"] == "missed_call" and row["dedup_key"] == "missed_call:vc_1" and row["resolved_at"]


def test_seen_is_first_write_wins_and_bumps_rev(store):
    r0 = store.rev()
    assert store.mark_seen("alex", ["evt_x"], "watch", now=T0) == 1
    assert store.mark_seen("alex", ["evt_x"], "web", now=T0 + timedelta(seconds=5)) == 0
    assert store.seen_map("alex", ["evt_x"]) == {"evt_x": (T0.isoformat(), "watch")}
    assert store.rev() == r0 + 1


def test_unread_count_is_a_true_total_and_honours_agents(store):
    for i in range(5):
        store.add_done("gm" if i % 2 else "pm", f"n{i}", key=f"k{i}", now=T0 + timedelta(seconds=i))
    evs = store.list_events(limit=2)
    store.mark_seen("alex", [evs[0]["id"]], "web")
    assert len(evs) == 2
    assert store.unread_count("alex") == 4
    assert store.unread_count("alex", agents=["gm"]) == 2 - (evs[0]["agent"] == "gm")


def test_updates_order_open_first_then_newest(store):
    a = store.add_done("gm", "old done", key="a", now=T0)
    b = store.open_condition("failed", "gm", "crashed", "failed:gm:crashed", "x", now=T0 - timedelta(days=1))
    c = store.add_done("gm", "new done", key="c", now=T0 + timedelta(minutes=1))
    assert [e["id"] for e in store.list_events()] == [b, c, a]


def test_busy_ledger_raises_attention_busy_after_bounded_retries(store, monkeypatch):
    monkeypatch.setattr(S, "_BACKOFF_S", (0.01, 0.01, 0.01))
    blocker = sqlite3.connect(store.db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(S, "_BUSY_MS", 50)
    try:
        with pytest.raises(S.AttentionBusy):
            store.add_done("gm", "x", key="k")
    finally:
        blocker.execute("ROLLBACK")
    assert store.add_done("gm", "x", key="k")


_WRITER = """
import sys
sys.path.insert(0, {here!r})
import attention_store as S
st = S.AttentionStore(db_path={db!r}); st.migrate()
for i in range(25):
    st.open_condition("failed", "gm", "crashed", "failed:a%d:crashed" % (i % 10), "x")
    st.add_done("gm", "sub", key="{tag}-%d" % i)
"""


def test_concurrent_writers_never_duplicate_and_rev_counts_every_change(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    db = str(tmp_path / "tasks.db")
    st = S.AttentionStore(db_path=db); st.migrate()
    errors = []

    def worker(n):
        try:
            for i in range(50):
                st.open_condition("failed", "gm", "crashed", f"failed:a{i % 10}:crashed", "x")
                if i % 5 == 0:
                    st.add_done("gm", "thread", key=f"t{n}-{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    env = {**os.environ}
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER.format(here=str(HERE), db=db, tag=f"p{k}")],
                              env=env, stderr=subprocess.PIPE) for k in range(2)]
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()
    assert errors == []
    c = sqlite3.connect(db)
    conds = c.execute("SELECT COUNT(*) FROM attention_events WHERE kind='failed'").fetchone()[0]
    dones = c.execute("SELECT COUNT(*) FROM attention_events WHERE kind='done'").fetchone()[0]
    assert conds == 10                         # one row per condition key, whoever wrote it
    assert dones == 4 * 10 + 2 * 25            # every distinct done note, none merged, none lost
    assert st.rev() == conds + dones           # every visible change bumped rev exactly once


def test_pytest_fence_refuses_the_live_db():
    with pytest.raises(Exception):
        S.AttentionStore(db_path=str(S.approval_config.LIVE_DB_PATH))


# ---- the ledger side of the same arm: urgency, and the duplicate-column race -----------------

def test_urgency_column_rides_the_same_arm(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    st = ApprovalStore(db_path=db); st.migrate()
    rid = st.create(from_agent="gm", question="q", worker_kind="pane", urgency=3)   # unarmed: accepted, not stored
    assert "urgency" not in st.get(rid)
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    st.migrate()
    rid = st.create(from_agent="gm", question="q2", worker_kind="pane", urgency=3)
    assert st.get(rid)["urgency"] == 3


def test_two_migrations_racing_on_one_file_never_fail_on_duplicate_column(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    db = str(tmp_path / "t.db")
    base = ApprovalStore(db_path=db)
    monkeypatch.delenv("APPROVAL_DDL_ARMED")
    monkeypatch.setenv("HOME", str(tmp_path))
    base.migrate()                                   # the pre-arm schema
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    # Simulate the loser of the race: its PRAGMA read says "absent" but the winner already added it.
    c = sqlite3.connect(db)
    c.execute("ALTER TABLE approval_requests ADD COLUMN urgency INTEGER")
    c.commit(); c.close()
    orig_conn = ApprovalStore._conn

    class _StalePragma:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA table_info(approval_requests)"):
                rows = self._c.execute(sql, *a).fetchall()

                class _R:
                    def fetchall(_s):
                        return [r for r in rows if r[1] != "urgency"]
                return _R()
            return self._c.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self._c, n)
    monkeypatch.setattr(ApprovalStore, "_conn", lambda self: _StalePragma(orig_conn(self)))
    ApprovalStore(db_path=db).migrate()              # must not raise "duplicate column name"
    assert "urgency" in {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(approval_requests)")}


def test_approval_cli_urgency_flag(tmp_path, monkeypatch, capsys):
    import approval
    monkeypatch.setenv("APPROVAL_DDL_ARMED", S.MIGRATION_ID)
    monkeypatch.setenv("APPROVAL_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(approval, "_authorship_guard", lambda a: True)
    monkeypatch.setattr(approval, "notify", lambda *a, **k: None)
    assert approval.main(["request", "Ship?", "--from", "gm", "--worker-kind", "pane", "--urgency", "2"]) == 0
    rid = capsys.readouterr().out.strip()
    assert ApprovalStore(db_path=str(tmp_path / "t.db")).get(rid)["urgency"] == 2
    assert approval.main(["human-task", "--from", "gm", "--task", "Plug in", "--op-key", "k1",
                          "--urgency", "1"]) == 0
    rid = capsys.readouterr().out.strip()
    assert ApprovalStore(db_path=str(tmp_path / "t.db")).get(rid)["urgency"] == 1
    with pytest.raises(SystemExit):
        approval.main(["request", "Ship?", "--from", "gm", "--worker-kind", "pane", "--urgency", "7"])
