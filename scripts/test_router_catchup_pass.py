"""A send that lands while a router pass is running still goes out within seconds.

msg_store kicks a pass on every send, but the kick exits at once when another pass holds
LOCKFILE, and that pass already read its candidates. Without a catch-up the row waited for
the next beat (up to a minute). finish_pass() re-runs the router as a fresh process when a
pending row arrived after the pass began: one catch-up per burst, bounded in depth.
"""
import importlib.util
import os
import time

import pytest

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router_catchup", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


class Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, path, argv, env):
        self.calls.append((path, argv, env))


@pytest.fixture
def store(tmp_path):
    s = mr.MessageStore(db_path=str(tmp_path / "tasks.db"))
    with s._conn() as conn:                 # the schema lives in the live DB; tests make their own
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, from_agent TEXT, "
                     "to_agent TEXT, type TEXT, subject TEXT, status TEXT, created_at TEXT)")
    return s


def send(store, n=1):
    """A pending row exactly as msg_store writes it (send() itself checks sender identity)."""
    with store._conn() as conn:
        for i in range(n):
            conn.execute("INSERT INTO messages (id, from_agent, to_agent, type, subject, status, created_at) "
                         "VALUES (?, 'a', 'b', 'task', ?, 'pending', ?)",
                         (f"msg_t{time.time_ns()}_{i}", f"s{i}", mr.now_iso()))


def test_mail_sent_during_the_pass_starts_one_fresh_catch_up_pass(store):
    started = mr.now_iso()
    send(store)
    ex = Exec()
    assert mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env={}) is True
    assert len(ex.calls) == 1
    path, argv, env = ex.calls[0]
    assert argv[1:] == mr.sys.argv          # the same command, as a fresh process
    assert env["ROUTER_CATCHUP_DEPTH"] == "1"


def test_mail_older_than_the_pass_does_not_rerun(store):
    send(store)                             # the pass saw this one in its own query
    time.sleep(0.002)
    started = mr.now_iso()
    ex = Exec()
    assert mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env={}) is False
    assert ex.calls == []


def test_a_burst_of_twenty_sends_is_one_catch_up(store):
    started = mr.now_iso()
    send(store, 20)
    ex = Exec()
    mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env={})
    assert len(ex.calls) == 1


def test_catch_up_stops_at_the_depth_cap(store, monkeypatch):
    monkeypatch.setattr(mr, "ROUTER_CATCHUP_MAX", 2)
    started = mr.now_iso()
    send(store)
    ex = Exec()
    env = {"ROUTER_CATCHUP_DEPTH": "2"}
    assert mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env=env) is False
    assert ex.calls == []


def test_zero_turns_catch_up_off(store, monkeypatch):
    monkeypatch.setattr(mr, "ROUTER_CATCHUP_MAX", 0)
    started = mr.now_iso()
    send(store)
    ex = Exec()
    mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env={})
    assert ex.calls == []


def test_a_pass_that_never_ran_logs_nothing_and_never_reruns(store, capsys):
    started = mr.now_iso()
    send(store)
    ex = Exec()
    assert mr.finish_pass(False, started, time.monotonic(), 0.0, store=store, execve=ex, env={}) is None
    assert ex.calls == [] and "PASS" not in capsys.readouterr().out


def test_every_pass_that_ran_logs_its_wall_and_cpu(store, capsys):
    mr.finish_pass(True, mr.now_iso(), time.monotonic(), 0.0, store=store, execve=Exec(), env={})
    out = capsys.readouterr().out
    assert "[router] PASS wall=" in out and " cpu=" in out and "new_pending=0" in out


def test_a_broken_store_never_breaks_the_pass(capsys):
    class Broken:
        def _conn(self):
            raise OSError("db gone")
    ex = Exec()
    assert mr.finish_pass(True, mr.now_iso(), time.monotonic(), 0.0, store=Broken(), execve=ex, env={}) is False
    assert ex.calls == [] and "[catchup] pending check failed" in capsys.readouterr().out


def test_the_lost_kick_by_effect(store, tmp_path, monkeypatch):
    """The real sequence: a pass holds the lock; a send kicks a second router, which finds the
    lock and gives up (main() returns False); the first pass ends and catches the row."""
    lock = tmp_path / "message-router.lock"
    monkeypatch.setattr(mr, "LOCKFILE", lock)

    def tripwire():                         # a main() that got past the lock would be a LIVE pass
        raise AssertionError("main() ran a pass despite a held lock")
    monkeypatch.setattr(mr, "load_env_telegram", tripwire)
    monkeypatch.setenv("MSG_DB_PATH", str(tmp_path / "tasks.db"))
    started = mr.now_iso()
    lock.write_text("1")                    # the running pass holds the lock
    send(store)                             # ...a send arrives mid-pass
    assert mr.main() is False               # ...and its kick loses the lock
    lock.unlink()                           # the running pass ends
    ex = Exec()
    mr.finish_pass(True, started, time.monotonic(), 0.0, store=store, execve=ex, env={})
    assert len(ex.calls) == 1               # the row is not left for the next beat


def test_a_kick_from_inside_a_catch_up_pass_starts_with_a_full_budget():
    """The router sends mail itself, and that send's kick copies os.environ: the depth must not
    ride along. Runs the real __main__ block with main() and finish_pass() stubbed."""
    import json
    import subprocess
    import sys
    src = open(os.path.join(_here, "message-router.py")).read()
    block = src[src.index('if __name__ == "__main__":'):].replace('if __name__ == "__main__":', "if True:", 1)
    code = ("import json, os, sys, time\n"
            "seen = {}\n"
            "def main():\n    seen['kick_sees'] = os.environ.get('ROUTER_CATCHUP_DEPTH'); return False\n"
            "def now_iso(): return ''\n"
            "def _cpu_seconds(): return 0.0\n"
            "def finish_pass(*a, env=None): seen['pass_depth'] = env['ROUTER_CATCHUP_DEPTH']\n"
            + block + "\nprint(json.dumps(seen))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "ROUTER_CATCHUP_DEPTH": "2"})
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"kick_sees": None, "pass_depth": "2"}


@pytest.mark.parametrize("row_ts,counted", [
    ("2026-11-20T07:00:01+00:00", True),           # microsecond 0: isoformat drops the fraction
    ("2026-11-20T07:00:00+00:00", False),          # same second, earlier than .5
    ("2026-11-20T07:00:00.600000+00:00", True),
])
def test_created_at_compares_right_when_the_fraction_is_missing(store, row_ts, counted):
    with store._conn() as conn:
        conn.execute("INSERT INTO messages (id, status, created_at) VALUES ('m', 'pending', ?)", (row_ts,))
        assert bool(mr.pending_since(conn, "2026-11-20T07:00:00.500000+00:00")) is counted
