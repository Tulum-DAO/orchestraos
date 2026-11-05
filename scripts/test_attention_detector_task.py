"""The gateway's background attention detector: it ticks, it is fail-open, and it never holds a shutdown.

Nothing here touches a live pane: the tick is stubbed, or the fleet scan is stubbed, or (subprocess test)
tmux is pointed at an empty socket directory so the gateway sees no sessions at all."""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scripts.watch_gateway as G  # noqa: E402
import attention_store as S  # noqa: E402
from attention_testbox import Box  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    b = Box(tmp_path, monkeypatch)
    monkeypatch.setattr(G, "_refresh_agents_cache", lambda: None)
    monkeypatch.setitem(G._agents_cache, "at", time.time())
    return b


async def test_detector_ticks_and_stops_fast(monkeypatch, tmp_path):
    ticks = []
    monkeypatch.setattr(G, "_attention_tick", lambda: ticks.append(1))
    monkeypatch.setattr(G, "_attention_interval_s", lambda: 0.05)
    app = G.build_app()
    async with TestServer(app):
        await asyncio.sleep(0.3)
    assert len(ticks) >= 2                       # ran in the background; shutdown returned (no hang)


async def test_a_tick_in_flight_never_holds_the_shutdown(monkeypatch):
    monkeypatch.setattr(G, "_attention_tick", lambda: time.sleep(3))
    monkeypatch.setattr(G, "_attention_interval_s", lambda: 0.05)
    server = TestServer(G.build_app())
    await server.start_server()
    await asyncio.sleep(0.2)                     # the tick is now sleeping in the executor
    t0 = time.monotonic()
    await asyncio.wait_for(server.close(), timeout=6)   # on_shutdown + on_cleanup
    assert time.monotonic() - t0 < 1


def test_the_lifecycle_hooks_are_registered():
    app = G.build_app()
    assert G._start_attention_detector in list(app.on_startup)
    assert G._attention_on_shutdown in list(app.on_shutdown)
    assert G._stop_attention_detector in list(app.on_cleanup)


def test_tick_is_fail_open(box, monkeypatch):
    monkeypatch.setitem(G._agents_cache, "at", 0)
    monkeypatch.setattr(G, "_refresh_agents_cache", lambda: 1 / 0)
    G._attention_tick()                          # logs, returns, never raises


def test_tick_is_off_when_disarmed(box, monkeypatch):
    (box.data / "state" / "attention").mkdir(parents=True)
    (box.data / "state" / "attention" / "DISARM").write_text("")
    box.ledger.create(from_agent="gm", question="Done", worker_kind="pane", options=["accept", "respond"],
                      kind="completion", evidence={})
    G._attention_tick()
    assert box.astore.list_events() == []


def test_tick_mirrors_detects_and_runs_the_rules(box, monkeypatch):
    monkeypatch.setitem(G._agents_cache, "data", [
        {"id": "gm", "state": "crashed", "tmux_session": "gm"},
        {"id": "qa", "state": "idle", "tmux_session": "qa"},
        {"id": "hl", "state": "idle", "tmux_session": "hl", "headless": True}])
    monkeypatch.setattr(G, "_capture_pane", lambda sess, lines=60, ansi=False: "Select login method:")
    hl = box.data / "state" / "headless" / "hl"
    hl.mkdir(parents=True)
    (hl / "seat.json").write_text(json.dumps({"seat": "hl", "session_id": "s1"}))
    (hl / "events.jsonl").write_text(json.dumps({"kind": "sup", "what": "start"}) + "\n" + json.dumps(
        {"kind": "out", "ev": {"type": "result", "is_error": True, "session_id": "s1",
                               "result": "Claude AI usage limit reached|1"}}) + "\n")
    monkeypatch.setattr(G, "_headless_seat_for", lambda sess: sess if sess == "hl" else None)
    card = box.ledger.create(from_agent="gm", question="Done X", worker_kind="pane",
                             options=["accept", "respond"], kind="completion", evidence={})
    G._attention_tick()
    keys = {e["dedup_key"] for e in box.astore.list_events()}
    assert {f"card:{card}", "failed:gm:crashed", "failed:qa:signed_out", "failed:hl:usage_limit"} <= keys
    rules = {r["trigger"] for r in G._attention_rule_store()[0].list("alex")}
    assert rules == {"unanswered", "no_progress"}            # defaults seeded by the first tick


