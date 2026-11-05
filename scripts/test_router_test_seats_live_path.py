"""The router's test-seat quiet, through the router's REAL main(): a temp registry.json with a test seat, one
router pass, and the set the pass built is the one the page check reads. Plus a guard for the class of bug that
hid this (a name main() calls that is defined nowhere, swallowed at runtime): every global the new headless and
test-seat code reads must exist in its module.

Temp data dir, the router's lock file moved to the temp dir, a fake store and fake tmux reads: nothing touches the
live router, the live store or tmux."""
import builtins
import importlib.util
import json
import sqlite3
import symtable
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


class _Store:
    """The two queries main() runs with no pending mail, on a temp sqlite file."""

    def __init__(self, path):
        self.path = path
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE messages (id TEXT, status TEXT, created_at TEXT, attempted_at TEXT, "
                  "priority TEXT, error TEXT)")
        c.commit()
        c.close()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


@pytest.fixture
def MR(monkeypatch, tmp_path):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("ORCH_DIR", str(data))
    spec = importlib.util.spec_from_file_location("mr_live_path", HERE / "message-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert str(m.REGISTRY).startswith(str(data))            # every state path is under the temp dir
    monkeypatch.setattr(m, "LOCKFILE", tmp_path / "router.lock")
    monkeypatch.setattr(m, "MessageStore", lambda: _Store(str(tmp_path / "messages.db")))
    monkeypatch.setattr(m, "load_env_telegram", lambda: None)
    monkeypatch.setattr(m, "live_sessions", lambda: set())
    monkeypatch.setattr(m, "session_info", lambda: {})
    monkeypatch.setattr(m, "client_presence", lambda: {})
    monkeypatch.setattr(m, "log", lambda *a, **k: None)
    m._data = data
    return m


def test_a_router_pass_builds_the_test_seats_from_the_registry_file(MR, monkeypatch):
    (MR._data / "registry.json").write_text(json.dumps({"agents": {
        "qa-sample-tq": {"test": True, "tmux_session": "qa-sample-tq", "tier": "T2"},
        "dev-demo": {"tier": "T2", "tmux_session": "dev-demo"}}}))
    MR.TEST_SEATS = set()
    MR.main()
    assert MR.TEST_SEATS == {"qa-sample-tq"}
    # ...and the page check reads that set: the test seat is not paged, a real one would be.
    paged = []
    monkeypatch.setattr(MR, "escalation_targets", lambda seat: paged.append(seat) or [])
    monkeypatch.setattr(MR, "_send_page", lambda *a, **k: paged.append(a))
    assert MR.page_seat_down({}, "qa-sample-tq", 1, 1000.0, 900.0, agent="qa-sample-tq") is False
    assert paged == []


def test_an_unreadable_registry_means_no_test_seats(MR):
    (MR._data / "registry.json").write_text("{not json")
    MR.TEST_SEATS = {"stale"}
    MR.main()
    assert MR.TEST_SEATS == set()


def _undefined_globals(path):
    """{function: names it reads as globals that the module never defines and builtins lack}."""
    src = Path(path).read_text()
    mod = symtable.symtable(src, str(path), "exec")
    defined = {s.get_name() for s in mod.get_symbols()
               if s.is_assigned() or s.is_imported() or s.is_namespace()} | {"__file__", "__name__"}
    out = {}

    def walk(t, top):
        for c in t.get_children():
            name = c.get_name() if t is mod else top
            if c.get_type() == "function":
                miss = {g for g in c.get_globals() if g not in defined and not hasattr(builtins, g)}
                if miss:
                    out.setdefault(name, set()).update(miss)
            walk(c, name)
    walk(mod, None)
    return out


# A pre-existing call, left exactly as it is by decision: the charter bumper's provisioning exemption reads it,
# and it has never resolved (dormant). Anything else here is a new name that works only where a test patches it.
KNOWN = {("message-router.py", "main"): {"load_registry"}}


@pytest.mark.parametrize("rel", ["scripts/message-router.py", "scripts/watch_gateway.py", "scripts/agent-status.py",
                                 "scripts/seat_supervisor.py", "scripts/headless_switch.py", "scripts/tier_rule.py",
                                 "orchestra_cli/seats.py", "services/arturo/surface.py",
                                 "services/arturo/relay_live_bridge.py"])
def test_no_global_that_resolves_only_in_tests(rel):
    found = _undefined_globals(ROOT / rel)
    name = Path(rel).name
    left = {fn: names - KNOWN.get((name, fn), set()) for fn, names in found.items()}
    assert {fn: n for fn, n in left.items() if n} == {}
