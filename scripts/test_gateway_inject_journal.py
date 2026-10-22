"""No half-sent inject across a gateway restart (gm msg_40dcd58a, 2026-10-14).

Incident: a `respawn-pane -k` of the gateway killed a Quest /agent-message between `send-keys -l`
and the Enter; the operator's text sat unsent in gm's composer. Fix under test:
  - verified_inject journals the inject from just before typing until the submit is verified;
  - the next start reconciles: our text still there -> one Enter, verified; else untouched + reported;
  - D1: the Enter retry is no longer refused by the stranded_input state its OWN text creates.
The last test is gm's race test on a REAL tmux pane (fenced TMUX_TMPDIR) running a tiny composer
emulator: kill the real verified_inject mid-ingest with SIGKILL, run the real reconcile, and the
composer must end submitted (or untouched + reported), never stranded.
"""
import importlib
import json
import os
import signal
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
G = importlib.import_module("watch_gateway")
J = importlib.import_module("inject_journal")

IDLE = "some output\n❯ \n"


@pytest.fixture
def jdir(tmp_path, monkeypatch):
    d = tmp_path / "journal"
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(d))
    return d


# ------------------------------------------------------------------ the pure reconcile
def _seed(session, marker):
    J.record(session, marker + " and more", marker)
    J.mark_typed(session)              # the keystrokes went out: an actionable row


def test_reconcile_submits_our_text_and_verifies(jdir, monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda s: None)
    _seed("gm", "124 pending cards")
    screens = iter(["124 pending cards and more", ""])
    pressed, reports = [], []
    out = J.reconcile(lambda s: next(screens), pressed.append, lambda *a: reports.append(a[1]))
    assert out == [("gm", "submitted")] and pressed == ["gm"] and reports == ["submitted"]
    assert J.rows() == []


def test_reconcile_never_touches_text_that_is_not_ours(jdir, monkeypatch):
    _seed("gm", "124 pending cards")
    pressed = []
    out = J.reconcile(lambda s: "the operator is typing something else", pressed.append, lambda *a: None)
    assert out == [("gm", "not-ours")] and pressed == []
    assert J.rows() == []


def test_reconcile_unverified_enter_is_left_untouched_and_reported_once(jdir, monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda s: None)
    _seed("gm", "124 pending cards")
    pressed, reports = [], []
    out = J.reconcile(lambda s: "124 pending cards and more", pressed.append, lambda *a: reports.append(a[1]))
    assert out == [("gm", "left-untouched")] and pressed == ["gm"] and reports == ["left-untouched"]
    # codex r2: the row STAYS as evidence, marked attempted ...
    assert [r["session"] for r in J.rows()] == ["gm"] and J.rows()[0]["reconcile_attempted_at"]
    # ... and a later start reports it again WITHOUT pressing Enter a second time
    out = J.reconcile(lambda s: "124 pending cards and more", pressed.append, lambda *a: reports.append(a[1]))
    assert out == [("gm", "left-untouched")] and pressed == ["gm"]
    # once the text has left the composer (the operator sent or cleared it), the row goes
    J.reconcile(lambda s: "", pressed.append, lambda *a: None)
    assert J.rows() == []


def test_reconcile_session_gone_and_unreadable(jdir):
    _seed("dead", "x")
    _seed("flaky", "y")
    reads = {"dead": None, "flaky": "ERROR:capture failed"}
    out = dict(J.reconcile(lambda s: reads[s], lambda s: pytest.fail("no Enter"), lambda *a: None))
    assert out == {"dead": "session-gone", "flaky": "unreadable"}
    assert [r["session"] for r in J.rows()] == ["flaky"]    # kept for the next start


def test_reconcile_no_composer_on_screen_means_no_enter(jdir):
    _seed("gm", "124 pending cards")
    out = J.reconcile(lambda s: "", lambda s: pytest.fail("Enter pressed with no composer"), lambda *a: None)
    assert out == [("gm", "not-ours")]


# ------------------------------------------------------------------ verified_inject journals
class _Det:
    def __init__(self, states): self._s = list(states)
    def get_agent_status(self, sess):
        st = self._s.pop(0) if len(self._s) > 1 else self._s[0]
        return {"state": st, "activity": "", "process": {}}


