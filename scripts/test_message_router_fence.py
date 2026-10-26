"""The log-only pane fence: inject() asks it once per delivery and STILL delivers when it
objects; check_pane() records a violation for a non-canonical pane and never raises.
Scratch registry DBs and a fake tmux only."""
import importlib.util
import os
import sqlite3
import sys

_here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mr_fence", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)
if _here not in sys.path:
    sys.path.insert(0, _here)
from lineage_daemon.wal import fence  # noqa: E402


class _R:
    def __init__(self, out=""):
        self.returncode, self.stdout = 0, out


def test_inject_consults_the_fence_once_and_still_delivers(monkeypatch):
    asked = []
    state = {"pasted": False, "submitted": False}

    def tmux(*a):
        if a[0] == "paste-buffer":
            state["pasted"] = True
        if a[0] == "send-keys" and a[-1] == "Enter" and state["pasted"]:
            state["submitted"] = True
        if a[0] == "capture-pane":
            return _R("\u276f \n")   # an empty prompt line: a composer, nothing typed
        return _R("")

    def pane_split(session, runtime):
        if state["submitted"]:
            return (["hello world probe"], "", True)
        return ([], "hello world probe" if state["pasted"] else "", True)

    monkeypatch.setattr(mr, "tmux", tmux)
    monkeypatch.setattr(mr, "pane_split", pane_split)
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mr, "fence_check",
                        lambda session, **kw: asked.append((session, kw.get("seam"))) or object())
    res = mr.inject("alpha-pane", "hello world probe", "claude")
    assert asked == [("alpha-pane", "router")]
    assert state["pasted"] and res == "submitted"       # log-only: delivery unchanged


def test_fence_check_is_fail_soft(monkeypatch):
    import lineage_daemon.wal.fence as f

    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(f, "check_pane", boom)
    assert mr.fence_check("alpha", seam="router") is None


def _registry(tmp_path):
    os.makedirs(tmp_path / "state", exist_ok=True)
    con = sqlite3.connect(tmp_path / "state" / "orchestra-registry.db")
    con.executescript("""
        CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER,
                                  retired_at TEXT);
        CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT);
        INSERT INTO generations VALUES (1, 'alpha', 1, '2026-01-01'), (2, 'alpha', 2, NULL),
                                       (3, 'alpha', 3, NULL);
        INSERT INTO canonical VALUES ('alpha', 2, 'alpha-g2');
    """)
    con.commit()
    con.close()
    return str(tmp_path)


RETIRED = "alpha-gen" + str(1)   # the long sibling spelling of generation 1


def _violations(d):
    con = sqlite3.connect(os.path.join(d, "state", "orchestra-registry.db"))
    try:
        return con.execute("SELECT seam, session, writer_generation_id FROM fencing_violations").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def test_canonical_pane_is_clean(tmp_path):
    d = _registry(tmp_path)
    assert fence.check_pane("alpha-g2", seam="router", orchestra_dir=d) is None
    assert _violations(d) == []


def test_retired_sibling_pane_is_recorded(tmp_path):
    d = _registry(tmp_path)
    v = fence.check_pane(RETIRED, seam="router", orchestra_dir=d, now=5.0)
    assert v is not None and v.root == "alpha" and v.writer_generation_id == 1
    assert v.canonical_generation_id == 2
    assert _violations(d) == [("router", RETIRED, 1)]


def test_bare_root_and_unknown_sessions_are_counted(tmp_path):
    d = _registry(tmp_path)
    assert fence.check_pane("alpha", seam="router", orchestra_dir=d).writer_generation_id is None
    assert fence.check_pane("nobody", seam="router", orchestra_dir=d).root is None
    assert len(_violations(d)) == 2


def test_provisional_successor_is_allowed_only_when_live(tmp_path):
    d = _registry(tmp_path)
    assert fence.check_pane("alpha-g3", seam="wake", orchestra_dir=d, allow_provisional=True) is None
    assert fence.check_pane("alpha-g1", seam="wake", orchestra_dir=d, allow_provisional=True)


def test_missing_registry_is_not_created(tmp_path):
    assert fence.check_pane("alpha", seam="router", orchestra_dir=str(tmp_path)) is None
    assert not (tmp_path / "state" / "orchestra-registry.db").exists()


def test_a_protected_data_dir_is_never_written_from_a_test(tmp_path, monkeypatch):
    d = _registry(tmp_path)
    from orchestra_cli import settings
    monkeypatch.setenv(settings.PROTECTED_ENV, d)
    assert fence.check_pane(RETIRED, seam="router", orchestra_dir=d) is None
    assert _violations(d) == []
