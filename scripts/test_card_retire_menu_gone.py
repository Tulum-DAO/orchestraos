"""Retire a kind='menu' card whose menu is GONE; re-open a card retired in error.

retire used to refuse every kind='menu' row ("menu-bridge owns it"), which stranded five gm cards from
September: CLI-minted menus, no bridge origin, posed by gm generations retired months ago. Now a menu
card may be retired through the normal path once its menu is gone, on POSITIVE evidence only:
- BRIDGE row: owner session not in tmux, or pane up with no menu AND the posing generation retired.
  A menu still on screen, or an unreadable pane, still refuses.
- CLI-minted row: the posing generation retired (named `<root>-gN` with retired_at, or a bare-root
  author whose current canonical generation was promoted after the card was created).
A LIVE menu card (current generation, menu on screen) must still refuse.

Hermetic: tmp tasks.db + registry beside it; tmp HOME; ntfy fenced; a fake tmux first on PATH for the CLI.
"""
import os, sqlite3, stat, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
from approval_schema import ApprovalStore, RETIRE_MIGRATION

HERE = os.path.dirname(os.path.abspath(__file__))
NO_NTFY = "/nonexistent/ntfy-token-for-tests"
PROMOTED = "2026-11-01T00:00:00+00:00"          # every current generation's promotion
BEFORE, AFTER = "2026-09-18T15:24:19.140218+00:00", "2026-11-20T10:00:00+00:00"
REG_DDL = """
CREATE TABLE lineages (root TEXT PRIMARY KEY);
CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, generation INTEGER NOT NULL,
  promoted_at TEXT, retired_at TEXT, UNIQUE(root, generation));
CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER NOT NULL, tmux_session TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online');
"""


def _registry(d, ddl=REG_DDL):
    """builder: g5 retired, g6 canonical. gm: g101 retired, g102 canonical (promoted PROMOTED)."""
    c = sqlite3.connect(os.path.join(d, "orchestra-registry.db"))
    c.executescript(ddl)
    has_promoted = "promoted_at" in ddl
    for root, gens, cur in (("builder", (5, 6), 6), ("gm", (101, 102), 102)):
        for n in gens:
            cols, vals = ["root", "generation", "retired_at"], [root, n, None if n == cur else "2026-10-31T00:00:00Z"]
            if has_promoted:
                cols.append("promoted_at"); vals.append(PROMOTED if n == cur else "2026-09-01T00:00:00Z")
            gid = c.execute(f"INSERT INTO generations({','.join(cols)}) VALUES ({','.join('?' * len(vals))})",
                            vals).lastrowid
            if n == cur:
                c.execute("INSERT INTO canonical VALUES (?,?,?, 'online')", (root, gid, root))
    c.commit(); c.close()


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    h = tmp_path / "home"; (h / "runtime").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("NTFY_TOKEN_FILE", NO_NTFY)
    monkeypatch.setenv("APPROVAL_DDL_ARMED", RETIRE_MIGRATION)
    import approval_notify
    def boom(*a, **k):
        raise AssertionError("retire must not notify")
    monkeypatch.setattr(approval_notify, "notify", boom)
    return h


@pytest.fixture
def store(tmp_path):
    _registry(str(tmp_path))
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate(); return s


def _set_created(store, rid, ts):
    c = sqlite3.connect(store.db_path)
    c.execute("UPDATE approval_requests SET created_at=? WHERE id=?", [ts, rid]); c.commit(); c.close()


def _cli_menu(store, author, created=BEFORE):
    rid = store.create(from_agent=author, question="Take the sites down?", worker_kind="pane", kind="menu",
                       menu={"question": "Take the sites down?", "options": []}, options=["A", "B"])
    _set_created(store, rid, created)
    return rid


def _bridge_menu(store, author="builder-g5", session="builder", created=BEFORE):
    q = "Close :7373 now?"
    rid = store.create(from_agent=author, question=q, worker_kind="pane", kind="menu",
                       menu={"question": q, "options": []}, options=["A"],
                       op_key=f"menu:{session}:9f7a7ac91937fe45", origin="menu_bridge")
    _set_created(store, rid, created)
    return rid


def _row(store, rid):
    c = sqlite3.connect(store.db_path); c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT * FROM approval_requests WHERE id=?", [rid]).fetchone())
    finally:
        c.close()


