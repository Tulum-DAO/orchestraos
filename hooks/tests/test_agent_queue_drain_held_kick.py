"""The Stop hook asks the router for a pass when a person's HELD message is pending for this seat (gm G1 ruling
msg_fbd39704). Run against EVERY live copy of the hook: this checkout's hooks/agent-queue-drain.py, and the user-level
copy at ~/.claude/hooks/agent-queue-drain.py (AQD_TEST_USER_HOOK overrides; skipped when absent).

  - only when the turn really ends (the digest is not blocking it) and a pending type='held_message' row is
    addressed to this seat (its tmux session, canonical id or an alias);
  - the kick never changes what the hook prints, and a kick that raises costs nothing.
Everything runs on a tmp data dir; the kick itself is stubbed except one test of the real one with
MSG_STORE_NO_KICK set (it must be a silent no-op)."""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "agent-queue-drain.py"
USER = Path(os.environ.get("AQD_TEST_USER_HOOK", os.path.expanduser("~/.claude/hooks/agent-queue-drain.py")))
HOOKS = [pytest.param(PUBLIC, id="public")]
if USER.is_file() and USER.resolve() != PUBLIC.resolve():
    HOOKS.append(pytest.param(USER, id="user"))


@pytest.fixture(autouse=True)
def _no_runner_tmux(monkeypatch):
    """Results must not depend on whether the runner's shell sits in tmux."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


def _load(path):
    name = f"aqd_{abs(hash(str(path)))}"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _seat(tmp_path, *, held_to=None, held_status="pending", mail=0, agent="planner", session="planner-tmux",
          alias=None):
    d = tmp_path / "data"
    (d / "state").mkdir(parents=True)
    agents = {agent: {"name": agent, "tmux_session": session}}
    if alias:
        agents[alias] = {"name": alias, "tmux_session": alias + "-old", "succeeded_by": agent}
    (d / "registry.json").write_text(json.dumps({"agents": agents}))
    (d / "state" / "queue-drain-allowlist.json").write_text(json.dumps({"enabled": True, "default_on": True}))
    db = d / "state" / "tasks.db"
    c = sqlite3.connect(db)
    c.execute("create table messages (id text primary key, from_agent text, to_agent text, type text, subject text, "
              "body text, status text, created_at text, archived_at text, metadata text, source text)")
    c.execute("create index idx_messages_type on messages(type, status)")
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 600))
    for i in range(mail):
        c.execute("insert into messages values (?,?,?,?,?,?,?,?,?,?,?)",
                  (f"msg_{i}", "gm", agent, "task_request", f"subject {i}", "b", "pending", old, None, "{}", "system"))
    if held_to:
        c.execute("insert into messages values (?,?,?,?,?,?,?,?,?,?,?)",
                  ("msg_held", "operator", held_to, "held_message", None, "look at the failing test", held_status,
                   old, None, json.dumps({"held_for_boundary": True, "deliver_raw": True}), "gateway_hold"))
    c.commit()
    c.close()
    env = {"AQD_TMUX_SESSION": session, "AQD_REGISTRY": str(d / "registry.json"), "AQD_DB": str(db),
           "AQD_MARKER_DIR": str(d / "state"), "AQD_ALLOWLIST": str(d / "state" / "queue-drain-allowlist.json")}
    return env


def _stub(m, monkeypatch, fail=False):
    kicks = []

    def kick(db_path):
        kicks.append(db_path)
        if fail:
            raise OSError("router missing")
    monkeypatch.setattr(m, "_router_kick", kick)
    return kicks


@pytest.mark.parametrize("hook", HOOKS)
def test_a_pending_held_row_for_this_seat_kicks_once_at_the_turn_end(hook, tmp_path, monkeypatch):
    m = _load(hook)
    kicks = _stub(m, monkeypatch)
    env = _seat(tmp_path, held_to="planner-tmux")            # the gateway addresses held rows by tmux session
    assert m.handle({"stop_hook_active": False}, env=env) is None
    assert kicks == [env["AQD_DB"]]


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("held_to", ["planner", "planner-v1"])
def test_the_canonical_id_and_an_alias_count_as_this_seat(hook, tmp_path, monkeypatch, held_to):
    m = _load(hook)
    kicks = _stub(m, monkeypatch)
    env = _seat(tmp_path, held_to=held_to, alias="planner-v1")
    m.handle({}, env=env)
    assert len(kicks) == 1


@pytest.mark.parametrize("hook", HOOKS)
@pytest.mark.parametrize("case", ["none", "other_seat", "delivered"])
def test_no_pending_held_row_for_this_seat_means_no_kick(hook, tmp_path, monkeypatch, case):
    m = _load(hook)
    kicks = _stub(m, monkeypatch)
    env = _seat(tmp_path, held_to={"none": None, "other_seat": "someone-else", "delivered": "planner-tmux"}[case],
                held_status="delivered" if case == "delivered" else "pending", mail=0)
    assert m.handle({}, env=env) is None
    assert kicks == []


@pytest.mark.parametrize("hook", HOOKS)
def test_a_blocking_digest_defers_the_kick_to_the_stop_that_really_ends_the_turn(hook, tmp_path, monkeypatch):
    m = _load(hook)
    kicks = _stub(m, monkeypatch)
    env = _seat(tmp_path, held_to="planner-tmux", mail=2)
    first = m.handle({"stop_hook_active": False}, env=env)
    assert first and first["decision"] == "block" and kicks == []     # the agent keeps going: no pass yet
    assert m.handle({"stop_hook_active": True}, env=env) is None      # now the turn ends
    assert len(kicks) == 1


@pytest.mark.parametrize("hook", HOOKS)
def test_the_digest_is_byte_identical_with_or_without_a_held_row(hook, tmp_path, monkeypatch):
    m = _load(hook)
    _stub(m, monkeypatch)
    a = m.handle({}, env=_seat(tmp_path / "a", held_to="planner-tmux", mail=2))
    b = m.handle({}, env=_seat(tmp_path / "b", mail=2))
    assert a["decision"] == b["decision"] == "block"
    assert a["reason"] == b["reason"] and "msg_held" not in a["reason"]


@pytest.mark.parametrize("hook", HOOKS)
def test_a_failing_kick_leaves_what_the_hook_prints_unchanged(hook, tmp_path, monkeypatch, capsys):
    printed = []
    for fail in (False, True):
        m = _load(hook)
        _stub(m, monkeypatch, fail=fail)
        env = _seat(tmp_path / str(fail), held_to="planner-tmux")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
        assert m.main() == 0
        printed.append(capsys.readouterr())
    assert printed[0].out == printed[1].out == "" and printed[0].err == printed[1].err == ""


@pytest.mark.parametrize("hook", HOOKS)
def test_an_unreadable_db_or_registry_never_raises(hook, tmp_path, monkeypatch):
    m = _load(hook)
    kicks = _stub(m, monkeypatch)
    env = _seat(tmp_path, held_to="planner-tmux")
    assert m.kick_if_held({**env, "AQD_DB": str(tmp_path / "nope.db")}) is False
    assert m.kick_if_held({**env, "AQD_REGISTRY": str(tmp_path / "nope.json")}) is True   # session name alone
    assert len(kicks) == 1


def _fake_router(tmp_path):
    marker = tmp_path / "router-ran"
    script = tmp_path / "fake-router.py"
    script.write_text(f"import pathlib, sys; pathlib.Path({str(marker)!r}).write_text(' '.join(sys.argv[1:]))\n")
    (tmp_path / "logs").mkdir(exist_ok=True)
    return script, marker


def _wait_for(path, s=10.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_the_public_hook_real_kick_starts_a_router_process(tmp_path, monkeypatch):
    """The real _router_kick -> this checkout's msg_store.kick_router_after_send, with only its target resolved to a
    stand-in router: a detached process really starts (the marker file), and the hook does not wait for it."""
    import tempfile
    sys.path.insert(0, str(HERE.parent.parent))
    import msg_store as MS
    script, marker = _fake_router(tmp_path)
    monkeypatch.setattr(MS, "_kick_suppressed", lambda: False)
    monkeypatch.setattr(MS, "_kick_target", lambda db: (tmp_path, script, tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    m = _load(PUBLIC)
    env = _seat(tmp_path / "seat", held_to="planner-tmux")
    t0 = time.monotonic()
    assert m.kick_if_held(env) is True
    assert time.monotonic() - t0 < 5.0
    assert _wait_for(marker) and marker.read_text() == "--cron"


USER_MSG_STORE = Path(os.environ.get("AQD_TEST_USER_MSG_STORE",
                                     os.path.expanduser("~/scripts/agent-orchestra/msg_store.py")))


@pytest.mark.skipif(len(HOOKS) < 2 or not USER_MSG_STORE.is_file(), reason="no user-level hook / old-tree store here")
def test_the_user_hook_real_kick_starts_a_router_process(tmp_path, monkeypatch):
    """The user copy imports ITS tree's msg_store (a fresh hook process would). A scratch copy of that store stands
    in for it, with only its live-store check and command resolved to a stand-in router."""
    import shutil
    tree = tmp_path / "tree"
    tree.mkdir()
    shutil.copy(USER_MSG_STORE, tree / "msg_store.py")
    (tree / "scripts").mkdir()
    for cand in (USER_MSG_STORE.parent / "scripts" / "store_open.py", USER_MSG_STORE.parent / "store_open.py"):
        if cand.is_file():                       # msg_store imports it from <its dir>/scripts
            shutil.copy(cand, tree / "scripts" / "store_open.py")
            break
    monkeypatch.syspath_prepend(str(tree))
    monkeypatch.delitem(sys.modules, "msg_store", raising=False)
    monkeypatch.delitem(sys.modules, "store_open", raising=False)
    import msg_store as OLD
    assert Path(OLD.__file__).parent == tree
    script, marker = _fake_router(tmp_path)
    monkeypatch.setattr(OLD, "_kick_suppressed", lambda: False)
    monkeypatch.setattr(OLD, "_is_live_store", lambda: True)
    monkeypatch.setattr(OLD, "_kick_command", lambda: (
        [sys.executable, str(script), "--cron"], str(tmp_path), dict(os.environ), tmp_path / "logs" / "router.log"))
    m = _load(USER)
    env = _seat(tmp_path / "seat", held_to="planner-tmux")
    assert m.kick_if_held(env) is True
    assert _wait_for(marker) and marker.read_text() == "--cron"


def test_the_public_hook_real_kick_is_a_silent_no_op_under_the_kill_env(tmp_path, monkeypatch, capsys):
    """The real _router_kick (msg_store.kick_router_after_send from this checkout) imports and returns quietly."""
    monkeypatch.setenv("MSG_STORE_NO_KICK", "1")
    m = _load(PUBLIC)
    env = _seat(tmp_path, held_to="planner-tmux")
    t0 = time.monotonic()
    assert m.kick_if_held(env) is True
    assert time.monotonic() - t0 < 5.0
    assert capsys.readouterr().out == ""
