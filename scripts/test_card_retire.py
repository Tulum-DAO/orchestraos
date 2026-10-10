"""Card RETIRE verb (operator ruling 2026-10-10 via gm msg_c16b5264; design approved gm msg_3f3a4d05).

An author (or its current generation) or gm may mark a PENDING card "no longer needed". It leaves the
pending feed, stays in history with who/why/what replaced it, sends nothing and resumes nothing. Menus are
never retired (menu-bridge resolves them from the pane). Storage = gated migration m20261010_card_retire:
until the operator's arm is present the verb REFUSES rather than half-writing.

Hermetic: tmp tasks.db with a tmp registry beside it; HOME is a tmp dir so the live ~/runtime sentinel
cannot arm these tests by accident.
"""
import json, os, sqlite3, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
import approval_schema
from approval_schema import ApprovalStore, RETIRE_MIGRATION, export_retired
from questionnaire_schema import QuestionnaireStore

HERE = os.path.dirname(os.path.abspath(__file__))
REG_DDL = """
CREATE TABLE lineages (root TEXT PRIMARY KEY);
CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, generation INTEGER NOT NULL,
  retired_at TEXT, UNIQUE(root, generation));
CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER NOT NULL, tmux_session TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online');
"""
COLS = ("retired_at", "retired_by", "retire_reason", "superseded_by")


def _registry(d):
    """builder: g5 retired, g6 canonical. gm: g101 retired, g102 canonical. other-seat: g1 canonical."""
    c = sqlite3.connect(os.path.join(d, "orchestra-registry.db"))
    c.executescript(REG_DDL)
    for root, gens, cur in (("builder", (5, 6), 6), ("gm", (101, 102), 102), ("other-seat", (1,), 1)):
        for n in gens:
            gid = c.execute("INSERT INTO generations(root, generation, retired_at) VALUES (?,?,?)",
                            (root, n, None if n == cur else "x")).lastrowid
            if n == cur:
                c.execute("INSERT INTO canonical VALUES (?,?,?, 'online')", (root, gid, root))
    c.commit(); c.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"; (h / "runtime").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    return h


