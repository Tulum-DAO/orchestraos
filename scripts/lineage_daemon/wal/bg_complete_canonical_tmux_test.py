"""After a blue-green swap the flat payloads must name the LIVE pane, not the green's boot alias.

message-router.py resolves a seat's pane from state/agent-sessions.json ``tmux_session``, falling
back to registry.json, and never reads ``canonical``. A green's documents are written at spawn and
name ``<root>-g<N>``. The background swap (bg_complete) renames the pane to the bare root and
repoints canonical but writes no document, so unless the projection takes ``tmux_session`` from
canonical the router targets a session that no longer exists: the seat's messages are held and
nothing alarms. These tests run the real swap completion and read the projected files.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_complete                      # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore            # noqa: E402
from identity_store import orchestra_db, projector, shims       # noqa: E402

ROOT = "swap-seat"
BLUE_GEN = 4
GREEN_GEN = 5
GREEN_SESSION = f"{ROOT}-g{GREEN_GEN}"
GREEN_SID = "sid-green-5"
RESUME = "claude --resume sid-green-5"
BLUE_PANE_PID = 3001
GREEN_PANE_PID = 4002


class _FakeTmux:
    """tmux by effect: name -> pane_pid (same model as bg_complete_finish_swap_test)."""
    def __init__(self, sessions=None, attached=()):
        self.sessions = dict(sessions or {})
        self._attached = set(attached)
        self.ops = []

    def session_exists(self, name):
        return name in self.sessions

    def pane_pid(self, name):
        return self.sessions.get(name)

    def find_session_by_pid(self, pid):
        for n, p in self.sessions.items():
            if p == pid:
                return n
        return None

    def kill_session(self, name):
        self.ops.append(("kill", name))
        self.sessions.pop(name, None)

    def rename_session(self, old, new):
        self.ops.append(("rename", old, new))
        if old in self.sessions:
            self.sessions[new] = self.sessions.pop(old)

    def is_attached(self, name):
        return name in self._attached


class _Seams:
    def __init__(self):
        self.reaped = []

    def reap(self, root, blue):
        self.reaped.append((root, blue))


def _make_world(tmp_path):
    """A scratch orchestra dir mid-swap, with the stored documents a real spawn leaves
    behind: both naming the green's boot alias, which is the whole defect."""
    orchestra_dir = str(tmp_path)
    os.makedirs(os.path.join(orchestra_dir, "state"), exist_ok=True)
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    orchestra_db.init_db(db)
    conn = orchestra_db.get_connection(db)
    try:
        conn.execute("INSERT INTO lineages (root, tier, runtime, always_on, machine, cwd) "
                     "VALUES (?, 'T1', 'claude', 1, 'vps', '/tmp/x')", (ROOT,))
        blue_id = conn.execute(
            "INSERT INTO generations (root, generation, model, session_id) "
            "VALUES (?, ?, 'claude-opus-5', 'sid-blue-4')", (ROOT, BLUE_GEN)).lastrowid
        green_id = conn.execute(
            "INSERT INTO generations (root, generation, model, session_id, resume_command) "
            "VALUES (?, ?, 'claude-opus-5', ?, ?)",
            (ROOT, GREEN_GEN, GREEN_SID, RESUME)).lastrowid
        # canonical already points at the green and STILL carries the boot alias — this is
        # the state execute_swap leaves before consolidation.
        conn.execute("INSERT INTO canonical (root, generation_id, tmux_session, status) "
                     "VALUES (?, ?, ?, 'online')", (ROOT, green_id, GREEN_SESSION))
        conn.execute(
            "INSERT INTO swaps (root, blue_generation_id, green_generation_id, "
            "committed_at, effects_status) VALUES (?, ?, ?, '2026-09-25T18:41:19Z', "
            "'effects-incomplete')", (ROOT, blue_id, green_id))
        # the stored documents: a spawn writes tmux_session at boot, so they name the alias
        shims.upsert_document(conn, "registry.json", "agent", ROOT, {
            "name": ROOT, "lineage_root": ROOT, "tier": "T1", "runtime": "claude",
            "machine": "vps", "cwd": "/tmp/x", "always_on": False,
            "tmux_session": GREEN_SESSION, "session_id": GREEN_SID,
            "generation": GREEN_GEN, "status": "online", "model": "claude-opus-5"})
        shims.upsert_document(conn, "agent-sessions.json", "session", ROOT, {
            "name": ROOT, "lineage_root": ROOT, "tmux_session": GREEN_SESSION,
            "session_id": GREEN_SID, "generation": GREEN_GEN, "status": "online",
            "resume_command": RESUME, "model": "claude-opus-5"})
        conn.commit()
    finally:
        conn.close()
    return orchestra_dir, blue_id, green_id


