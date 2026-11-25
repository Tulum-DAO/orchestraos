"""A successor may annotate its own predecessor's cards; `approval.py note` is the CLI verb.

append_note compared literal ids, so after a rotation `ios-watch-dev` (g21) could not annotate a card
authored by `ios-watch-dev-g18`, and stale asks stayed on the operator's surface for good. Same seat now =
both names resolve to the same root BY POSITIVE EVIDENCE in the registry (a `<root>-gN` name counts
only if generations has (root, N)); the caller must be the bare root or the CURRENT canonical
generation. Hermetic: a tmp registry next to a tmp tasks db (the store looks beside its own db).
"""
import os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore
import seat_identity as SI

REG_DDL = """
CREATE TABLE lineages (root TEXT PRIMARY KEY);
CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, generation INTEGER NOT NULL,
  retired_at TEXT, UNIQUE(root, generation));
CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER NOT NULL, tmux_session TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online');
"""


def _registry(dirpath, canonical_gen=21):
    """Mirrors the live shape for ios-watch-dev: (root, N) rows plus `<root>-gN` alias lineages, one
    of which (g19) even has its OWN parked canonical row."""
    c = sqlite3.connect(os.path.join(dirpath, "orchestra-registry.db"))
    c.executescript(REG_DDL)
    ids = {}
    for n in (17, 18, 19, 20, 21):
        cur = c.execute("INSERT INTO generations(root, generation, retired_at) VALUES (?,?,?)",
                        ("ios-watch-dev", n, None if n == canonical_gen else "x"))
        ids[n] = cur.lastrowid
        c.execute("INSERT INTO generations(root, generation, retired_at) VALUES (?,?,?)",
                  (f"ios-watch-dev-g{n}", 1, "x"))
    alias19 = c.execute("SELECT id FROM generations WHERE root='ios-watch-dev-g19'").fetchone()[0]
    c.execute("INSERT INTO canonical VALUES ('ios-watch-dev', ?, 'ios-watch-dev', 'online')",
              (ids[canonical_gen],))
    c.execute("INSERT INTO canonical VALUES ('ios-watch-dev-g19', ?, 'ios-watch-dev-g19', 'parked')",
              (alias19,))
    c.execute("INSERT INTO generations(root, generation) VALUES ('ios-watch-devx', 1)")
    c.commit(); c.close()


@pytest.fixture
def store(tmp_path):
    _registry(str(tmp_path))
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate()
    return s


def _card(s, author="ios-watch-dev-g18"):
    return s.create(from_agent=author, question="Verify build 234?", worker_kind="pane")


@pytest.mark.parametrize("caller", ["ios-watch-dev", "ios-watch-dev-g21"])
def test_current_successor_annotates_predecessor_card(store, caller):
    rid = _card(store)
    ok, why = store.append_note(rid, caller, "superseded: phone is on build 257")
    assert ok, why
    assert "build 257" in store.get(rid)["summary"]


@pytest.mark.parametrize("caller", ["ios-watch-devx", "ios-watchdog", "ios-watch-dev-x", "ios-watch-dev-g99"])
def test_a_different_or_unproven_seat_is_still_refused(store, caller):
    rid = _card(store)
    ok, why = store.append_note(rid, caller, "nope")
    assert not ok and "not the author" in why


def test_a_stale_non_canonical_generation_is_refused(store):
    rid = _card(store, author="ios-watch-dev-g18")
    ok, why = store.append_note(rid, "ios-watch-dev-g20", "stale")
    assert not ok and "current" in why


def test_author_and_operator_unchanged(store):
    rid = _card(store)
    assert store.append_note(rid, "ios-watch-dev-g18", "own")[0]
    assert store.append_note(rid, "operator", "operator")[0]


def test_card_by_the_bare_root_is_annotatable_by_its_current_generation(store):
    rid = _card(store, author="ios-watch-dev")
    assert store.append_note(rid, "ios-watch-dev-g21", "ok")[0]


