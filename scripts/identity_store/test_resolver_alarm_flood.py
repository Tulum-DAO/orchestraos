"""The readers-db-first alarm must not flood gm (fresh-install bug, 2026-10-14).

On a fresh install `orchestra init` arms the identity store with no seats. The message router
resolves agent meta on every pass, and each resolve found the DB "absent/empty" and sent gm an
alert: one a minute, forever (a new user's agent counted 902 of them, addressed to a gm that did
not exist yet). Now:
- an armed store with no seats on either side (DB and flat file both empty) is a fresh install,
  not a skew: silent;
- a real condition (the DB empty while the flat file has seats; the DB far below the flat file)
  alerts once per episode, again at most once a day while it lasts, and re-arms once it clears.
"""
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

from scripts.identity_store.test_resolver_db_first import _seed  # noqa: E402


def _resolver():
    spec = importlib.util.spec_from_file_location("identity_resolver_flood", os.path.join(HERE, "resolver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_install(tmp_path):
    """What `orchestra init` leaves: empty flat files, then the store armed with 0 seats."""
    from orchestra_cli import identity_cmd as IC
    (tmp_path / "state").mkdir()
    (tmp_path / "registry.json").write_text('{"agents": {}}')
    (tmp_path / "state" / "agent-sessions.json").write_text("{}")
    rep = IC.arm(Path(REPO), tmp_path, start_regenerator=False)
    assert rep.get("typed_populated") == 0
    assert (tmp_path / "state" / "identity-store-cutover.flag").exists()
    return tmp_path


def test_fresh_install_with_no_seats_never_alarms(tmp_path):
    orch = _fresh_install(tmp_path)
    r = _resolver()
    alarms = []
    for _ in range(902):                     # ~15 h of router passes
        assert r.load_meta_db_first(str(orch), {}, alarm=alarms.append) == {}
    assert alarms == []


def test_empty_db_with_seats_alarms_once_per_episode_then_daily(tmp_path, monkeypatch):
    orch = _fresh_install(tmp_path)
    r = _resolver()
    clock = [1_000_000.0]
    monkeypatch.setattr(r, "_now", lambda: clock[0], raising=False)
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    alarms = []
    for _ in range(902):
        clock[0] += 60
        assert r.load_meta_db_first(str(orch), flat, alarm=alarms.append) == flat
    assert len(alarms) == 1 and "absent/empty" in alarms[0]

    clock[0] += 24 * 3600                    # still broken a day later: one reminder
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    assert len(alarms) == 2


def test_alarm_rearms_after_the_condition_clears(tmp_path, monkeypatch):
    orch = tmp_path
    r = _resolver()
    clock = [1_000_000.0]
    monkeypatch.setattr(r, "_now", lambda: clock[0], raising=False)
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    alarms = []
    (orch / "state").mkdir()
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)          # no DB: episode 1
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    assert len(alarms) == 1
    dbp = _seed(orch, flat)                                              # healthy: clears it
    clock[0] += 60
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    assert len(alarms) == 1
    dbp.unlink()                                                         # broken again: episode 2
    clock[0] += 60
    r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    assert len(alarms) == 2


def test_count_anomaly_alarm_is_deduplicated_too(tmp_path, monkeypatch):
    orch = tmp_path
    r = _resolver()
    monkeypatch.setattr(r, "_now", lambda: 1_000_000.0, raising=False)
    _seed(orch, {"a": {"tmux_session": "a", "status": "online"}})
    flat = {k: {"tmux_session": k, "status": "online"} for k in "abcde"}
    alarms = []
    for _ in range(50):
        r.load_meta_db_first(str(orch), flat, alarm=alarms.append)
    assert len(alarms) == 1 and "far below" in alarms[0]


def test_default_alarm_sends_gm_once_across_router_passes(tmp_path, monkeypatch):
    """By effect, through the real default alarm: the msg_store send happens once, not per pass."""
    orch = _fresh_install(tmp_path)
    r = _resolver()
    sends = []
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: sends.append(argv))
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    for _ in range(30):
        r.load_meta_db_first(str(orch), flat)
    assert len(sends) == 1 and "--to" in sends[0] and sends[0][sends[0].index("--to") + 1] == "gm"


def test_unreadable_db_alarm_is_deduplicated_too(tmp_path):
    orch = tmp_path
    (orch / "state").mkdir()
    (orch / "state" / "orchestra-registry.db").write_bytes(b"not a database" * 100)
    r = _resolver()
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    alarms = []
    for _ in range(20):
        assert r.load_meta_db_first(str(orch), flat, alarm=alarms.append) == flat
    assert len(alarms) == 1 and "unreadable" in alarms[0]


def test_an_undelivered_alarm_is_not_recorded_as_sent(tmp_path):
    """codex R1 #1: a send that failed must not start the 24 h quiet period."""
    orch = tmp_path
    (orch / "state").mkdir()
    r = _resolver()
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    outcomes = [False, False, True]
    calls = []

    def alarm(msg):
        calls.append(msg)
        return outcomes[len(calls) - 1] if len(calls) <= len(outcomes) else True

    for _ in range(10):
        r.load_meta_db_first(str(orch), flat, alarm=alarm)
    assert len(calls) == 3                   # retried until delivered, then quiet


def test_default_alarm_retries_when_msg_store_exits_nonzero(tmp_path, monkeypatch):
    orch = tmp_path
    (orch / "state").mkdir()
    r = _resolver()
    sends = []

    class P:
        def __init__(self, rc):
            self.returncode = rc

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: sends.append(argv) or P(1 if len(sends) < 3 else 0))
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    for _ in range(10):
        r.load_meta_db_first(str(orch), flat)
    assert len(sends) == 3


def test_any_read_failure_is_reported_as_unreadable(tmp_path, monkeypatch):
    """codex R1 #3: a non-sqlite failure while reading the store used to escape to the
    router's generic fallback, which alarms nobody."""
    orch = tmp_path
    _seed(orch, {"a": {"tmux_session": "a", "status": "online"}})
    r = _resolver()
    from scripts.identity_store import projector

    def boom(*a, **k):
        raise ValueError("payload is not a session document")

    monkeypatch.setattr(projector, "_build_faithful_sessions", boom)
    alarms = []
    flat = {"a": {"tmux_session": "a", "status": "online"}}
    assert r.load_meta_db_first(str(orch), flat, alarm=alarms.append) == flat
    assert len(alarms) == 1 and "unreadable" in alarms[0]


def _concurrent_pass(orch, out):
    import fcntl  # noqa: F401  (the platform has it; the resolver uses it)
    r = _resolver()

    def alarm(msg):
        with open(out, "a") as f:
            f.write("x\n")
        time.sleep(0.05)                     # widen the window two unlocked passes would share

    r.load_meta_db_first(str(orch), {"a": {"tmux_session": "a", "status": "online"}}, alarm=alarm)


def test_concurrent_router_passes_alarm_once(tmp_path):
    """codex R1 #2: router passes that overlap share one record; the read-modify-write is locked."""
    import multiprocessing as mp
    orch = tmp_path
    (orch / "state").mkdir()
    out = tmp_path / "alarms.txt"
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_concurrent_pass, args=(str(orch), str(out))) for _ in range(8)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    assert all(p.exitcode == 0 for p in procs)
    assert out.read_text().count("x") == 1