class Pane:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, session):
        self.calls.append(session)
        return self.answer


def _assert_retired(store, rid, reason, basis_bit):
    r = _row(store, rid)
    assert r["status"] == "retired", r
    assert r["retire_reason"] == reason and r["retired_by"] == "gm"
    assert "menu gone, pane retired" in r["summary"] and basis_bit in r["summary"]


def _assert_pending(store, rid):
    r = _row(store, rid)
    assert r["status"] == "pending" and r["retired_at"] is None and "RETIRED" not in (r["summary"] or "")


# ------------------------------------------------------------------ CLI-minted menu cards
def test_bare_root_card_posed_before_the_current_generation_retires(store):
    """The five Sept gm cards: author 'gm', created before gm's current generation was promoted."""
    rid = _cli_menu(store, "gm")
    reason = "stale since 09-25, no client report in two months."
    ok, why = store.retire(rid, "gm", reason)
    assert ok, why
    _assert_retired(store, rid, reason, "earlier gm generation")


def test_bare_root_card_posed_by_the_current_generation_refuses(store):
    rid = _cli_menu(store, "gm", created=AFTER)
    ok, why = store.retire(rid, "gm", "x")
    assert not ok and "still live" in why and "menu-bridge owns it" in why
    _assert_pending(store, rid)


def test_named_retired_generation_card_retires(store):
    rid = _cli_menu(store, "builder-g5", created=AFTER)
    ok, why = store.retire(rid, "builder", "obsolete")
    assert ok, why
    r = _row(store, rid)
    assert r["status"] == "retired" and "builder-g5 retired" in r["summary"]


def test_named_current_generation_card_refuses(store):
    rid = _cli_menu(store, "builder-g6", created=BEFORE)     # age alone never proves it gone
    ok, why = store.retire(rid, "gm", "x")
    assert not ok and "not retired" in why
    _assert_pending(store, rid)


def test_superseded_by_is_kept_on_a_menu_retire(store):
    other = _cli_menu(store, "gm", created=AFTER)
    rid = _cli_menu(store, "gm")
    ok, why = store.retire(rid, "gm", "folded behind it", superseded_by=other)
    assert ok, why
    r = _row(store, rid)
    assert r["superseded_by"] == other and f"(superseded by {other}) [menu gone" in r["summary"]


def test_cli_menu_card_never_reads_the_pane(store):
    rid = _cli_menu(store, "gm")
    pane = Pane("menu")
    ok, why = store.retire(rid, "gm", "x", read_pane=pane)
    assert ok, why
    assert pane.calls == []


def test_registry_without_promoted_at_refuses(tmp_path):
    _registry(str(tmp_path), REG_DDL.replace("promoted_at TEXT, ", ""))
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate()
    rid = _cli_menu(s, "gm")
    ok, why = s.retire(rid, "gm", "x")
    assert not ok and "registry unreadable" in why
    _assert_pending(s, rid)


def test_who_may_retire_is_unchanged_for_menu_cards(store):
    rid = _cli_menu(store, "gm")
    ok, why = store.retire(rid, "builder", "x")
    assert not ok and "may not retire" in why
    _assert_pending(store, rid)


# ------------------------------------------------------------------ bridge menu cards
def test_bridge_menu_still_on_screen_refuses_even_from_a_retired_generation(store):
    rid = _bridge_menu(store, "builder-g5")
    ok, why = store.retire(rid, "gm", "x", read_pane=Pane("menu"))
    assert not ok and "menu still on screen in builder" in why
    _assert_pending(store, rid)


def test_bridge_session_gone_retires(store):
    rid = _bridge_menu(store, "builder-g6")
    ok, why = store.retire(rid, "gm", "pane gone", read_pane=Pane("no-session"))
    assert ok, why
    _assert_retired(store, rid, "pane gone", "builder not in tmux")


def test_bridge_pane_up_no_menu_needs_the_posing_generation_retired(store):
    live = _bridge_menu(store, "builder-g6")
    ok, why = store.retire(live, "gm", "x", read_pane=Pane("none"))
    assert not ok
    _assert_pending(store, live)
    old = _bridge_menu(store, "builder-g5")
    ok, why = store.retire(old, "gm", "x", read_pane=Pane("none"))
    assert ok, why
    assert "no menu on screen in builder; builder-g5 retired" in _row(store, old)["summary"]


