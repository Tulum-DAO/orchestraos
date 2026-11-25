"""approval.py close-menu.

A PENDING kind='menu' card answered another way -> 'resolved_elsewhere' with who/why.
- BRIDGE rows: the owner pane is READ; a menu still on screen (or a failed read) refuses.
- CLI-minted rows (no bridge origin): no pane read at all; --reason must state the basis.
- Only the author, its current generation, or gm.
- NEVER sends keys or injects, in any branch: pinned through the REAL CLI with a fake `tmux`
  first on PATH that records every call, and with the gateway unreachable.
Hermetic: tmp tasks.db + registry; tmp HOME; ntfy token fenced.
"""
import json, os, sqlite3, stat, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
import approval_schema
from approval_schema import ApprovalStore

HERE = os.path.dirname(os.path.abspath(__file__))
NO_NTFY = "/nonexistent/ntfy-token-for-tests"
REG_DDL = """
CREATE TABLE lineages (root TEXT PRIMARY KEY);
CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, generation INTEGER NOT NULL,
  retired_at TEXT, UNIQUE(root, generation));
CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER NOT NULL, tmux_session TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'online');
"""
# A real Claude Code AskUserQuestion screen (menu replaces the composer) vs an idle screen.
MENU_SCREEN = "\n".join([
    "", "  Close :7373 now?", "",
    "❯ 1. Tailnet-only now", "     Turn Funnel off for :7373 and keep tailnet access",
    "  2. Leave it public", "     Keep the Funnel as it is",
    "  3. Type something.", "", "Enter to select · ↑/↓ to navigate · Esc to cancel", ""])
IDLE_SCREEN = "\n".join(["● done.", "", "─" * 40, "> ", "─" * 40, "  ? for shortcuts", ""])


def _registry(d):
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
    monkeypatch.setenv("NTFY_TOKEN_FILE", NO_NTFY)
    return h


@pytest.fixture
def db(tmp_path):
    _registry(str(tmp_path))
    return str(tmp_path / "tasks.db")


@pytest.fixture
def store(db, home):
    assert not os.path.exists(NO_NTFY)
    s = ApprovalStore(db_path=db); s.migrate(); return s


def _bridge_card(store, session="builder", key="feedfacefeedface"):
    op = f"menu:{session}:{key}"
    q = "Close :7373 now?"
    return store.create(from_agent=session, question=q, worker_kind="pane", kind="menu",
                        menu={"question": q, "options": []}, options=["A"], op_key=op,
                        origin="menu_bridge"), op


def _cli_card(store, author="builder"):
    return store.create(from_agent=author, question="Pixel fix?", worker_kind="pane",
                        kind="menu", menu={"question": "Pixel fix?", "options": []},
                        options=["A", "B"])


def _row(store, rid):
    c = sqlite3.connect(store.db_path); c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT status, summary FROM approval_requests WHERE id=?", [rid]).fetchone())
    finally:
        c.close()


class Pane:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, session):
        self.calls.append(session)
        return self.answer


# ------------------------------------------------------------------ bridge rows
def test_bridge_row_no_menu_on_screen_closes_as_answered_in_pane(store):
    rid, op = _bridge_card(store)
    c = sqlite3.connect(store.db_path)
    c.execute("CREATE TABLE IF NOT EXISTS menu_bridge_capheld (op_key TEXT PRIMARY KEY, held_at REAL NOT NULL)")
    c.execute("INSERT INTO menu_bridge_capheld VALUES (?, 1.0)", [op]); c.commit(); c.close()
    pane = Pane("none")
    ok, why, note = store.close_menu(rid, "gm", "", pane)
    assert ok, why
    assert pane.calls == ["builder"]
    r = _row(store, rid)
    assert r["status"] == "resolved_elsewhere"
    assert "CLOSED" in r["summary"] and "answered in pane (no menu on screen" in r["summary"]
    c = sqlite3.connect(store.db_path)
    assert c.execute("SELECT COUNT(*) FROM menu_bridge_capheld").fetchone()[0] == 0


def test_bridge_row_menu_still_on_screen_refuses(store):
    rid, _ = _bridge_card(store)
    ok, why, _ = store.close_menu(rid, "gm", "", Pane("menu"))
    assert not ok and "still on screen" in why
    assert _row(store, rid)["status"] == "pending"


@pytest.mark.parametrize("seen", ["error:capture rc=1", "error:TimeoutExpired", "", None])
def test_bridge_row_unreadable_pane_refuses(store, seen):
    rid, _ = _bridge_card(store)
    ok, why, _ = store.close_menu(rid, "gm", "", Pane(seen))
    assert not ok and "could not read" in why
    assert _row(store, rid)["status"] == "pending"


def test_bridge_row_session_gone_closes(store):
    rid, _ = _bridge_card(store)
    ok, _, note = store.close_menu(rid, "gm", "usage-limit menu", Pane("no-session"))
    assert ok and "not in tmux" in note and "usage-limit menu" in note


