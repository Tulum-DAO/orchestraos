"""A lineage whose runtime is 'service' is never tmux-delivered and never put on a hold path."""
import importlib.util
import inspect
import os
import sqlite3

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_svc",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def _db(tmp_path):
    dbp = str(tmp_path / "orchestra-registry.db")
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE lineages (root TEXT PRIMARY KEY, runtime TEXT NOT NULL)")
    c.execute("INSERT INTO lineages VALUES ('mailbox', 'service')")
    c.execute("INSERT INTO lineages VALUES ('alice', 'claude')")
    c.commit()
    c.close()
    return dbp


def test_a_service_lineage_is_a_service_target(tmp_path):
    assert mr.is_service_target("mailbox", db_path=_db(tmp_path)) is True


def test_an_agent_lineage_is_not(tmp_path):
    dbp = _db(tmp_path)
    assert mr.is_service_target("alice", db_path=dbp) is False
    assert mr.is_service_target("no-such-agent", db_path=dbp) is False


def test_an_unreadable_db_fails_open_to_normal_delivery(tmp_path):
    assert mr.is_service_target("mailbox", db_path=str(tmp_path / "missing.db")) is False
    assert not (tmp_path / "missing.db").exists()       # read-only: never created


def test_default_path_is_the_routers_registry(tmp_path, monkeypatch):
    dbp = _db(tmp_path)
    monkeypatch.setattr(mr, "_registry_db_path", lambda: dbp)
    assert mr.is_service_target("mailbox") is True


def test_one_read_per_agent_per_cycle(tmp_path):
    dbp = _db(tmp_path)
    cache = {}
    assert mr.is_service_target("mailbox", cache=cache, db_path=dbp) is True
    os.remove(dbp)                                    # a second read would now fail open
    assert mr.is_service_target("mailbox", cache=cache, db_path=dbp) is True


def test_the_delivery_loop_decides_service_before_every_hold():
    """STRUCTURAL: main() is one long loop with no seam a test can drive without a real
    router's state. The guard must come before the throttle and every note_hold call."""
    src = inspect.getsource(mr.main)
    g = src.index("is_service_target(_svc_agent")
    for later in ('"attached-active"', '"breathe-window"', '"not-idle"', "inject(session"):
        assert g < src.index(later), later