class _Tmux:
    def __init__(self, captures, jdir, fail_type=False):
        self.calls, self._c, self.jdir, self.fail_type = [], list(captures), jdir, fail_type
        self.row_at_enter = []
    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        if args[0] == "send-keys" and "-l" in args and self.fail_type:
            return types.SimpleNamespace(returncode=1, stdout="")
        if args[0] == "send-keys" and args[-1] == "Enter":
            self.row_at_enter.append(any(self.jdir.glob("*.json")))
        out = self._c.pop(0) if (args[0] == "capture-pane" and self._c) else ""
        return types.SimpleNamespace(returncode=0, stdout=out)


def _env(monkeypatch, jdir, states, captures, **kw):
    t = _Tmux(captures, jdir, **kw)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    det = _Det(states)                                  # ONE detector: the state sequence must advance
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def test_row_exists_while_typed_and_is_cleared_after_verified_submit(jdir, monkeypatch):
    t = _env(monkeypatch, jdir, ["idle"], [IDLE, "out\n❯ \n"])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert ok, info
    assert t.row_at_enter == [True]               # the row existed when Enter was pressed
    assert J.rows() == []


def test_row_kept_when_submit_unverified(jdir, monkeypatch):
    stuck = "out\n❯ run the audit\n"
    t = _env(monkeypatch, jdir, ["idle"], [IDLE, stuck, stuck])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok and info["reason"] == "unverified_submit"
    assert [r["session"] for r in J.rows()] == ["tgt"]


def test_row_cleared_when_nothing_was_typed(jdir, monkeypatch):
    _env(monkeypatch, jdir, ["idle"], [IDLE], fail_type=True)
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok and info["reason"] == "send_failed"
    assert J.rows() == []


def test_D1_retry_enter_is_pressed_for_our_own_stranded_text(jdir, monkeypatch):
    """First Enter swallowed -> our text sits in the composer -> state reads stranded_input. The
    retry must still press Enter (it used to `break`) and the submit verifies on attempt 2."""
    stuck = "out\n❯ run the audit\n"
    t = _env(monkeypatch, jdir, ["idle", "stranded_input"], [IDLE, stuck, "out\n❯ \n"])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert ok and info["attempts"] == 2, info
    assert sum(1 for c in t.calls if c[0] == "send-keys" and c[-1] == "Enter") == 2


def test_D1_retry_still_refused_when_the_composer_does_not_START_with_our_text(jdir, monkeypatch):
    """Our text behind someone else's (a forced send over the operator's draft): not ours to Enter again."""
    other = "out\n❯ the operator typed this himself run the audit\n"
    t = _env(monkeypatch, jdir, ["idle", "stranded_input"], [IDLE, other, other])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok
    assert sum(1 for c in t.calls if c[0] == "send-keys" and c[-1] == "Enter") == 1


# ------------------------------------------------------------------ gm's race test: a REAL pane
EMULATOR = textwrap.dedent(r'''
    import os, sys, termios, tty
    log = sys.argv[1]
    fd = sys.stdin.fileno(); tty.setraw(fd)
    buf = ""
    def draw():
        sys.stdout.write("\r\x1b[2K❯ " + buf); sys.stdout.flush()
    sys.stdout.write("emulator ready\r\n"); draw()
    while True:
        ch = os.read(fd, 1).decode("utf-8", "ignore")
        if ch in ("\r", "\n"):
            open(log, "a").write(buf + "\n")
            sys.stdout.write("\r\x1b[2K> sent: " + buf + "\r\n"); buf = ""; draw()
        elif ch == "\x7f":
            buf = buf[:-1]; draw()
        elif ch:
            buf += ch; draw()
''')

CHILD = textwrap.dedent(r'''
    import sys, types
    sys.path.insert(0, sys.argv[1])
    import watch_gateway as G
    class Det:
        def get_agent_status(self, s): return {"state": "idle", "activity": "", "process": {}}
    G._agent_status = lambda: Det()
    G.INJECT_INGEST_WAIT_S = 30.0          # wide window: the parent kills us inside it
    print(G.verified_inject("emu", "124 pending cards right now"), flush=True)
''')