def _run_full_bg_swap(tmp_path, orchestra_dir, blue_id, *, attached=()):
    wal = tmp_path / "wal"
    wal.mkdir(exist_ok=True)
    store = BgStateStore(str(wal), ROOT)
    store.write_state("DEGRADED", reason="effects-incomplete")
    store.write_meta("blue_pane_pid", BLUE_PANE_PID)
    tmux = _FakeTmux(sessions={ROOT: BLUE_PANE_PID, GREEN_SESSION: GREEN_PANE_PID},
                     attached=attached)
    out = bg_complete.complete_swap(
        ROOT, wal_dir=str(wal), seams=_Seams(), blue_generation_id=blue_id,
        orchestra_dir=orchestra_dir, expected_green_generation=GREEN_GEN, tmux_ops=tmux)
    return out, tmux


def _project(orchestra_dir, out_dir):
    """Produce the two flat files the router reads, from the DB, as the projector does:
    the assertions are on what the router sees, not on an intermediate."""
    db = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    conn = orchestra_db.get_connection(db)
    try:
        projector.project_faithful(conn, out_dir)
    finally:
        conn.close()
    reg = json.load(open(os.path.join(out_dir, "registry.json")))
    ses = json.load(open(os.path.join(out_dir, "state", "agent-sessions.json")))
    return reg, ses


def test_after_a_full_swap_both_flat_payloads_name_the_live_session(tmp_path):
    orchestra_dir, blue_id, _g = _make_world(tmp_path / "orch")
    out, tmux = _run_full_bg_swap(tmp_path, orchestra_dir, blue_id)
    assert out["consolidated"] is True
    assert ROOT in tmux.sessions and GREEN_SESSION not in tmux.sessions
    reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert ses[ROOT]["tmux_session"] == ROOT, (
        f"agent-sessions.json names the dead alias {ses[ROOT]['tmux_session']!r}: the router "
        "reads this file first, so the seat would be silent")
    assert reg["agents"][ROOT]["tmux_session"] == ROOT, (
        f"registry.json names the dead alias {reg['agents'][ROOT]['tmux_session']!r} "
        "(the router's fallback)")


def test_the_promoted_generation_identity_survives_the_swap(tmp_path):
    orchestra_dir, blue_id, _g = _make_world(tmp_path / "orch")
    _run_full_bg_swap(tmp_path, orchestra_dir, blue_id)
    _reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert ses[ROOT]["session_id"] == GREEN_SID
    assert ses[ROOT]["resume_command"] == RESUME


def test_the_repoint_only_path_also_lands_the_flat_payload(tmp_path):
    """The green pane is already at the bare root (nothing to rename): canonical is repointed and
    the swap returns early. The flat payload must follow it just the same."""
    orchestra_dir, blue_id, _g = _make_world(tmp_path / "orch")
    wal = tmp_path / "wal"
    wal.mkdir(exist_ok=True)
    store = BgStateStore(str(wal), ROOT)
    store.write_state("DEGRADED", reason="effects-incomplete")
    store.write_meta("blue_pane_pid", BLUE_PANE_PID)
    tmux = _FakeTmux(sessions={ROOT: GREEN_PANE_PID})
    bg_complete.complete_swap(
        ROOT, wal_dir=str(wal), seams=_Seams(), blue_generation_id=blue_id,
        orchestra_dir=orchestra_dir, expected_green_generation=GREEN_GEN, tmux_ops=tmux)
    reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert ses[ROOT]["tmux_session"] == ROOT
    assert reg["agents"][ROOT]["tmux_session"] == ROOT


