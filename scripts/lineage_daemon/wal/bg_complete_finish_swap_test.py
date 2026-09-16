"""RED-first tests for BG finish-swap hardening v3 (STEP 1, gm-g60 msg_a39f05d2 / msg_7d2061c2).

The one real autonomous fire (ios-watch-dev g17->g18) committed identity but finished
DEGRADED effects-incomplete: complete_swap reaped Blue's process tree + wrote DRAINED
but NEVER (i) flipped swaps.effects_status='complete', (ii) consolidated green
{root}-gN -> bare {root} + repointed canonical.tmux_session, (iii) stamped
generations.promoted_at, (iv) killed Blue's tmux SESSION.

v3 (post DC COUNTER_PROPOSE + gm by-effect steer): blue's REAL live session is resolved
BY EFFECT from the already-recorded blue_pane_pid (prewarm, bg_state meta) and killed by
that — robust even in a degraded chain where blue is NOT at bare {root}. Kill the husk
BEFORE renaming green {root}-gN -> bare {root} (else duplicate-name livelock). The
the operator-attached defer must NOT flip effects_status='complete' (OQ-1, gm-endorsed).

All new work is opt-in via orchestra_dir presence, so the legacy path is unchanged.
"""
import os
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_complete  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from identity_store import orchestra_db  # noqa: E402

ROOT = "demo-codex-pred"
BLUE_GEN = 6
GREEN_GEN = 7
GREEN_SESSION = f"{ROOT}-g{GREEN_GEN}"
BLUE_PANE_PID = 1001
GREEN_PANE_PID = 2002


class _FakeTmux:
    """Models the tmux world by effect: name -> pane_pid. Records an ordered op log so
    tests can assert kill-before-rename (collision-safety)."""
    def __init__(self, sessions=None, attached=()):
        self.sessions = dict(sessions or {})
        self._attached = set(attached)
        self.ops = []            # ('kill', name) / ('rename', old, new)
        self.killed = []
        self.renamed = []

    def session_exists(self, name):
        return name in self.sessions

    def pane_pid(self, name):
        return self.sessions.get(name)

    def find_session_by_pid(self, pid):
        for name, p in self.sessions.items():
            if p == pid:
                return name
        return None

    def kill_session(self, name):
        self.ops.append(("kill", name))
        self.killed.append(name)
        self.sessions.pop(name, None)   # idempotent: absent = no-op

    def rename_session(self, old, new):
        self.ops.append(("rename", old, new))
        self.renamed.append((old, new))
        if old in self.sessions:
            self.sessions[new] = self.sessions.pop(old)

    def is_attached(self, name):
        return name in self._attached


class _Seams:
    def __init__(self):
        self.reaped = []

    def reap(self, root, blue):
        self.reaped.append((root, blue))


def _make_db(tmp_path):
    orchestra_dir = str(tmp_path)
    os.makedirs(os.path.join(orchestra_dir, "state"), exist_ok=True)
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    orchestra_db.init_db(db)
    conn = orchestra_db.get_connection(db)
    try:
        conn.execute("INSERT INTO lineages (root, tier, runtime) VALUES (?, 'T2', 'claude')",
                     (ROOT,))
        blue_id = conn.execute(
            "INSERT INTO generations (root, generation, model) VALUES (?, ?, 'm')",
            (ROOT, BLUE_GEN)).lastrowid
        green_id = conn.execute(
            "INSERT INTO generations (root, generation, model) VALUES (?, ?, 'm')",
            (ROOT, GREEN_GEN)).lastrowid
        conn.execute(
            "INSERT INTO canonical (root, generation_id, tmux_session, status) "
            "VALUES (?, ?, ?, 'online')", (ROOT, green_id, GREEN_SESSION))
        conn.execute(
            "INSERT INTO swaps (root, blue_generation_id, green_generation_id, "
            "committed_at, effects_status) VALUES (?, ?, ?, '2026-09-14T04:00:00Z', "
            "'effects-incomplete')", (ROOT, blue_id, green_id))
    finally:
        conn.commit()
        conn.close()
    return orchestra_dir, blue_id, green_id


def _degraded(tmp_path, *, blue_pane_pid=BLUE_PANE_PID):
    store = BgStateStore(str(tmp_path), ROOT)
    store.write_state("DEGRADED", reason="effects-incomplete")
    if blue_pane_pid is not None:
        store.write_meta("blue_pane_pid", blue_pane_pid)


def _read(orchestra_dir, sql, args=()):
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    conn = orchestra_db.get_connection(db)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _call(wal, orchestra_dir, blue_id, seams, tmux):
    return bg_complete.complete_swap(
        ROOT, wal_dir=str(wal), seams=seams, blue_generation_id=blue_id,
        orchestra_dir=orchestra_dir, expected_green_generation=GREEN_GEN, tmux_ops=tmux)