@pytest.mark.parametrize("seen", ["error:capture rc=1", "", None])
def test_bridge_unreadable_pane_refuses(store, seen):
    rid = _bridge_menu(store, "builder-g5")
    ok, why = store.retire(rid, "gm", "x", read_pane=Pane(seen))
    assert not ok and "could not read" in why
    _assert_pending(store, rid)


def test_bridge_without_a_pane_reader_refuses(store):
    rid = _bridge_menu(store, "builder-g5")
    ok, why = store.retire(rid, "gm", "x")
    assert not ok and "no pane reader" in why
    _assert_pending(store, rid)


# ------------------------------------------------------------------ the real CLI
def _fake_tmux(tmp_path, has_session_rc):
    d = tmp_path / "bin"; d.mkdir(exist_ok=True)
    log = tmp_path / "tmux.calls"
    t = d / "tmux"
    # a missing session answers the way real tmux does: exit 1 AND "can't find session" on stderr
    miss = "echo \"can't find session: x\" >&2; " if has_session_rc else ""
    t.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\ncase \"$1\" in has-session) {miss}exit {has_session_rc};; esac\n"
                 f"exit 1\n")
    t.chmod(t.stat().st_mode | stat.S_IEXEC)
    return str(d), log


def _cli(store, tmp_path, bindir, *args):
    env = {**os.environ, "APPROVAL_DB_PATH": store.db_path, "PATH": bindir + os.pathsep + os.environ["PATH"],
           "HOME": os.environ["HOME"], "NTFY_TOKEN_FILE": NO_NTFY, "APPROVAL_DDL_ARMED": RETIRE_MIGRATION,
           "WATCH_GATEWAY_URL": "http://127.0.0.1:9", "ORCHESTRA_DIR": str(tmp_path / "orch"),
           "OUTWARD_LEDGER_PATH": str(tmp_path / "outward-actions.db")}
    env.pop("TMUX_PANE", None)
    env.pop("TMUX", None)
    return subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_cli_retires_a_gone_bridge_menu_and_never_sends_keys(store, tmp_path):
    bindir, log = _fake_tmux(tmp_path, has_session_rc=1)
    rid = _bridge_menu(store, "builder-g6")
    r = _cli(store, tmp_path, bindir, "retire", "--id", rid, "--from", "gm", "--reason", "pane gone")
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    assert _row(store, rid)["status"] == "retired"
    calls = log.read_text() if log.exists() else ""
    assert "send-keys" not in calls and "paste-buffer" not in calls


def test_cli_refuses_a_live_cli_menu(store, tmp_path):
    bindir, _ = _fake_tmux(tmp_path, has_session_rc=0)
    rid = _cli_menu(store, "gm", created=AFTER)
    r = _cli(store, tmp_path, bindir, "retire", "--id", rid, "--from", "gm", "--reason", "x")
    assert r.returncode == 4 and "still live" in r.stderr
    _assert_pending(store, rid)


# ------------------------------------------------------------------ dead seats
# A seat with NO canonical row and NO tmux session under any of its names cannot show a menu anywhere:
# "unprovable" resolves to gone. A live seat's card must still refuse.
class Sessions:
    def __init__(self, live=(), unknown=()):
        self.live, self.unknown, self.calls = set(live), set(unknown), []

    def __call__(self, name):
        self.calls.append(name)
        return None if name in self.unknown else (name in self.live)


def test_dead_seat_cli_menu_retires(store):
    rid = _cli_menu(store, "rotation-council")
    s = Sessions()
    ok, why = store.retire(rid, "gm", "stale: posing seat retired, menu gone", has_session=s)
    assert ok, why
    _assert_retired(store, rid, "stale: posing seat retired, menu gone", "posing seat rotation-council is gone")
    assert "rotation-council" in s.calls


def test_dead_seat_bridge_menu_with_blank_pane_retires(store):
    rid = _bridge_menu(store, author="wifi-mesh-help", session="wifi-mesh-help")
    ok, why = store.retire(rid, "gm", "stale", read_pane=Pane("none"), has_session=Sessions())
    assert ok, why
    _assert_retired(store, rid, "stale", "posing seat wifi-mesh-help is gone")