def test_an_attached_green_the_flat_payload_names_the_session_that_exists(tmp_path):
    """Whether or not the swap consolidates an attached green, both files must name the pane
    that actually exists, and agree with canonical."""
    orchestra_dir, blue_id, _g = _make_world(tmp_path / "orch")
    _out, tmux = _run_full_bg_swap(tmp_path, orchestra_dir, blue_id, attached=(GREEN_SESSION,))
    conn = orchestra_db.get_connection(os.path.join(orchestra_dir, "state", "orchestra-registry.db"))
    try:
        canon = conn.execute("SELECT tmux_session FROM canonical WHERE root=?", (ROOT,)).fetchone()[0]
    finally:
        conn.close()
    reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert tmux.pane_pid(canon) == GREEN_PANE_PID, (canon, tmux.sessions)
    assert ses[ROOT]["tmux_session"] == canon
    assert reg["agents"][ROOT]["tmux_session"] == canon


def _rewrite_docs_without_tmux_session(orchestra_dir):
    conn = orchestra_db.get_connection(os.path.join(orchestra_dir, "state", "orchestra-registry.db"))
    try:
        shims.upsert_document(conn, "agent-sessions.json", "session", ROOT, {
            "name": ROOT, "lineage_root": ROOT, "session_id": GREEN_SID,
            "generation": GREEN_GEN, "status": "online", "model": "claude-opus-5"})
        shims.upsert_document(conn, "registry.json", "agent", ROOT, {
            "name": ROOT, "lineage_root": ROOT, "tier": "T1", "runtime": "claude",
            "session_id": GREEN_SID, "generation": GREEN_GEN, "status": "online"})
        conn.commit()
    finally:
        conn.close()


def test_a_document_that_never_carried_tmux_session_does_not_gain_one(tmp_path):
    """Override, never add: correct what a document states, add nothing it never had."""
    orchestra_dir, blue_id, _g = _make_world(tmp_path / "orch")
    _rewrite_docs_without_tmux_session(orchestra_dir)
    _run_full_bg_swap(tmp_path, orchestra_dir, blue_id)
    reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert "tmux_session" not in ses[ROOT]
    assert "tmux_session" not in reg["agents"][ROOT]


def test_absent_or_null_canonical_authority_serves_the_document_unchanged():
    """Absent authority and wrong authority must not share a path: a missing or NULL
    canonical.tmux_session never blanks a stated value."""
    doc = {"name": "x", "tmux_session": "x-g9"}
    assert projector._canonical_authoritative_tmux_session(doc, None) == doc
    assert projector._canonical_authoritative_tmux_session(doc, {"tmux_session": None}) == doc
    assert projector._canonical_authoritative_tmux_session(doc, {"tmux_session": ""}) == doc
    assert projector._canonical_authoritative_tmux_session({"name": "x"}, {"tmux_session": "x"}) == {"name": "x"}
    assert projector._canonical_authoritative_tmux_session(doc, {"tmux_session": "x"})["tmux_session"] == "x"


def test_a_session_doc_whose_canonical_generation_row_is_missing_still_follows_canonical(tmp_path):
    """The sessions projection's second branch: canonical points at a generation id with no row."""
    orchestra_dir, _blue_id, _g = _make_world(tmp_path / "orch")
    conn = orchestra_db.get_connection(os.path.join(orchestra_dir, "state", "orchestra-registry.db"))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("UPDATE canonical SET generation_id=?, tmux_session=? WHERE root=?", (999999, ROOT, ROOT))
        conn.commit()
    finally:
        conn.close()
    reg, ses = _project(orchestra_dir, str(tmp_path / "proj"))
    assert ses[ROOT]["tmux_session"] == ROOT
    assert reg["agents"][ROOT]["tmux_session"] == ROOT


def test_a_non_dict_document_is_served_unchanged():
    assert projector._canonical_authoritative_tmux_session(None, {"tmux_session": "x"}) is None