# ------------------------------------------------------------------ CLI-minted rows
def test_cli_row_requires_reason_and_never_reads_the_pane(store):
    rid = _cli_card(store)
    pane = Pane("menu")                      # even a menu on screen is irrelevant here
    ok, why, _ = store.close_menu(rid, "gm", "  ", pane)
    assert not ok and "--reason" in why
    ok, why, note = store.close_menu(rid, "gm", "obsolete: the operator ruled 2026-10-13 the pixel stays", pane)
    assert ok, why
    assert pane.calls == []
    assert "obsolete: the operator ruled 2026-10-13 the pixel stays" in _row(store, rid)["summary"]
    assert "no pane read" in note


# ------------------------------------------------------------------ who / what
def test_only_author_lineage_or_gm(store):
    rid, _ = _bridge_card(store)
    ok, why, _ = store.close_menu(rid, "other-seat", "", Pane("none"))
    assert not ok and _row(store, rid)["status"] == "pending"
    ok, _, _ = store.close_menu(rid, "builder", "", Pane("none"))
    assert ok


def test_refuses_non_menu_and_non_pending(store):
    rid = store.create(from_agent="builder", question="plain?", worker_kind="pane", options=["yes", "no"])
    ok, why, _ = store.close_menu(rid, "gm", "x", Pane("none"))
    assert not ok and "use retire" in why
    mrid, _ = _bridge_card(store, key="aaaa")
    assert store.close_menu(mrid, "gm", "", Pane("none"))[0]
    ok, why, _ = store.close_menu(mrid, "gm", "", Pane("none"))
    assert not ok and "not pending" in why


def test_a_safety_gate_card_is_never_closed(store):
    """Only a person answers a permission card / grant proposal; close-menu must not end one."""
    q = "Allow the deploy?"
    rid = store.create(from_agent="builder", question=q, worker_kind="pane", kind="menu",
                       menu={"question": q, "options": [], "bridge": {"kind": "gate"}}, options=["A"])
    pane = Pane("none")
    ok, why, _ = store.close_menu(rid, "gm", "answered in session", pane)
    assert not ok and "safety-gate" in why
    assert pane.calls == [] and _row(store, rid)["status"] == "pending"


# ------------------------------------------------------------------ the deployed CLI never sends keys
def _fake_tmux(bin_dir, log, screen, has_session=True):
    script = bin_dir / "tmux"
    script.write_text(f"""#!/usr/bin/env python3
import sys, json
open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'has-session':
    sys.exit(0 if {has_session!r} else 1)
if sys.argv[1] == 'capture-pane':
    sys.stdout.write({screen!r}); sys.exit(0)
sys.exit(97)   # anything else (send-keys, respawn, kill...) is a failure AND is logged
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _cli(db, home, bin_dir, *args):
    tmp = os.path.dirname(db)
    env = dict(os.environ, APPROVAL_DB_PATH=db, NTFY_TOKEN_FILE=NO_NTFY, HOME=str(home),
               ORCHESTRA_DIR=os.path.join(tmp, "orch"),
               OUTWARD_LEDGER_PATH=os.path.join(tmp, "outward-actions.db"),
               PATH=f"{bin_dir}:{os.environ['PATH']}",
               WATCH_GATEWAY_URL="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9",
               https_proxy="http://127.0.0.1:9")
    return subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("screen,expect_rc", [(IDLE_SCREEN, 0), (MENU_SCREEN, 4)])
def test_cli_close_menu_only_reads_the_pane(store, db, home, tmp_path, screen, expect_rc):
    rid, _ = _bridge_card(store)
    b = tmp_path / "bin"; b.mkdir(); log = tmp_path / "tmux.log"
    _fake_tmux(b, log, screen)
    r = _cli(db, home, b, "close-menu", "--id", rid, "--from", "gm")
    assert r.returncode == expect_rc, (r.stdout, r.stderr)
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    assert calls and {c[0] for c in calls} <= {"has-session", "capture-pane"}, calls
    assert all(c[c.index("-t") + 1].startswith("=builder") for c in calls)      # exact target
    assert _row(store, rid)["status"] == ("resolved_elsewhere" if expect_rc == 0 else "pending")


def test_cli_close_menu_cli_row_touches_tmux_not_at_all(store, db, home, tmp_path):
    rid = _cli_card(store)
    b = tmp_path / "bin"; b.mkdir(); log = tmp_path / "tmux.log"
    _fake_tmux(b, log, MENU_SCREEN)
    r = _cli(db, home, b, "close-menu", "--id", rid, "--from", "gm", "--reason", "the operator ruled in session")
    assert r.returncode == 0, r.stderr
    assert not log.exists()


def test_close_menu_source_has_no_key_or_inject_path():
    import inspect
    import approval
    src = inspect.getsource(approval.read_pane_menu) + inspect.getsource(approval_schema.close_menu_card)
    for bad in ("send-keys", "send_keys", "agent-message", "verified_inject", "respawn", "kill-"):
        assert bad not in src, bad