@pytest.fixture
def armed(home, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", RETIRE_MIGRATION)


@pytest.fixture
def db(tmp_path):
    _registry(str(tmp_path))
    return str(tmp_path / "tasks.db")


@pytest.fixture
def store(db, armed):
    s = ApprovalStore(db_path=db); s.migrate(); return s


@pytest.fixture
def qstore(db, armed):
    ApprovalStore(db_path=db).migrate()
    q = QuestionnaireStore(db_path=db); q.migrate(); return q


NO_NTFY = "/nonexistent/ntfy-token-for-tests"


@pytest.fixture(autouse=True)
def ntfy_fenced(monkeypatch):
    """surface-decision VERIFY 5: a scratch DB is not enough, notify() would still POST to ntfy."""
    monkeypatch.setenv("NTFY_TOKEN_FILE", NO_NTFY)
    assert os.environ["NTFY_TOKEN_FILE"] == NO_NTFY and not os.path.exists(NO_NTFY)


@pytest.fixture(autouse=True)
def no_notify(monkeypatch):
    """Retire must never notify. Any POST to ntfy (or the notify entry point) fails the test."""
    import approval_notify, urllib.request
    calls = []
    def boom(*a, **k):
        calls.append((a, k)); raise AssertionError("retire must not notify")
    monkeypatch.setattr(approval_notify, "notify", boom)
    real = urllib.request.urlopen
    def guarded(req, *a, **k):
        url = getattr(req, "full_url", req)
        if "ntfy" in str(url):
            boom(url)
        return real(req, *a, **k)
    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    return calls


def _card(s, author="builder-g5", kind=None):
    kw = {"kind": kind} if kind else {}
    return s.create(from_agent=author, question="Ship the thing?", worker_kind="pane", **kw)


def _qnr(q, author="builder-g5"):
    return q.create(from_agent=author, title="Three questions", summary="s",
                    questions=[{"prompt": "A?", "kind": "menu", "menu": {"options": ["x", "y"]}}])


# ---------------------------------------------------------------- who

@pytest.mark.parametrize("caller", ["builder-g5", "builder", "builder-g6", "gm", "gm-g102"])
def test_author_successor_and_gm_may_retire(store, caller):
    rid = _card(store)
    ok, why = store.retire(rid, caller, "superseded by the new plan")
    assert ok, why
    row = store.get(rid)
    assert row["status"] == "retired"
    assert row["retired_by"] == caller and row["retire_reason"] == "superseded by the new plan"
    assert row["retired_at"]
    assert "RETIRED" in row["summary"] and "superseded by the new plan" in row["summary"]


@pytest.mark.parametrize("caller", ["other-seat", "gm-g101", "builder-g4", "operator", "builderx"])
def test_anyone_else_is_refused(store, caller):
    rid = _card(store)
    ok, why = store.retire(rid, caller, "nope")
    assert not ok and why
    assert store.get(rid)["status"] == "pending"


def test_a_stale_generation_of_the_author_is_refused(store):
    rid = _card(store, author="builder-g6")
    ok, why = store.retire(rid, "builder-g5", "stale gen")
    assert not ok and "current" in why


# ---------------------------------------------------------------- when

@pytest.mark.parametrize("terminal", ["answered", "discarded", "expired", "resumed", "retired"])
def test_only_a_pending_card_can_be_retired(store, db, terminal):
    rid = _card(store)
    c = sqlite3.connect(db); c.execute("UPDATE approval_requests SET status=? WHERE id=?", (terminal, rid))
    c.commit(); c.close()
    ok, why = store.retire(rid, "gm", "late")
    assert not ok and terminal in why
    assert store.get(rid)["status"] == terminal


def test_a_menu_card_is_never_retired(store):
    rid = _card(store, kind="menu")
    ok, why = store.retire(rid, "gm", "menu")
    assert not ok and "menu" in why
    assert store.get(rid)["status"] == "pending"


def test_reason_is_required(store):
    rid = _card(store)
    for reason in ("", "   ", None):
        ok, why = store.retire(rid, "gm", reason)
        assert not ok and "reason" in why
    assert store.get(rid)["status"] == "pending"


def test_superseded_by_must_name_another_existing_card(store, qstore):
    rid, other = _card(store), _card(store)
    q = _qnr(qstore)
    assert not store.retire(rid, "gm", "x", superseded_by="apr_doesnotexist")[0]
    assert not store.retire(rid, "gm", "x", superseded_by=rid)[0]
    ok, why = store.retire(rid, "gm", "replaced", superseded_by=q)     # a qnr may replace an apr
    assert ok, why
    assert store.get(rid)["superseded_by"] == q
    ok, why = store.retire(other, "gm", "replaced", superseded_by=rid)  # any status is fine for the target
    assert ok, why


def test_unknown_id_is_refused(store):
    ok, why = store.retire("apr_nope", "gm", "x")
    assert not ok and "apr_nope" in why


# ---------------------------------------------------------------- storage gate

def test_unarmed_the_verb_refuses_and_writes_nothing(db, home):
    s = ApprovalStore(db_path=db); s.migrate()
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(approval_requests)")}
    assert not (set(COLS) & cols)                     # unarmed: the columns do not exist
    rid = _card(s)
    ok, why = s.retire(rid, "gm", "x")
    assert not ok and "not armed" in why
    assert s.get(rid)["status"] == "pending"
    # history and pending still work on an unarmed schema (no reference to missing columns)
    assert s.history(10) == [] and [r["id"] for r in s.pending_to_notify()] == [rid]


def test_arming_adds_the_four_columns_to_both_tables(db, armed):
    ApprovalStore(db_path=db).migrate(); QuestionnaireStore(db_path=db).migrate()
    c = sqlite3.connect(db)
    for table in ("approval_requests", "questionnaires"):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        assert set(COLS) <= cols, table


# ---------------------------------------------------------------- effect

def test_retired_leaves_pending_and_shows_in_history(store, no_notify):
    rid, keep = _card(store), _card(store)
    assert store.retire(rid, "gm", "obsolete")[0]
    assert [r["id"] for r in store.pending_to_notify()] == [keep]
    hist = store.history(10)
    assert [r["id"] for r in hist] == [rid] and hist[0]["status"] == "retired"
    assert no_notify == []


def test_retire_never_resumes(store, monkeypatch):
    import approval_resume
    fired = []
    monkeypatch.setattr(approval_resume, "fire_resume", lambda *a, **k: fired.append(a))
    rid = _card(store)
    assert store.retire(rid, "gm", "x")[0]
    assert fired == [] and store.answered_unacked() == []


def test_questionnaire_retire_same_rules(qstore, no_notify):
    q = _qnr(qstore)
    assert not qstore.retire(q, "other-seat", "x")[0]
    assert not qstore.retire(q, "gm", "")[0]
    ok, why = qstore.retire(q, "builder", "folded into the new plan")
    assert ok, why
    assert qstore.list_pending() == []
    hist = qstore.history(10)
    assert [h["id"] for h in hist] == [q] and hist[0]["status"] == "retired"
    assert hist[0]["retire_reason"] == "folded into the new plan"
    ok, why = qstore.retire(q, "gm", "again")
    assert not ok and "retired" in why
    assert no_notify == []


# ---------------------------------------------------------------- export (client-safe default)

def test_export_retired_maps_to_discarded_plus_an_additive_object():
    row = {"id": "apr_1", "status": "retired", "retired_at": "2026-10-10T06:00:00Z", "retired_by": "gm",
           "retire_reason": "obsolete", "superseded_by": "apr_2", "answered_at": None}
    out = export_retired(dict(row))
    assert out["status"] == "discarded"
    assert out["retired"] == {"by": "gm", "reason": "obsolete", "superseded_by": "apr_2",
                              "at": "2026-10-10T06:00:00Z"}
    assert out["answered_at"] == "2026-10-10T06:00:00Z"        # sorts and reads as when it ended
    plain = {"id": "apr_3", "status": "answered", "answered_at": "t"}
    assert export_retired(dict(plain)) == plain                  # every other row untouched


def test_gateway_history_and_detail_use_the_safe_export(store, qstore, monkeypatch, db):
    import watch_gateway as G
    import asyncio
    monkeypatch.setattr(G, "ApprovalStore", lambda: ApprovalStore(db_path=db))
    monkeypatch.setattr(G, "QuestionnaireStore", lambda: QuestionnaireStore(db_path=db))
    monkeypatch.setattr(G, "gateway_token", lambda: "tok")

    class R:
        def __init__(self, match_info=None):
            self.headers = {"Authorization": "Bearer tok"}; self.query = {}; self.match_info = match_info or {}
            self.method = "GET"; self.path = "/history"; self.remote = "127.0.0.1"
    rid, q = _card(store), _qnr(qstore)
    assert store.retire(rid, "gm", "obsolete")[0] and qstore.retire(q, "gm", "obsolete")[0]
    hist = json.loads(asyncio.run(G.handle_history(R())).text)["history"]
    by = {h["id"]: h for h in hist}
    for i in (rid, q):
        assert by[i]["status"] == "discarded" and by[i]["retired"]["reason"] == "obsolete", by[i]
    det = json.loads(asyncio.run(G.handle_approval_detail(R({"id": rid}))).text)
    row = det["approval"]
    assert row["status"] == "discarded" and row["retired"]["by"] == "gm"


# ---------------------------------------------------------------- CLI

def _cli(db, *args, env_extra=None):
    env = dict(os.environ, APPROVAL_DB_PATH=db, NTFY_TOKEN_FILE=NO_NTFY, **(env_extra or {}))
    return subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_cli_retire_exit_codes(store, db, home):
    rid = _card(store)
    env = {"HOME": str(home), "APPROVAL_DDL_ARMED": RETIRE_MIGRATION}
    r = _cli(db, "retire", "--id", rid, "--from", "other-seat", "--reason", "x", env_extra=env)
    assert r.returncode == 4 and "REFUSED" in r.stderr
    r = _cli(db, "retire", "--id", rid, "--from", "gm", "--reason", "obsolete", env_extra=env)
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    assert store.get(rid)["status"] == "retired"
    r = _cli(db, "retire", "--id", rid, "--from", "gm", env_extra=env)       # --reason missing
    assert r.returncode == 2