RECONCILE = textwrap.dedent(r'''
    import sys
    sys.path.insert(0, sys.argv[1])
    import watch_gateway as G
    class Det:
        def get_agent_status(self, s): return {"state": "stranded_input", "activity": "", "process": {}}
    G._agent_status = lambda: Det()
    G._report_inject_reconcile = lambda s, o, r: print("REPORT", s, o, flush=True)
    print(G.reconcile_inject_journal(), flush=True)
''')


@pytest.mark.skipif(subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
                    reason="tmux not installed")
def test_RACE_kill_mid_inject_then_restart_reconcile_submits_never_strands(tmp_path):
    tdir = tempfile.mkdtemp(prefix="tmux-fence-")
    env = dict(os.environ, TMUX_TMPDIR=tdir, WATCH_GATEWAY_INJECT_JOURNAL_DIR=str(tmp_path / "journal"))
    env.pop("TMUX", None)
    assert env["TMUX_TMPDIR"] != os.environ.get("TMUX_TMPDIR")       # FENCE: never the live server
    log = tmp_path / "submitted.log"
    (tmp_path / "emu.py").write_text(EMULATOR)
    (tmp_path / "child.py").write_text(CHILD)
    (tmp_path / "rec.py").write_text(RECONCILE)
    tm = lambda *a: subprocess.run(["tmux", *a], env=env, capture_output=True, text=True, timeout=10)
    try:
        assert tm("new-session", "-d", "-s", "emu", "-x", "120", "-y", "20",
                  f"{sys.executable} {tmp_path/'emu.py'} {log}").returncode == 0
        for _ in range(50):
            if "emulator ready" in tm("capture-pane", "-t", "=emu:", "-p").stdout:
                break
            time.sleep(0.1)
        child = subprocess.Popen([sys.executable, str(tmp_path / "child.py"), str(HERE)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        jfile = tmp_path / "journal" / "emu.json"
        def _typed_mark():
            try:
                return json.loads(jfile.read_text()).get("typed") is True
            except (OSError, ValueError):
                return False
        for _ in range(200):                       # wait until the text is TYPED (marked row + composer)
            if _typed_mark() and "124 pending cards" in tm("capture-pane", "-t", "=emu:", "-p").stdout:
                break
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGKILL)         # the crash: between send-keys -l and Enter
        child.wait(timeout=10)
        time.sleep(0.3)
        screen = tm("capture-pane", "-t", "=emu:", "-p").stdout
        assert "❯ 124 pending cards right now" in screen, screen    # stranded: the incident, reproduced
        assert not log.exists() or "124 pending" not in log.read_text()
        assert jfile.exists(), "the journal row must survive the kill"
        rec = subprocess.run([sys.executable, str(tmp_path / "rec.py"), str(HERE)], env=env,
                             capture_output=True, text=True, timeout=60)
        assert "('emu', 'submitted')" in rec.stdout, rec.stdout + rec.stderr
        assert log.read_text().splitlines() == ["124 pending cards right now"]   # submitted ONCE
        assert "❯ 124" not in tm("capture-pane", "-t", "=emu:", "-p").stdout     # composer empty
        assert not jfile.exists()
    finally:
        tm("kill-session", "-t", "=emu")


def test_main_reconciles_BEFORE_the_port_binds(monkeypatch):
    order = []
    import aiohttp.web as web
    monkeypatch.setattr(G, "gateway_token", lambda: "tok")
    monkeypatch.setattr(G, "reconcile_inject_journal", lambda: order.append("reconcile") or [])
    monkeypatch.setattr(G, "_warm_agents_cache", lambda **k: True, raising=False)   # fenced: no real fleet scan
    monkeypatch.setattr(G, "build_app", lambda: order.append("build_app") or object())
    monkeypatch.setattr(web, "run_app", lambda *a, **k: order.append("run_app"))
    monkeypatch.setattr(sys, "argv", ["watch_gateway.py"])
    G.main()
    assert order == ["reconcile", "build_app", "run_app"]


# ------------------------------------------------------------------ clear-own: never strand, never drop
def test_unverified_submit_clears_OUR_text_and_returns_it(jdir, monkeypatch):
    stuck = "out\n❯ run the audit\n"
    t = _env(monkeypatch, jdir, ["idle", "stranded_input"], [IDLE, stuck, stuck, stuck, "out\n❯ \n"])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok and info["reason"] == "unverified_submit"
    assert info["cleared"] is True and info["text_returned"] == "run the audit"
    assert ("send-keys", "-t", "=tgt:", "C-u") in t.calls
    assert J.rows() == []                          # nothing left behind, nothing to reconcile


def test_unverified_submit_never_clears_text_that_is_not_ours(jdir, monkeypatch):
    other = "out\n❯ the operator typed this himself run the audit\n"
    t = _env(monkeypatch, jdir, ["idle", "stranded_input"], [IDLE, other, other, other])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok and info["cleared"] is False and "text_returned" not in info
    assert not any(c[-1] == "C-u" for c in t.calls)


def _handler(monkeypatch, inject_result, accepts):
    import asyncio, json as _json
    held = []
    monkeypatch.setattr(G, "_authorized", lambda r: True)
    monkeypatch.setattr(G, "_test_session_refusal", lambda r, s: None, raising=False)
    monkeypatch.setattr(G, "_protected_refusal", lambda r, s: None)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: {"tgt"})
    monkeypatch.setattr(G, "verified_inject", lambda s, t, f=False: inject_result)
    monkeypatch.setattr(G, "_hold_for_boundary", lambda s, t, a, origin="app": held.append((s, t)) or "msg_held1")
    monkeypatch.setattr(G, "_parse_actor", lambda r: ("device", "quest"))

    async def _no_ledger(*a, **k):      # never the LIVE operator-actions ledger (ORCH_DIR is import-time)
        return None
    monkeypatch.setattr(G, "_record_operator_message", _no_ledger)

    class Req:
        headers = {}
        async def json(self):
            return {"session": "tgt", "text": "run the audit", **({"accepts": accepts} if accepts else {})}
    resp = asyncio.new_event_loop().run_until_complete(G.handle_agent_message(Req()))
    return resp.status, _json.loads(resp.body), held