def test_finish_swap_full_effects_detached(tmp_path):
    """INV-1: DETACHED green -> promoted_at stamped, effects_status='complete',
    canonical -> bare {root}, blue's real session (recorded pid) killed by EXACT name,
    green renamed to bare, DRAINED."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal)
    # common case: blue at bare {root} (pid 1001), green at {root}-gN (pid 2002)
    tmux = _FakeTmux(sessions={ROOT: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID})
    seams = _Seams()

    out = _call(wal, orchestra_dir, blue_id, seams, tmux)

    assert seams.reaped == [(ROOT, blue_id)]
    assert _read(orchestra_dir, "SELECT effects_status FROM swaps")[0] == "complete"
    assert _read(orchestra_dir, "SELECT promoted_at FROM generations WHERE generation=?",
                 (GREEN_GEN,))[0] is not None
    assert _read(orchestra_dir, "SELECT tmux_session FROM canonical")[0] == ROOT
    assert tmux.killed == [ROOT]                       # blue's real session, exact name
    assert (GREEN_SESSION, ROOT) in tmux.renamed
    assert BgStateStore(str(wal), ROOT).read()["state"] == "DRAINED"
    assert out["state"] == "DRAINED" and out["consolidated"] is True


def test_finish_swap_kills_blue_in_degraded_chain_not_at_bare_root(tmp_path):
    """INV-5 (gm steer): blue is NOT at bare {root} (degraded chain -> {root}-g{blue});
    resolved from recorded blue_pane_pid and killed anyway. A bare-root heuristic would
    have LEAKED this husk."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal)
    blue_session = f"{ROOT}-g{BLUE_GEN}"
    tmux = _FakeTmux(sessions={blue_session: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID})
    _call(wal, orchestra_dir, blue_id, _Seams(), tmux)
    assert blue_session in tmux.killed                 # blue killed wherever named
    assert (GREEN_SESSION, ROOT) in tmux.renamed       # green consolidated to bare


def test_finish_swap_collision_safe_kill_before_rename(tmp_path):
    """INV-5: the blue husk is killed BEFORE the green rename -> no duplicate-name raise."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal)
    tmux = _FakeTmux(sessions={ROOT: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID})
    _call(wal, orchestra_dir, blue_id, _Seams(), tmux)
    assert ("kill", ROOT) in tmux.ops
    assert ("rename", GREEN_SESSION, ROOT) in tmux.ops
    assert tmux.ops.index(("kill", ROOT)) < tmux.ops.index(("rename", GREEN_SESSION, ROOT))


def test_finish_swap_idempotent_already_bare_no_kill_no_rename(tmp_path):
    """INV-2: green already the bare {root} session -> neither kills nor renames."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal, blue_pane_pid=None)   # blue already gone
    tmux = _FakeTmux(sessions={ROOT: GREEN_PANE_PID})   # green already at bare {root}
    _call(wal, orchestra_dir, blue_id, _Seams(), tmux)
    assert tmux.killed == []
    assert tmux.renamed == []
    assert _read(orchestra_dir, "SELECT effects_status FROM swaps")[0] == "complete"


def test_finish_swap_negative_control_effect_raises_never_drains(tmp_path):
    """INV-3: a raising post-commit effect ⇒ effects_status NOT flipped, NOT DRAINED,
    re-raised (RuntimeError specifically, not an earlier/other error)."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal)

    class _Boom(_FakeTmux):
        def rename_session(self, old, new):
            raise RuntimeError("tmux rename blew up")

    tmux = _Boom(sessions={ROOT: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID})
    with pytest.raises(RuntimeError, match="tmux rename blew up"):
        _call(wal, orchestra_dir, blue_id, _Seams(), tmux)
    assert _read(orchestra_dir, "SELECT effects_status FROM swaps")[0] == "effects-incomplete"
    assert BgStateStore(str(wal), ROOT).read()["state"] != "DRAINED"


def test_finish_swap_attached_defers_and_stays_incomplete(tmp_path):
    """INV-4 (OQ-1, gm-endorsed): attached green ⇒ rename+repoint DEFERRED (canonical
    stays {root}-gN, no rename, no raise) AND effects_status stays 'effects-incomplete'
    AND state NOT DRAINED."""
    wal = tmp_path / "wal"; wal.mkdir()
    orchestra_dir, blue_id, _g = _make_db(tmp_path / "orch")
    _degraded(wal)
    tmux = _FakeTmux(sessions={ROOT: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID},
                     attached=(GREEN_SESSION,))
    out = _call(wal, orchestra_dir, blue_id, _Seams(), tmux)

    assert tmux.renamed == []
    assert _read(orchestra_dir, "SELECT tmux_session FROM canonical")[0] == GREEN_SESSION
    assert _read(orchestra_dir, "SELECT effects_status FROM swaps")[0] == "effects-incomplete"
    assert BgStateStore(str(wal), ROOT).read()["state"] != "DRAINED"
    assert out.get("consolidated") is False and out.get("deferred") == "attached"


def test_finish_swap_legacy_no_orchestra_dir_unchanged(tmp_path):
    """INV-6: orchestra_dir is None ⇒ behaves exactly as today (reap -> DRAINED, no DB)."""
    wal = tmp_path / "wal"; wal.mkdir()
    _degraded(wal)
    seams = _Seams()
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(wal), seams=seams, blue_generation_id=BLUE_GEN)
    assert out["reaped"] is True
    assert seams.reaped == [(ROOT, BLUE_GEN)]
    assert BgStateStore(str(wal), ROOT).read()["state"] == "DRAINED"