def test_unregistered_generation_name_checks_its_root(store):
    """'oldseat-g3' is in no registry: the root 'oldseat' and every name are probed."""
    rid = _cli_menu(store, "oldseat-g3")
    s = Sessions()
    ok, why = store.retire(rid, "gm", "stale", has_session=s)
    assert ok, why
    assert {"oldseat", "oldseat-g3"} <= set(s.calls)


def test_live_seat_card_still_refuses_even_with_no_sessions(store):
    """Sabotage guard: gm has a canonical row, so a card its current generation posed stays live."""
    rid = _cli_menu(store, "gm", created=AFTER)
    ok, why = store.retire(rid, "gm", "x", has_session=Sessions())
    assert not ok and "still live" in why
    _assert_pending(store, rid)


def test_dead_seat_with_a_session_under_a_generation_name_refuses(store):
    rid = _cli_menu(store, "ghost")
    ok, why = store.retire(rid, "gm", "x", has_session=Sessions(live={"ghost"}))
    assert not ok and "still live" in why
    _assert_pending(store, rid)


def test_dead_seat_unknown_tmux_answer_refuses(store):
    rid = _cli_menu(store, "ghost")
    ok, why = store.retire(rid, "gm", "x", has_session=Sessions(unknown={"ghost"}))
    assert not ok and "still live" in why
    _assert_pending(store, rid)


def test_dead_seat_rule_is_off_without_a_session_probe(store):
    rid = _cli_menu(store, "ghost")
    ok, why = store.retire(rid, "gm", "x")
    assert not ok and "still live" in why
    _assert_pending(store, rid)


def _parked_store(tmp_path):
    _registry(str(tmp_path))
    c = sqlite3.connect(str(tmp_path / "orchestra-registry.db"))
    gid = c.execute("INSERT INTO generations(root, generation, promoted_at) VALUES ('napper', 1, ?)",
                    [AFTER]).lastrowid
    c.execute("INSERT INTO canonical VALUES ('napper', ?, 'napper', 'parked')", [gid]); c.commit(); c.close()
    s = ApprovalStore(db_path=str(tmp_path / "tasks.db")); s.migrate()
    return s


def test_parked_seat_menu_without_tmux_retires(tmp_path):
    """A menu is pane state; a parked seat with no pane anywhere cannot show it."""
    s = _parked_store(tmp_path)
    rid = _cli_menu(s, "napper", created="2026-11-21T00:00:00+00:00")
    ok, why = s.retire(rid, "gm", "stale", has_session=Sessions())
    assert ok, why
    _assert_retired(s, rid, "stale", "canonical parked")


def test_parked_seat_menu_with_a_session_refuses(tmp_path):
    s = _parked_store(tmp_path)
    rid = _cli_menu(s, "napper", created="2026-11-21T00:00:00+00:00")
    ok, why = s.retire(rid, "gm", "x", has_session=Sessions(live={"napper"}))
    assert not ok and "still live" in why
    _assert_pending(s, rid)


def _fake_tmux_no_session(tmp_path):
    d = tmp_path / "bin2"; d.mkdir(exist_ok=True)
    t = d / "tmux"
    t.write_text("#!/bin/sh\ncase \"$1\" in has-session) echo \"can't find session: ${3#=}\" >&2; exit 1;; esac\n"
                 "exit 1\n")
    t.chmod(t.stat().st_mode | stat.S_IEXEC)
    return str(d)


def test_cli_retires_a_dead_seat_menu(store, tmp_path):
    rid = _cli_menu(store, "lessons-historian")
    r = _cli(store, tmp_path, _fake_tmux_no_session(tmp_path), "retire", "--id", rid, "--from", "gm",
             "--reason", "stale: posing seat retired, menu gone")
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    assert _row(store, rid)["status"] == "retired"


def test_cli_refuses_a_live_seat_menu_when_tmux_says_no_session(store, tmp_path):
    rid = _cli_menu(store, "gm", created=AFTER)
    r = _cli(store, tmp_path, _fake_tmux_no_session(tmp_path), "retire", "--id", rid, "--from", "gm",
             "--reason", "x")
    assert r.returncode == 4 and "still live" in r.stderr
    _assert_pending(store, rid)