CLEARED = (False, {"reason": "unverified_submit", "state": "idle", "cleared": True,
                   "text_returned": "run the audit"})


def test_cleared_text_is_HELD_for_a_held_capable_client(monkeypatch):
    status, body, held = _handler(monkeypatch, CLEARED, ["held"])
    assert status == 200 and body["held"] is True and body["delivered"] is False and body["cleared"] is True
    assert held == [("tgt", "run the audit")]           # durable: delivered at the next boundary


def test_cleared_text_is_RETURNED_to_a_legacy_client(monkeypatch):
    status, body, held = _handler(monkeypatch, CLEARED, None)
    assert status == 502 and body["delivered"] is False and body["text_returned"] == "run the audit"
    assert held == []


# ------------------------------------------------------------------ codex r1
def test_reconcile_never_enters_our_text_with_a_persons_words_appended(jdir):
    J.record("gm", "124 pending cards", "124 pending cards")
    out = J.reconcile(lambda s: "124 pending cards and the operator kept typing",
                      lambda s: pytest.fail("Enter would submit the operator's words"), lambda *a: None)
    assert out == [("gm", "not-ours")]


def test_reconcile_runs_under_the_pane_lock_and_keeps_the_row_when_busy(jdir):
    from contextlib import contextmanager
    J.record("gm", "hello", "hello"); J.mark_typed("gm")
    @contextmanager
    def busy(session):
        yield False
    out = J.reconcile(lambda s: pytest.fail("read without the lock"), lambda s: pytest.fail("Enter"),
                      lambda *a: None, lock=busy)
    assert out == [("gm", "unreadable")] and [r["session"] for r in J.rows()] == ["gm"]


def test_gateway_reconcile_passes_the_pane_lock(monkeypatch):
    seen = {}
    monkeypatch.setattr(J, "reconcile", lambda *a, **k: seen.update(k) or [])
    G.reconcile_inject_journal()
    assert seen.get("lock") is G._pane_guard


def test_journal_write_failure_types_nothing(jdir, monkeypatch):
    t = _env(monkeypatch, jdir, ["idle"], [IDLE])
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(J, "record", boom)
    ok, info = G.verified_inject("tgt", "run the audit")
    assert not ok and info["reason"] == "journal_unwritable"
    assert not any(c[0] == "send-keys" for c in t.calls)


