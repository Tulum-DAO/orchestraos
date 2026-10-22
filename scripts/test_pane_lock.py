"""ONE cross-process pane lock for everything that types into a pane (pane_lock.py), and exact tmux
targets in the gateway.

Before: the gateway's send lock was in-process only, and the message router, the nudger, boot_inject,
the grok mail nudge and the RED ALERT repairs typed with no lock at all, so two senders on one pane in
the same second could glue or split each other's text. Now each typer holds the same flock for its
whole read -> type -> Enter -> verify burst, and one that cannot get it types NOTHING and says so
('busy' / 'held' / 'pane-locked' / RC_NOT_SENT), without spending a retry.

Each "busy" test holds the real flock on its own fd (flock conflicts across fds, even in one process)
and asserts the typer types nothing; each "runs" test asserts the typing happens while the typer
holds it. PANE_LOCK_DIR is fenced per test; tmux is a stub except in the race test, which runs a real
fenced tmux server.
"""
import fcntl
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pane_lock  # noqa: E402
import grok_mail_watch as GMW  # noqa: E402
import red_alert as RA  # noqa: E402
import red_alert_watch as RAW  # noqa: E402
import nudge_pane as NP  # noqa: E402
import boot_inject as BI  # noqa: E402


@pytest.fixture(autouse=True)
def locks(tmp_path, monkeypatch):
    monkeypatch.setenv("PANE_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("RED_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RED_ALERT_LOG_DIR", str(tmp_path / "logs"))


def _held_elsewhere(session):
    """True when ANOTHER fd cannot take the lock right now (i.e. someone holds it)."""
    p = pane_lock.lock_path(session)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def _router():
    spec = importlib.util.spec_from_file_location("message_router_pl", HERE / "message-router.py")
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    return R


def _gateway():
    import watch_gateway as G
    return G


# ------------------------------------------------------------------ pane_lock itself
def test_lock_path_is_one_file_per_session_whatever_the_target_spelling():
    assert pane_lock.lock_path("gm") == pane_lock.lock_path("=gm:") == pane_lock.lock_path("=gm")
    assert pane_lock.lock_path("gm") != pane_lock.lock_path("gm-g2")
    assert "/" not in pane_lock.lock_path("../../etc/x").name


def test_a_held_lock_refuses_a_second_holder_and_frees_on_exit():
    with pane_lock.pane_lock("gm", wait_s=0):
        assert _held_elsewhere("gm")
        with pytest.raises(pane_lock.PaneBusy):
            with pane_lock.pane_lock("gm", wait_s=0.05):
                pass
    assert not _held_elsewhere("gm")


# ------------------------------------------------------------------ gateway
def test_gateway_verified_inject_types_nothing_while_another_sender_holds_the_pane(monkeypatch):
    G = _gateway()
    keys = []
    monkeypatch.setattr(G, "_tmux", lambda *a, **k: keys.append(a) or types.SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(G, "PANE_LOCK_WAIT_S", 0.1)
    monkeypatch.setattr(G, "_verified_inject_locked", lambda *a, **k: pytest.fail("ran under a held lock"))
    with pane_lock.pane_lock("gm", wait_s=0):
        ok, info = G.verified_inject("gm", "hello")
    assert ok is False and info["reason"] == "busy" and "another sender" in info["activity"]
    assert keys == []


def test_gateway_verified_inject_runs_its_whole_call_under_the_lock(monkeypatch):
    G = _gateway()
    seen = []
    monkeypatch.setattr(G, "_verified_inject_locked",
                        lambda s, t, f=False, i=False: seen.append(_held_elsewhere(s)) or (True, {"state": "idle"}))
    assert G.verified_inject("gm", "hello") == (True, {"state": "idle"})
    assert seen == [True]


def test_gateway_suggest_accept_is_409_while_another_sender_holds_the_pane(monkeypatch):
    G = _gateway()
    monkeypatch.setattr(G, "PANE_LOCK_WAIT_S", 0.1)
    monkeypatch.setattr(G, "_suggest_accept_inner", lambda *a: pytest.fail("ran under a held lock"))
    with pane_lock.pane_lock("gm", wait_s=0):
        status, body = G.suggest_accept("gm", "x")
    assert status == 409 and body["ok"] is False


def test_gateway_exact_target_never_a_prefix():
    G = _gateway()
    assert G._st("gm") == "=gm:"
    assert G._st("=gm") == "=gm:"
    assert G._st("gm:1") == "=gm:1"
    assert G._st("=gm:1.0") == "=gm:1.0"


def test_gateway_source_has_no_bare_session_target():
    """Every pane target in the gateway goes through _st (a bare -t NAME reaches NAME-gN, a green,
    in the swap gap); the one session target (list-panes) uses '=NAME'."""
    src = (HERE / "watch_gateway.py").read_text()
    assert '"-t", session' not in src
    assert src.count('"-t", _st(session)') >= 20


# ------------------------------------------------------------------ message router
def test_router_inject_is_held_and_types_nothing_while_another_sender_holds_the_pane(monkeypatch):
    R = _router()
    monkeypatch.setattr(R, "ROUTER_PANE_LOCK_WAIT_S", 0.1)
    monkeypatch.setattr(R, "_inject_unlocked", lambda *a, **k: pytest.fail("typed under a held lock"))
    with pane_lock.pane_lock("pm-x", wait_s=0):
        assert R.inject("pm-x", "hello") == "held"


def test_router_inject_runs_under_the_lock(monkeypatch):
    R = _router()
    seen = []
    monkeypatch.setattr(R, "_inject_unlocked", lambda s, t, rt=None: seen.append(_held_elsewhere(s)) or "submitted")
    assert R.inject("pm-x", "hello") == "submitted"
    assert seen == [True]


def test_router_run_budget_spent_means_no_wait_at_all(monkeypatch):
    R = _router()
    monkeypatch.setattr(R, "_RUN_STARTED", time.monotonic() - 10_000)
    monkeypatch.setattr(R, "_inject_unlocked", lambda *a, **k: pytest.fail("typed under a held lock"))
    with pane_lock.pane_lock("pm-x", wait_s=0):
        t0 = time.monotonic()
        assert R.inject("pm-x", "hello") == "held"
        assert time.monotonic() - t0 < 0.5


class _Store:
    def __init__(self):
        self.calls = []

    def claim(self, i):
        self.calls.append(("claim", i)); return True

    def defer(self, i, error=None):
        self.calls.append(("defer", i)); return True

    def fail(self, i, error=None):
        self.calls.append(("fail", i))

    def deliver(self, i):
        self.calls.append(("deliver", i))


def test_router_digest_held_defers_every_row_and_spends_no_retry(monkeypatch):
    R = _router()
    rows = [{"id": "m1"}, {"id": "m2"}]
    monkeypatch.setattr(R, "partition_for_digest", lambda rows, age: ([], rows))
    monkeypatch.setattr(R, "compose_digest", lambda claimed, store: "digest")
    st = _Store()
    n = R.deliver_stale_digest("pm-x", st, rows, {"enabled": True}, inject_fn=lambda *a: "held")
    assert n == 0
    assert [c for c in st.calls if c[0] != "claim"] == [("defer", "m1"), ("defer", "m2")]


def test_msg_store_defer_reopens_only_a_claimed_row(tmp_path):
    import sqlite3
    sys.path.insert(0, str(HERE.parent))
    import msg_store
    db = str(tmp_path / "bus.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, status TEXT, attempted_at TEXT, error TEXT, "
              "retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 5)")
    c.execute("INSERT INTO messages (id, status, retry_count, max_retries) VALUES ('m1', 'pending', 2, 5)")
    c.commit(); c.close()
    s = msg_store.MessageStore(db_path=db)
    assert s.defer("m1") is False                       # pending: nothing to release
    assert s.claim("m1") is True
    assert s.defer("m1", error="held") is True
    row = sqlite3.connect(db).execute(
        "SELECT status, attempted_at, error, retry_count, max_retries FROM messages").fetchone()
    assert row == ("pending", None, "held", 2, 5)       # back in the queue, retry budget untouched
    assert s.claim("m1") is True


# ------------------------------------------------------------------ nudge_pane / boot_inject
def test_nudge_pane_is_not_sent_while_another_sender_holds_the_pane(monkeypatch):
    keys = []
    monkeypatch.setattr(NP, "tmux", lambda *a: keys.append(a) or types.SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(NP, "_nudge_unlocked", lambda *a, **k: pytest.fail("typed under a held lock"))
    monkeypatch.setattr(pane_lock, "pane_lock", _short(pane_lock.pane_lock))
    with _hold("gm"):
        assert NP.nudge("gm", "hello") == NP.RC_NOT_SENT
    assert keys == []


def _short(real):
    """pane_lock with the caller's wait cut to 0.1 s, so a 'busy' test does not sit out a 5-8 s wait."""
    def f(session, wait_s=8.0, poll_s=0.05):
        return real(session, wait_s=min(wait_s, 0.1), poll_s=poll_s)
    return f


def test_nudge_pane_runs_under_the_lock(monkeypatch):
    seen = []
    monkeypatch.setattr(NP, "_nudge_unlocked", lambda s, t, **k: seen.append(_held_elsewhere(s)) or 0)
    assert NP.nudge("gm", "hello") == 0
    assert seen == [True]


def test_boot_inject_is_pane_locked_while_another_sender_holds_the_pane(monkeypatch):
    monkeypatch.setattr(BI, "_boot_inject_unlocked", lambda *a, **k: pytest.fail("typed under a held lock"))
    monkeypatch.setattr(pane_lock, "pane_lock", _short(pane_lock.pane_lock))
    with _hold("pm-x"):
        assert BI.boot_inject("pm-x", "hello") == ("failed", "pane-locked")


def _hold(session):
    import contextlib

    @contextlib.contextmanager
    def cm():
        p = pane_lock.lock_path(session)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    return cm()


def test_boot_inject_runs_under_the_lock_and_passes_its_options(monkeypatch):
    seen = []
    monkeypatch.setattr(BI, "_boot_inject_unlocked",
                        lambda s, t, rt, **kw: seen.append((_held_elsewhere(s), rt, kw)) or ("submitted", "ok"))
    assert BI.boot_inject("pm-x", "hello", "codex", allow_canonical=True) == ("submitted", "ok")
    assert seen == [(True, "codex", {"allow_canonical": True})]


# ------------------------------------------------------------------ grok_mail_watch
def test_grok_nudge_types_nothing_while_another_sender_holds_the_pane(monkeypatch):
    keys = []
    monkeypatch.setattr(GMW, "tmux", lambda *a: keys.append(a) or types.SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(GMW, "pane_blocker", lambda s, q: None)
    with _hold("grok-gm"):
        ok, why = GMW.nudge_locked("grok-gm", "[fleet-mail] 1 pending", 1.0, 4.0, lock_wait_s=0.1)
    assert (ok, why) == (False, "pane-locked")
    assert [k for k in keys if k and k[0] == "send-keys"] == []


def test_grok_blocker_read_and_typing_happen_under_the_lock(monkeypatch):
    seen = []
    monkeypatch.setattr(GMW, "pane_blocker", lambda s, q: seen.append(("blocker", _held_elsewhere(s))) or None)
    monkeypatch.setattr(GMW, "nudge", lambda s, t, v: seen.append(("nudge", _held_elsewhere(s))) or True)
    assert GMW.nudge_locked("grok-gm", "[fleet-mail] 1 pending", 1.0, 4.0) == (True, None)
    assert seen == [("blocker", True), ("nudge", True)]


def test_grok_blocker_under_the_lock_holds_the_nudge(monkeypatch):
    monkeypatch.setattr(GMW, "pane_blocker", lambda s, q: "composer-has-text")
    monkeypatch.setattr(GMW, "nudge", lambda *a: pytest.fail("typed past a blocker"))
    assert GMW.nudge_locked("grok-gm", "x", 1.0, 4.0) == (False, "composer-has-text")


def test_grok_main_CALL_SITE_types_only_through_the_locked_nudge(monkeypatch, tmp_path):
    """main()'s nudge must go through nudge_locked (a bare nudge() there bypasses the lock)."""
    calls = []
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(GMW, "Inotify", lambda *a: types.SimpleNamespace(wait=lambda t: set()))
    monkeypatch.setattr(GMW, "pending_ids", lambda db, agent, tenant: ["msg_1"])
    monkeypatch.setattr(GMW, "KILL", str(tmp_path / "no-kill"))
    monkeypatch.setattr(GMW, "pane_blocker", lambda s, q: None)
    monkeypatch.setattr(GMW, "nudge", lambda s, t, v: calls.append(("nudge", _held_elsewhere(s))) or True)
    assert GMW.main(["--once", "--agent", "grok-gm", "--db", str(tmp_path / "tasks.db")]) == 0
    assert calls == [("nudge", True)]


def test_grok_main_the_read_under_the_lock_decides_not_the_early_one(monkeypatch, tmp_path):
    """main() reads the pane once early (unlocked; it can only skip) and again under the lock: a
    blocker that appears between the two still stops the typing."""
    reads, typed = [], []
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(GMW, "Inotify", lambda *a: types.SimpleNamespace(wait=lambda t: set()))
    monkeypatch.setattr(GMW, "pending_ids", lambda db, agent, tenant: ["msg_1"])
    monkeypatch.setattr(GMW, "KILL", str(tmp_path / "no-kill"))
    def blocker(s, q):
        reads.append(_held_elsewhere(s))
        return None if len(reads) == 1 else "composer-has-text"   # clear early, busy under the lock
    monkeypatch.setattr(GMW, "pane_blocker", blocker)
    monkeypatch.setattr(GMW, "nudge", lambda *a: typed.append(a) or True)
    assert GMW.main(["--once", "--agent", "grok-gm", "--db", str(tmp_path / "tasks.db")]) == 0
    assert reads == [False, True] and typed == []


# ------------------------------------------------------------------ red_alert_watch
def _repair_report():
    return {"id": "ra_x", "class": "pane_dead", "card_id": None, "status": "open"}


def _wire(monkeypatch, fn):
    monkeypatch.setattr(RAW, "decide", lambda *a, **k: {"action": "repair", "fix": "bare_enter", "reason": "t"})
    monkeypatch.setattr(RAW, "_view", lambda r: r)
    monkeypatch.setattr(RAW, "norm_answer", lambda c: None)
    monkeypatch.setattr(RAW, "armed", lambda: True)
    monkeypatch.setitem(RAW.REPAIRS, "bare_enter", fn)
    attempts, updates = [], []
    monkeypatch.setattr(RA, "record_attempt", lambda rid, **k: attempts.append(k))
    monkeypatch.setattr(RA, "update", lambda rid, **k: updates.append(k))
    monkeypatch.setattr(RAW.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    return attempts, updates


def test_red_alert_repair_waits_for_the_pane_and_records_no_attempt_when_busy(monkeypatch):
    attempts, updates = _wire(monkeypatch, lambda *a: pytest.fail("repair typed into a locked pane"))
    monkeypatch.setattr(RAW, "REPAIR_LOCK_WAIT_S", 0.1)
    with _hold("gm"):
        RAW.handle("gm", "gm", _repair_report(), {"attached": {}}, dry=False)
    assert attempts == []                                  # a busy pane is not a FAILED repair (no escalation)
    assert not any(u.get("status") == "repairing" for u in updates)


def test_red_alert_repair_runs_while_holding_the_pane_lock(monkeypatch):
    held = []
    attempts, _ = _wire(monkeypatch, lambda seat, session, ev: held.append(_held_elsewhere(session)) or (True, "ok"))
    RAW.handle("gm", "gm", _repair_report(), {"attached": {}}, dry=False)
    assert held == [True] and attempts and attempts[0]["ok"] is True


# ------------------------------------------------------------------ the race, on a real fenced tmux pane
EMULATOR = textwrap.dedent(r'''
    import os, sys, tty
    log = sys.argv[1]
    fd = sys.stdin.fileno(); tty.setraw(fd)
    buf = ""
    def draw():
        sys.stdout.write("\r\x1b[2K❯ " + buf); sys.stdout.flush()
    sys.stdout.write("emulator ready\r\n"); draw()
    while True:
        data = os.read(fd, 4096).decode("utf-8", "ignore")
        for ch in data:
            if ch in ("\r", "\n"):
                if buf:
                    open(log, "a").write(buf + "\n")
                    sys.stdout.write("\r\x1b[2K> " + buf + "\r\n")
                buf = ""
            elif ch == "\x15":
                buf = ""
            elif ch == "\x7f":
                buf = buf[:-1]
            elif ch == "\x1b":
                pass
            else:
                buf += ch
        draw()
''')

GATEWAY_CHILD = textwrap.dedent(r'''
    import sys
    sys.path.insert(0, sys.argv[1])
    import watch_gateway as G
    class Det:
        def get_agent_status(self, s): return {"state": "idle", "activity": "", "process": {}}
    G._agent_status = lambda: Det()
    G.INJECT_INGEST_WAIT_S = float(sys.argv[3])
    print("GW", G.verified_inject("emu", sys.argv[2]), flush=True)
''')

ROUTER_CHILD = textwrap.dedent(r'''
    import importlib.util, sys
    sys.path.insert(0, sys.argv[1])
    spec = importlib.util.spec_from_file_location("message_router", sys.argv[1] + "/message-router.py")
    R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
    if hasattr(R, "fence_check"): R.fence_check = lambda *a, **k: None
    if hasattr(R, "composer_is_ghost"): R.composer_is_ghost = lambda *a, **k: False
    print("RT", R.inject("emu", sys.argv[2]), flush=True)
''')

GW_TEXT = "gateway says hello from the quest"
RT_TEXT = "[MSG from orchestra-builder | low] router delivery for the race test"
_HAS_TMUX = subprocess.run(["which", "tmux"], capture_output=True).returncode == 0


def _rig(tmp_path, gw_lock_dir, rt_lock_dir):
    tdir = tempfile.mkdtemp(prefix="tmuxf-", dir="/tmp")       # short: a socket path has a length cap
    base = dict(os.environ, TMUX_TMPDIR=tdir, MSG_DB_PATH=str(tmp_path / "bus.db"))
    base.pop("TMUX", None)                                        # $TMUX would override TMUX_TMPDIR
    assert base["TMUX_TMPDIR"] != os.environ.get("TMUX_TMPDIR")   # FENCE: never the live server
    log = tmp_path / "prompts.log"
    for name, src in (("emu.py", EMULATOR), ("gw.py", GATEWAY_CHILD), ("rt.py", ROUTER_CHILD)):
        (tmp_path / name).write_text(src)
    tm = lambda *a: subprocess.run(["tmux", *a], env=base, capture_output=True, text=True, timeout=10)
    assert tm("new-session", "-d", "-s", "emu", "-x", "160", "-y", "30",
              f"{sys.executable} {tmp_path/'emu.py'} {log}").returncode == 0
    assert tm("list-sessions", "-F", "#{session_name}").stdout.split() == ["emu"]   # only ours
    for _ in range(50):
        if "emulator ready" in tm("capture-pane", "-t", "=emu:", "-p").stdout:
            break
        time.sleep(0.1)
    return tm, log, dict(base, PANE_LOCK_DIR=str(gw_lock_dir)), dict(base, PANE_LOCK_DIR=str(rt_lock_dir))


def _race(tmp_path, tm, gw_env, rt_env, ingest_s="2.5"):
    gw = subprocess.Popen([sys.executable, str(tmp_path / "gw.py"), str(HERE), GW_TEXT, ingest_s],
                          env=gw_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(300):                              # the router fires once the gateway has TYPED
        if GW_TEXT in tm("capture-pane", "-t", "=emu:", "-p").stdout:
            break
        time.sleep(0.02)
    rt = subprocess.Popen([sys.executable, str(tmp_path / "rt.py"), str(HERE), RT_TEXT],
                          env=rt_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    g_out, g_err = gw.communicate(timeout=90)
    r_out, r_err = rt.communicate(timeout=90)
    return g_out + g_err, r_out + r_err


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_RACE_shared_lock_two_separate_prompts_composer_empty(tmp_path):
    locks = tmp_path / "locks"
    tm, log, gw_env, rt_env = _rig(tmp_path, locks, locks)          # ONE shared lock dir
    try:
        g, r = _race(tmp_path, tm, gw_env, rt_env)
        assert "GW (True" in g, g
        if "RT held" in r:                            # held = row stays pending: run the next tick
            r2 = subprocess.run([sys.executable, str(tmp_path / "rt.py"), str(HERE), RT_TEXT],
                                env=rt_env, capture_output=True, text=True, timeout=90)
            r = r2.stdout + r2.stderr
        assert "RT submitted" in r, r
        prompts = log.read_text().splitlines()
        assert sorted(prompts) == sorted([GW_TEXT, RT_TEXT]), prompts      # separate, nothing glued
    finally:
        tm("kill-server")


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux not installed")
def test_SABOTAGE_no_shared_lock_glues_the_two_messages(tmp_path):
    """The control: separate lock dirs = no shared lock. The router's paste lands inside the
    gateway's ingest window and the gateway's Enter submits both as ONE prompt."""
    tm, log, gw_env, rt_env = _rig(tmp_path, tmp_path / "locks-gw", tmp_path / "locks-rt")
    try:
        _race(tmp_path, tm, gw_env, rt_env)
        prompts = log.read_text().splitlines() if log.exists() else []
        assert sorted(prompts) != sorted([GW_TEXT, RT_TEXT]), \
            f"expected the unlocked race to glue/split, got clean prompts {prompts}"
    finally:
        tm("kill-server")