def test_unregistered_sessions_are_never_scanned(box, monkeypatch):
    """A provider sign-in session (login-*) shows a login screen by design: it is not an agent signed out."""
    import attention_rules as AR
    monkeypatch.setitem(G._agents_cache, "data", [
        {"id": "login-claude-1", "state": "idle", "tmux_session": "login-claude-1", "unregistered": True},
        {"id": "scratch-1", "state": "crashed", "tmux_session": "scratch-1", "unregistered": True},
        {"id": "scratch-2", "state": "stalled", "state_age_s": 9999, "tmux_session": "scratch-2",
         "unregistered": True}])
    captured = []
    monkeypatch.setattr(G, "_capture_pane", lambda sess, lines=60, ansi=False: captured.append(sess) or
                        "Select login method:")
    rs = AR.RuleStore(box.astore)
    rs.seed_defaults("alex")
    for r in rs.list("alex"):
        rs.update("alex", r["id"], enabled=False)
    rs.create("alex", trigger="no_progress", after_s=600, actions=[{"type": "mark_stuck"}])
    import sqlite3
    c = sqlite3.connect(box.db)
    c.execute("UPDATE attention_rules SET updated_at='2020-01-01T00:00:00+00:00'")
    c.execute("UPDATE attention_meta SET value='2020-01-01T00:00:00+00:00' WHERE key='rules_epoch'")
    c.commit(); c.close()
    G._attention_tick()
    assert captured == [] and box.astore.list_events() == [] and rs.fires() == []


def test_an_expire_rule_reaches_the_agent_through_the_resume_path(box, monkeypatch):
    import attention_rules as AR
    resumed = []
    monkeypatch.setattr(G.approval_resume, "fire_resume", lambda row, store: resumed.append(row["id"]))
    rid = box.ledger.create(from_agent="gm", question="Deploy?", worker_kind="pane", default_option="approve")
    rs = AR.RuleStore(box.astore)
    rs.seed_defaults("alex")
    for r in rs.list("alex"):
        rs.update("alex", r["id"], enabled=False)
    rs.create("alex", trigger="unanswered", after_s=60, actions=[{"type": "expire"}])
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    import sqlite3
    c = sqlite3.connect(box.db)
    c.execute("UPDATE approval_requests SET created_at=?", [past])
    c.execute("UPDATE attention_rules SET updated_at=?", [past])
    c.execute("UPDATE attention_meta SET value=? WHERE key='rules_epoch'", [past])
    c.commit(); c.close()
    G._attention_tick()
    row = box.ledger.get(rid)
    assert (row["answer"], row["answer_surface"]) == ("approve", "rule") and resumed == [rid]


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def test_the_real_gateway_exits_on_sigterm_with_the_detector_running(tmp_path):
    """The 2026-10-22 lesson: restart_watch_gateway.sh waits 30 s; the gateway must drain well inside it."""
    data = tmp_path / "data"; (data / "state").mkdir(parents=True)
    tok = tmp_path / "tok"; tok.write_text("t" * 40)
    (tmp_path / "tmux").mkdir()
    port = _free_port()
    env = {**os.environ, "WATCH_GATEWAY_TOKEN_FILE": str(tok), "WATCH_GATEWAY_PORT": str(port),
           "ORCHESTRA_DIR": str(data), "ORCH_DIR": str(data), "HOME": str(tmp_path),
           "TMUX_TMPDIR": str(tmp_path / "tmux"), "APPROVAL_DDL_ARMED": "all"}
    env.pop("TMUX", None)
    p = subprocess.Popen([sys.executable, str(HERE / "watch_gateway.py")], env=env, cwd=str(HERE.parent),
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if p.poll() is not None:
                    pytest.fail(p.stderr.read().decode()[-2000:])
                time.sleep(0.2)
        time.sleep(1.5)                                         # let the first detector tick start
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
        assert time.monotonic() - t0 < 10
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