def test_clear_own_refuses_our_text_with_a_persons_words_appended(jdir, monkeypatch):
    mixed = "out\n❯ run the audit please also check X\n"
    t = _env(monkeypatch, jdir, ["idle", "stranded_input"], [IDLE, mixed, mixed, mixed])
    ok, info = G.verified_inject("tgt", "run the audit")
    assert info.get("cleared") is not True
    assert not any(c[-1] == "C-u" for c in t.calls)


# ------------------------------------------------------------------ tmux-inject.sh journals too (codex r2)
STUCK_EMU = textwrap.dedent(r'''
    import os, sys, tty
    fd = sys.stdin.fileno(); tty.setraw(fd); buf = ""
    sys.stdout.write("emulator ready\r\n\u276f "); sys.stdout.flush()
    while True:
        ch = os.read(fd, 1).decode("utf-8", "ignore")
        if ch in ("\r", "\n"):
            continue                                  # Enter is swallowed: never submits
        if ch == "\x15":
            buf = ""                                  # C-u clears the line, as Claude Code does
        else:
            buf += ch
        sys.stdout.write("\r\x1b[2K\u276f " + buf); sys.stdout.flush()
''')


CLEAR_CHILD = textwrap.dedent(r'''
    import sys
    sys.path.insert(0, sys.argv[1])
    import watch_gateway as G
    class Det:
        def get_agent_status(self, s): return {"state": "idle", "activity": "", "process": {}}
    G._agent_status = lambda: Det()
    G.INJECT_INGEST_WAIT_S = 0.3
    print("RESULT", G.verified_inject("stuck", sys.argv[2]), flush=True)
''')


@pytest.mark.skipif(subprocess.run(["which", "tmux"], capture_output=True).returncode != 0, reason="no tmux")
@pytest.mark.parametrize("viewer", [True, False])
def test_clear_own_never_runs_with_a_client_attached_REAL_tmux(tmp_path, viewer):
    tdir = tempfile.mkdtemp(prefix="tmux-fence-")
    env = dict(os.environ, TMUX_TMPDIR=tdir, WATCH_GATEWAY_INJECT_JOURNAL_DIR=str(tmp_path / "j"),
               PANE_LOCK_DIR=str(tmp_path / "locks"))
    env.pop("TMUX", None)
    assert env["TMUX_TMPDIR"] != os.environ.get("TMUX_TMPDIR")
    (tmp_path / "emu.py").write_text(STUCK_EMU)
    (tmp_path / "c.py").write_text(CLEAR_CHILD)
    tm = lambda *a: subprocess.run(["tmux", *a], env=env, capture_output=True, text=True, timeout=10)
    text = "a message the pane will not submit"
    try:
        tm("new-session", "-d", "-s", "stuck", "-x", "120", "-y", "20", f"{sys.executable} {tmp_path/'emu.py'}")
        if viewer:   # a REAL attached client: a person could be typing in this pane
            tm("new-session", "-d", "-s", "viewer", "-x", "120", "-y", "20",
               f"env -u TMUX TMUX_TMPDIR={tdir} tmux attach -t =stuck")
            for _ in range(50):
                if tm("list-clients", "-t", "=stuck:").stdout.strip():
                    break
                time.sleep(0.1)
            assert tm("list-clients", "-t", "=stuck:").stdout.strip(), "viewer never attached"
        for _ in range(50):
            if "emulator ready" in tm("capture-pane", "-t", "=stuck:", "-p").stdout:
                break
            time.sleep(0.1)
        r = subprocess.run([sys.executable, str(tmp_path / "c.py"), str(HERE), text], env=env,
                           capture_output=True, text=True, timeout=90)
        screen = tm("capture-pane", "-t", "=stuck:", "-p").stdout
        rows = list((tmp_path / "j").glob("*.json"))
        if viewer:
            assert "'cleared': False" in r.stdout and "client is attached" in r.stdout, r.stdout + r.stderr
            assert text in screen and rows            # left in front of the person, on record
        else:
            assert "'cleared': True" in r.stdout and "'text_returned'" in r.stdout, r.stdout + r.stderr
            assert text not in screen.splitlines()[-1] and not rows
    finally:
        tm("kill-session", "-t", "=viewer")
        tm("kill-session", "-t", "=stuck")