def test_no_registry_falls_back_to_literal_ids(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate()
    rid = _card(s)
    ok, why = s.append_note(rid, "ios-watch-dev", "x")
    assert not ok and "not the author" in why


def test_questionnaire_gets_the_same_rule(tmp_path):
    _registry(str(tmp_path))
    q = QuestionnaireStore(db_path=str(tmp_path / "tasks.db")); q.migrate()
    qid = q.create(from_agent="ios-watch-dev-g18", title="T",
                   questions=[{"prompt": "p", "kind": "free_text"}])
    assert q.append_note(qid, "ios-watch-dev", "succ")[0]
    assert not q.append_note(qid, "ios-watch-dev-g20", "stale")[0]
    assert not q.append_note(qid, "ios-watchdog", "other")[0]


def test_resolver_requires_positive_evidence(tmp_path):
    _registry(str(tmp_path))
    reg = os.path.join(str(tmp_path), "orchestra-registry.db")
    assert SI.resolve(reg, "ios-watch-dev-g18") == ("ios-watch-dev", 18)
    assert SI.resolve(reg, "ios-watch-dev") == ("ios-watch-dev", None)
    assert SI.resolve(reg, "ios-watch-dev-g99") == ("ios-watch-dev-g99", None)   # no (root, 99) row
    assert SI.resolve(reg, "ios-watchdog") == ("ios-watchdog", None)


# ---- -genN suffixes, and gm as ORPHAN STEWARD ---------------------------------

def _registry_gm(dirpath):
    _registry(dirpath)
    c = sqlite3.connect(os.path.join(dirpath, "orchestra-registry.db"))
    for n in (52, 53, 79):
        c.execute("INSERT INTO generations(root, generation, retired_at) VALUES ('orchestra-builder', ?, NULL)", (n,))
    ob = c.execute("SELECT id FROM generations WHERE root='orchestra-builder' AND generation=79").fetchone()[0]
    c.execute("INSERT INTO canonical VALUES ('orchestra-builder', ?, 'orchestra-builder', 'online')", (ob,))
    for n in (97, 98):
        c.execute("INSERT INTO generations(root, generation) VALUES ('gm', ?)", (n,))
    g98 = c.execute("SELECT id FROM generations WHERE root='gm' AND generation=98").fetchone()[0]
    c.execute("INSERT INTO canonical VALUES ('gm', ?, 'gm', 'online')", (g98,))
    c.execute("INSERT INTO generations(root, generation) VALUES ('oss-arturo-dev', 4)")   # no canonical: retired
    c.commit(); c.close()


def test_gen_suffix_resolves_by_evidence(tmp_path):
    _registry_gm(str(tmp_path))
    reg = os.path.join(str(tmp_path), "orchestra-registry.db")
    assert SI.resolve(reg, "orchestra-builder-gen53") == ("orchestra-builder", 53)
    assert SI.resolve(reg, "orchestra-builder-gen99") == ("orchestra-builder-gen99", None)
    assert SI.live_root(reg, "orchestra-builder-gen53") == "orchestra-builder"
    assert SI.live_root(reg, "oss-arturo-dev-g4") is None          # lineage exists, no live canonical


@pytest.fixture
def gstore(tmp_path):
    _registry_gm(str(tmp_path))
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate()
    return s


@pytest.mark.parametrize("gm_caller", ["gm", "gm-g98"])
@pytest.mark.parametrize("orphan", ["oss-arturo-dev-g4", "retired-seat", "council"])
def test_gm_annotates_an_orphan_card_as_steward(gstore, gm_caller, orphan):
    rid = gstore.create(from_agent=orphan, question="old ask", worker_kind="pane")
    ok, why = gstore.append_note(rid, gm_caller, "OBSOLETE: superseded")
    assert ok, why
    assert "orphan steward" in gstore.get(rid)["summary"]


def test_gm_is_still_refused_on_a_card_whose_author_has_a_live_seat(gstore):
    rid = gstore.create(from_agent="orchestra-builder-gen53", question="mine", worker_kind="pane")
    ok, why = gstore.append_note(rid, "gm", "nope")
    assert not ok
    assert gstore.append_note(rid, "orchestra-builder", "OBSOLETE: mine")[0]   # its live successor may


def test_a_stale_gm_generation_is_not_a_steward(gstore):
    rid = gstore.create(from_agent="retired-seat", question="old", worker_kind="pane")
    assert not gstore.append_note(rid, "gm-g97", "x")[0]


def test_a_non_gm_seat_is_not_a_steward(gstore):
    rid = gstore.create(from_agent="retired-seat", question="old", worker_kind="pane")
    assert not gstore.append_note(rid, "orchestra-builder", "x")[0]


# ---- the CLI verb: `approval.py note --id --from --text` ----------------------------------------

def _note_cli(tmp_path, *args):
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, APPROVAL_DB_PATH=str(tmp_path / "tasks.db"),
               NTFY_TOKEN_FILE="/nonexistent/ntfy-token-for-tests",
               ORCHESTRA_DIR=str(tmp_path / "orch"),
               OUTWARD_LEDGER_PATH=str(tmp_path / "outward-actions.db"),
               WATCH_GATEWAY_URL="http://127.0.0.1:9")
    env.pop("TMUX_PANE", None)
    return subprocess.run([sys.executable, os.path.join(here, "approval.py"), "note", *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_cli_note_appends_on_an_approval_card_and_never_touches_status(store, tmp_path):
    rid = _card(store)
    r = _note_cli(tmp_path, "--id", rid, "--from", "ios-watch-dev", "--text", "superseded by build 257")
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    row = store.get(rid)
    assert row["status"] == "pending" and "NOTE" in row["summary"] and "build 257" in row["summary"]
    assert row["notified_at"] is None


def test_cli_note_refuses_a_stranger_with_exit_4(store, tmp_path):
    rid = _card(store)
    r = _note_cli(tmp_path, "--id", rid, "--from", "ios-watchdog", "--text", "nope")
    assert r.returncode == 4 and "REFUSED" in r.stderr and "not the author" in r.stderr
    assert "nope" not in (store.get(rid)["summary"] or "")


def test_cli_note_on_a_questionnaire(tmp_path):
    _registry(str(tmp_path))
    q = QuestionnaireStore(db_path=str(tmp_path / "tasks.db")); q.migrate()
    qid = q.create(from_agent="ios-watch-dev-g18", title="T", questions=[{"prompt": "p", "kind": "free_text"}])
    r = _note_cli(tmp_path, "--id", qid, "--from", "ios-watch-dev-g21", "--text", "folded into the new plan")
    assert r.returncode == 0 and r.stdout.strip() == qid, r.stderr
    got = q.get(qid)
    assert got["status"] == "pending" and "folded into the new plan" in got["summary"]


def test_note_on_an_unknown_questionnaire_in_a_fresh_store_says_so(tmp_path):
    q = QuestionnaireStore(db_path=str(tmp_path / "tasks.db"))
    ok, why = q.append_note("qnr_nope", "gm", "x")
    assert not ok and "qnr_nope" in why
