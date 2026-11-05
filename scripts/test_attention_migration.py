"""The m20261104_attention migration on a ledger shaped like a live one, and rollback to the old code.

  * a populated WAL ledger with the pre-attention schema, a reader holding a snapshot open: migrate()
    succeeds and every old row is unchanged;
  * a repeated migrate() is a no-op;
  * the code from before part 1 (BASE, main before this change), run against the migrated DB, still serves /pending-approvals and
    /history exactly as the goldens say (old code ignores the new tables and columns);
  * migrate() in two processes at once, and migrate() racing add_done, never fail.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import attention_store as S  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
import test_attention_compat as C  # noqa: E402

PRE = "m20260825_answer_attribution,m20260825_human_task,m20261005_answer_device,m20261010_card_retire"
BASE = "fc801d6"            # main before part 1 (the release this branch lands on)


def _old_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", PRE)
    monkeypatch.setenv("HOME", str(tmp_path))
    db = str(tmp_path / "tasks.db")
    st = ApprovalStore(db_path=db); st.migrate()
    sqlite3.connect(db).execute("PRAGMA journal_mode=WAL").fetchall()
    for i in range(20):
        rid = st.create(from_agent=f"a{i % 3}", question=f"q{i}", worker_kind="pane")
        if i % 4 == 0:
            st.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    return db, st


def _dump(db):
    c = sqlite3.connect(db)
    old = [r[1] for r in c.execute("PRAGMA table_info(approval_requests)")
           if r[1] not in ("urgency", "default_option")]
    rows = c.execute(f"SELECT {', '.join(old)} FROM approval_requests ORDER BY id").fetchall()
    c.close()
    return rows


def test_populated_wal_ledger_with_an_open_reader_migrates_and_keeps_every_row(tmp_path, monkeypatch):
    db, st = _old_ledger(tmp_path, monkeypatch)
    before = _dump(db)
    reader = sqlite3.connect(db, isolation_level=None)
    reader.execute("BEGIN")
    assert reader.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0] == 20   # snapshot held open
    monkeypatch.setenv("APPROVAL_DDL_ARMED", PRE + "," + S.MIGRATION_ID)
    st.migrate()
    a = S.AttentionStore(db_path=db)
    assert set(a.migrate()) == {"attention_events", "attention_seen", "attention_meta", "attention_rules",
                                "attention_rule_fires"}
    reader.execute("COMMIT"); reader.close()
    assert _dump(db) == before
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(approval_requests)")}
    assert {"urgency", "default_option"} <= cols
    assert sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_repeated_migrate_is_a_no_op(tmp_path, monkeypatch):
    db, st = _old_ledger(tmp_path, monkeypatch)
    monkeypatch.setenv("APPROVAL_DDL_ARMED", PRE + "," + S.MIGRATION_ID)
    st.migrate()
    a = S.AttentionStore(db_path=db); a.migrate()
    schema = sqlite3.connect(db).execute("SELECT group_concat(sql, ';') FROM sqlite_master").fetchone()[0]
    epoch = a.meta("rules_epoch")
    st.migrate()
    assert a.migrate() == []
    assert sqlite3.connect(db).execute("SELECT group_concat(sql, ';') FROM sqlite_master").fetchone()[0] == schema
    assert a.meta("rules_epoch") == epoch and a.rev() == 0


_OLD_RUNNER = r"""
import asyncio, json, sys
sys.path.insert(0, "scripts")
import scripts.watch_gateway as G
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore
from aiohttp.test_utils import TestClient, TestServer
db, tok = sys.argv[1], sys.argv[2]
import pathlib
G.TOKEN_FILE = pathlib.Path(tok)
G.ApprovalStore = lambda *a, **k: ApprovalStore(db_path=db)
G.QuestionnaireStore = lambda *a, **k: QuestionnaireStore(db_path=db)
G._perm_pseudo_rows = lambda: []
async def go():
    async with TestClient(TestServer(G.build_app())) as c:
        h = {"Authorization": "Bearer " + open(tok).read().strip()}
        p = await (await c.get("/pending-approvals", headers=h)).json()
        hi = await (await c.get("/history", headers=h)).json()
        print(json.dumps({"pending": p["pending"], "history": hi["history"]}))
asyncio.run(go())
"""


def _old_tree(tmp_path):
    """The repo's scripts/ and orchestra_cli/ as of BASE, or skip when this checkout has no git history."""
    arc = tmp_path / "old.tar"
    r = subprocess.run(["git", "archive", "-o", str(arc), BASE, "scripts", "orchestra_cli", "services"],
                       cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"no git history for {BASE} here: {r.stderr.strip()}")
    out = tmp_path / "old"
    out.mkdir()
    with tarfile.open(arc) as t:
        t.extractall(out, filter="data")
    assert "_attention_tick" not in (out / "scripts" / "watch_gateway.py").read_text()   # really the old code
    return out


def test_the_old_code_still_serves_the_goldens_from_a_migrated_db(tmp_path, monkeypatch):
    db, ids = C._seed(tmp_path, monkeypatch)                    # seeded with every arm, attention included
    a = S.AttentionStore(db_path=db); a.migrate()
    S.mirror_completions(a, ApprovalStore(db_path=db))          # the new tables hold rows too
    a.add_done("gm", "a note", key="k")
    old = _old_tree(tmp_path)
    tok = tmp_path / "tok"; tok.write_text(C.FLEET)
    env = {**os.environ, "ORCHESTRA_DIR": str(tmp_path / "data")}
    r = subprocess.run([sys.executable, "-c", _OLD_RUNNER, db, str(tok)], cwd=str(old), env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert C._stable(got["pending"], ids) == json.loads((C.FIX / "pending.golden.json").read_text())
    assert C._stable(got["history"], ids) == json.loads((C.FIX / "history.golden.json").read_text())


_MIGRATOR = """
import sys
sys.path.insert(0, {here!r})
from approval_schema import ApprovalStore
import attention_store as S
ApprovalStore(db_path={db!r}).migrate()
a = S.AttentionStore(db_path={db!r}); a.migrate()
for i in range(20):
    a.add_done("gm", "n", key="{tag}-%d" % i)
"""


def test_migrations_in_two_processes_and_racing_writes_never_fail(tmp_path, monkeypatch):
    db, _ = _old_ledger(tmp_path, monkeypatch)
    env = {**os.environ, "APPROVAL_DDL_ARMED": PRE + "," + S.MIGRATION_ID}
    procs = [subprocess.Popen([sys.executable, "-c", _MIGRATOR.format(here=str(HERE), db=db, tag=f"p{k}")],
                              env=env, stderr=subprocess.PIPE) for k in range(3)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()[-2000:]
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM attention_events").fetchone()[0] == 60
    assert c.execute("SELECT COUNT(*) FROM attention_meta WHERE key='rules_epoch'").fetchone()[0] == 1