# ------------------------------------------------------------------ re-open
def _approval(store, author="builder"):
    rid = store.create(from_agent=author, question="Ship it?", worker_kind="pane", options=["Yes", "No"])
    _set_created(store, rid, BEFORE)
    return rid


def test_reopen_restores_a_retired_card_with_both_history_lines(store):
    rid = _approval(store)
    ok, why = store.retire(rid, "gm", "stale sweep")
    assert ok, why
    notified = _row(store, rid)["notified_at"]
    ok, why = store.reopen(rid, "gm", "retired in error by the 11-25 sweep; re-opened")
    assert ok, why
    r = _row(store, rid)
    assert r["status"] == "pending"
    assert r["retired_at"] is None and r["retired_by"] is None and r["retire_reason"] is None
    assert "RETIRED" in r["summary"] and "RE-OPENED" in r["summary"]
    assert r["summary"].count("RE-OPENED") == 1 and "retired in error by the 11-25 sweep" in r["summary"]
    assert r["notified_at"] == notified


def test_reopen_is_gm_only(store):
    rid = _approval(store)
    store.retire(rid, "gm", "stale sweep")
    ok, why = store.reopen(rid, "builder", "x")
    assert not ok and "only gm" in why
    assert _row(store, rid)["status"] == "retired"


def test_reopen_refuses_a_card_that_is_not_retired(store):
    rid = _approval(store)
    ok, why = store.reopen(rid, "gm", "x")
    assert not ok and "not retired" in why
    _assert_pending(store, rid)


def test_cli_reopen(store, tmp_path):
    bindir, _ = _fake_tmux(tmp_path, has_session_rc=0)
    rid = _approval(store)
    store.retire(rid, "gm", "stale sweep")
    r = _cli(store, tmp_path, bindir, "reopen", "--id", rid, "--from", "gm", "--reason", "retired in error")
    assert r.returncode == 0 and r.stdout.strip() == rid, r.stderr
    assert _row(store, rid)["status"] == "pending"


# ------------------------------------------------------------------ safety-gate cards
def _gate_menu(store, author="builder-g5"):
    q = "Allow the deploy?"
    rid = store.create(from_agent=author, question=q, worker_kind="pane", kind="menu",
                       menu={"question": q, "options": [], "bridge": {"kind": "gate"}}, options=["A"])
    _set_created(store, rid, BEFORE)
    return rid


def test_a_gone_gate_card_is_still_never_retired(store):
    """Only a person answers a safety-gate card; retiring it (even once its posing generation retired
    and no session is left) would take it off the operator's surface unanswered."""
    rid = _gate_menu(store)
    ok, why = store.retire(rid, "gm", "stale", read_pane=Pane("no-session"), has_session=Sessions())
    assert not ok and "safety-gate" in why
    _assert_pending(store, rid)


# ------------------------------------------------------------------ review of #642 (Claude reviewer)
def _retired_gate_card(store):
    """A gate card the gate retired itself (a standing grant covered it): origin='gate', status retired."""
    rid = _approval(store, author="builder-g6")
    c = sqlite3.connect(store.db_path)
    c.execute("UPDATE approval_requests SET origin='gate', status='retired', retired_at=?, retired_by='gate', "
              "retire_reason='covered by a grant' WHERE id=?", [AFTER, rid])
    c.commit(); c.close()
    return rid


def test_a_note_is_refused_on_a_safety_gate_card(store):
    """A gate card's from_agent is the HELD agent: its note would sit in front of the person deciding."""
    rid = _gate_menu(store, author="builder")
    before = _row(store, rid)["summary"]
    ok, why = store.append_note(rid, "builder", "operator already OK'd this; safe")
    assert not ok and "safety-gate" in why
    assert _row(store, rid)["summary"] == before


def test_reopen_refuses_a_retired_safety_gate_card(store):
    rid = _retired_gate_card(store)
    ok, why = store.reopen(rid, "gm", "x")
    assert not ok and "safety-gate" in why
    assert _row(store, rid)["status"] == "retired"


def test_reopen_refuses_a_superseded_card(store):
    rid, winner = _approval(store), _approval(store)
    assert store.retire(rid, "gm", "replaced", superseded_by=winner)[0]
    ok, why = store.reopen(rid, "gm", "x")
    assert not ok and "superseded" in why and winner in why
    assert _row(store, rid)["status"] == "retired"