def test_reconcile_marks_the_row_BEFORE_pressing_enter(jdir, monkeypatch):
    monkeypatch.setattr(J.time, "sleep", lambda s: None)
    J.record("gm", "hello", "hello"); J.mark_typed("gm")
    seen = {}
    def enter(session):
        seen["marked_at_enter"] = bool(J.rows()[0].get("reconcile_attempted_at"))
        raise SystemExit("crash right after Enter")
    with pytest.raises(SystemExit):
        J.reconcile(lambda s: "hello", enter, lambda *a: None)
    assert seen["marked_at_enter"] is True
    pressed = []                                      # the next start must not press again
    J.reconcile(lambda s: "hello", pressed.append, lambda *a: None)
    assert pressed == []


def test_reconcile_no_enter_when_the_mark_cannot_be_written(jdir, monkeypatch):
    J.record("gm", "hello", "hello"); J.mark_typed("gm")
    monkeypatch.setattr(J, "_mark_attempted", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    out = J.reconcile(lambda s: "hello", lambda s: pytest.fail("Enter without a durable mark"), lambda *a: None)
    assert out == [("gm", "unreadable")]


def test_reconcile_failed_enter_unmarks_the_row_for_a_later_retry(jdir, monkeypatch):
    J.record("gm", "hello", "hello"); J.mark_typed("gm")
    out = J.reconcile(lambda s: "hello", lambda s: False, lambda *a: None)
    assert out == [("gm", "unreadable")]
    assert not J.rows()[0].get("reconcile_attempted_at")      # the next start may try once


def test_ownership_is_exact_a_spacing_edit_is_not_ours():
    assert J.is_ours("hello world", "hello world")
    assert J.is_ours("  hello world ", "hello world")
    assert not J.is_ours("hello  world", "hello world")


def test_a_row_never_marked_typed_is_reported_and_NEVER_pressed(jdir, monkeypatch):
    """codex R4/R5 on #472: the process died after record() and before its keystrokes. A person may
    later type the identical text, park it and detach: the reconcile must never submit that draft."""
    monkeypatch.setattr(J.time, "sleep", lambda s: None)
    J.record("gm", "deploy", "deploy")                    # recorded, never marked typed
    pressed, reports = [], []
    out = J.reconcile(lambda s: "deploy", pressed.append, lambda *a: reports.append(a[1]))
    assert out == [("gm", "left-untouched")] and pressed == [] and reports == ["left-untouched"]


def test_verified_inject_marks_the_row_typed_only_after_its_keystrokes(jdir, monkeypatch):
    seen = []

    def tmux(*a, timeout=5):
        if a[0] == "send-keys" and "-l" in a:
            seen.append(("typing", [r.get("typed") for r in J.rows()]))
        if a[0] == "send-keys" and a[-1] == "Enter":
            seen.append(("enter", [r.get("typed") for r in J.rows()]))
        return types.SimpleNamespace(returncode=0, stdout=IDLE if a[0] == "capture-pane" else "")
    monkeypatch.setattr(G, "_tmux", tmux)
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(
        get_agent_status=lambda s: {"state": "idle", "activity": "", "process": {"runtime": "claude"}}))
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    G.verified_inject("gm", "hello there")
    assert seen[0] == ("typing", [None])                  # recorded, not yet typed
    assert seen[1] == ("enter", [True])                   # marked once the keystrokes went out


def test_a_forced_send_never_overwrites_a_half_sent_row(jdir, monkeypatch):
    """codex R12 on #472: force skips the typed-text guard; it must not append to an earlier
    gateway's half-sent text and replace the row the next start needs."""
    J.record("gm", "the half-sent one", "the half-sent one"); J.mark_typed("gm")
    calls = []

    def tmux(*a, timeout=5):
        calls.append(a)
        return types.SimpleNamespace(returncode=0, stdout="out\n❯ the half-sent one\n" if a[0] == "capture-pane" else "")
    monkeypatch.setattr(G, "_tmux", tmux)
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(
        get_agent_status=lambda s: {"state": "stranded_input", "activity": "", "process": {"runtime": "claude"}}))
    ok, info = G.verified_inject("gm", "something new", True)
    assert not ok and info["reason"] == "busy" and info["composer_text"] == "the half-sent one"
    assert not [c for c in calls if c[0] == "send-keys"]
    assert [(r["text"], r.get("typed")) for r in J.rows()] == [("the half-sent one", True)]