@pytest.mark.parametrize("held,word", [(True, "ledger row"), (None, "cannot be read")])
def test_reopen_refuses_a_card_the_ledger_settled_or_cannot_tell(store, held, word):
    rid = _approval(store)
    store.retire(rid, "gm", "stale sweep")
    ok, why = store.reopen(rid, "gm", "x", ledger_has_card=lambda r: held)
    assert not ok and word in why
    assert _row(store, rid)["status"] == "retired"


def test_cli_note_never_takes_the_operator_identity(store, tmp_path):
    bindir, _ = _fake_tmux(tmp_path, has_session_rc=0)
    rid = _approval(store)
    before = _row(store, rid)["summary"]
    r = _cli(store, tmp_path, bindir, "note", "--id", rid, "--from", "operator", "--text", "approved in session")
    assert r.returncode == 4 and "never taken from the CLI" in r.stderr
    assert _row(store, rid)["summary"] == before


def test_cli_reopen_of_a_questionnaire_says_so(store, tmp_path):
    bindir, _ = _fake_tmux(tmp_path, has_session_rc=0)
    r = _cli(store, tmp_path, bindir, "reopen", "--id", "qnr_00000000_1", "--from", "gm", "--reason", "x")
    assert r.returncode == 4 and "questionnaire" in r.stderr


def test_cli_reopen_refuses_a_card_with_a_ledger_row(store, tmp_path):
    bindir, _ = _fake_tmux(tmp_path, has_session_rc=0)
    rid = _approval(store)
    store.retire(rid, "gm", "stale sweep")
    import outward_ledger
    led = sqlite3.connect(str(tmp_path / "outward-actions.db"))
    led.executescript(outward_ledger.DDL)               # the ledger's own schema
    led.execute("INSERT INTO outward_actions (id, action_hash, action, material_args, card_id, state, created_at, "
                "updated_at) VALUES ('oa_1', 'h', 'telegram', '{}', ?, 'not_run', ?, ?)", [rid, AFTER, AFTER])
    led.commit(); led.close()
    r = _cli(store, tmp_path, bindir, "reopen", "--id", rid, "--from", "gm", "--reason", "x")
    assert r.returncode == 4 and "ledger row" in r.stderr, r.stderr
    assert _row(store, rid)["status"] == "retired"


def test_read_pane_menu_calls_only_tmuxs_own_answer_no_session(tmp_path, monkeypatch):
    """A has-session failure that is not tmux's "no such session" (permission denied, wrong socket) is
    could-not-tell, never no-session: no-session lets close-menu and a bridge retire go ahead."""
    import approval
    d = tmp_path / "b"; d.mkdir()
    t = d / "tmux"
    t.write_text("#!/bin/sh\necho 'error connecting to /tmp/tmux-1/default (Permission denied)' >&2\nexit 1\n")
    t.chmod(t.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    assert approval.read_pane_menu("builder").startswith("error:")
    t.write_text("#!/bin/sh\necho \"can't find session: builder\" >&2\nexit 1\n")
    assert approval.read_pane_menu("builder") == "no-session"


def test_a_note_keeps_a_line_written_after_it_was_read(store):
    """append_note appends in one statement: a line that lands between its read and its write survives."""
    rid = _approval(store)
    import approval_schema
    real = approval_schema.ApprovalStore._conn

    def racing_conn(self):
        c = real(self)
        orig = c.execute

        class C:
            def __getattr__(self, k):
                return getattr(c, k)

            def execute(self, sql, *a):
                if sql.startswith("UPDATE approval_requests SET summary"):
                    orig("UPDATE approval_requests SET summary=COALESCE(summary, question, '') || ? WHERE id=?",
                         ["\n\n— RETIRED by gm: raced", rid])
                return orig(sql, *a)
        return C()
    approval_schema.ApprovalStore._conn = racing_conn
    try:
        ok, why = store.append_note(rid, "builder", "a correction")
    finally:
        approval_schema.ApprovalStore._conn = real
    assert ok, why
    s = _row(store, rid)["summary"]
    assert "raced" in s and "a correction" in s
