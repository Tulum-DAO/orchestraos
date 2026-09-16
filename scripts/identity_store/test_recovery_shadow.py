"""R4 Stage-1 DETECT-ONLY shadow — RED-first suite (DEC-1788691687768192, gm ruling
msg_fb0c05c8: 2-leg detect-only GO, 7 binding conditions).

Contract under test (scripts/identity_store/recovery_shadow.py):
- INERT unless the arm flag exists (state/RECOVERY_SHADOW_ARMED — gm creates it).
- Pure diff: classify seats across the DB-first roster and the EXACT legacy roster
  (passed in, produced by agent-recovery.sh --print-roster — guards stay verbatim).
- Condition 2: DB canonical 'online' with NO evidence (no resumable+sid sessions row,
  no live tmux) = STALE_DB_ONLINE, never a recovery candidate.
- Condition 3: DB canonical with evidence-but-no-sid = WOULD_CARD, never a spawn.
- Condition 1: DB read failure = fail-open + consecutive-failure counter; at
  FAIL_OPEN_ALARM_N the shadow writes a pulse escalation file. Success resets.
- Condition 4: the shadow NEVER touches tmux/spawn — the module must not even
  import subprocess/os.system paths for it; run() takes rosters as data.
- Detect-only output: JSONL divergence records + latest.json summary per cycle.

Run: cd ~/scripts/agent-orchestra && python3 -m pytest scripts/identity_store/test_recovery_shadow.py -q
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ORCH = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

from identity_store import recovery_shadow as rs  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    db = state / "orchestra-registry.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, machine TEXT);
        CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INT,
                                  session_id TEXT, model TEXT);
        CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INT,
                                tmux_session TEXT, status TEXT);
        """
    )
    conn.commit()
    conn.close()
    sessions = {}
    return {"orch": tmp_path, "state": state, "db": db, "sessions": sessions}


def _seed(sandbox, root, status="online", sid=None, tmux=None, sess_row=None):
    conn = sqlite3.connect(sandbox["db"])
    conn.execute("INSERT INTO lineages VALUES (?,?,?,?)", (root, "T3", "claude", "vps"))
    cur = conn.execute(
        "INSERT INTO generations (root, generation, session_id, model) VALUES (?,?,?,?)",
        (root, 1, sid, "claude-fable-5[1m]"))
    conn.execute("INSERT INTO canonical VALUES (?,?,?,?)",
                 (root, cur.lastrowid, tmux or root, status))
    conn.commit(); conn.close()
    if sess_row is not None:
        sandbox["sessions"][root] = sess_row


def _run(sandbox, legacy, live_tmux=(), armed=True):
    if armed:
        (sandbox["state"] / "RECOVERY_SHADOW_ARMED").touch()
    sessions_file = sandbox["state"] / "agent-sessions.json"
    sessions_file.write_text(json.dumps(sandbox["sessions"]))
    return rs.run_cycle(
        orchestra_dir=str(sandbox["orch"]),
        legacy_roster=legacy,
        live_tmux_sessions=set(live_tmux),
    )


def test_inert_without_arm_flag(sandbox):
    res = _run(sandbox, legacy=[], armed=False)
    assert res["ran"] is False
    assert not (sandbox["state"] / "recovery-shadow").exists()


def test_db_only_recoverable_seat_detected(sandbox):
    # The live incident class: adopted DB-only seat with a resumable sessions row,
    # absent from flat registry so the legacy roster misses it entirely.
    _seed(sandbox, "marketerx-like", sid="sid-1",
          sess_row={"resumable": True, "session_id": "sid-1", "machine": "vps"})
    res = _run(sandbox, legacy=[])
    assert res["ran"] is True
    kinds = {d["root"]: d["class"] for d in res["divergences"]}
    assert kinds.get("marketerx-like") == "DB_ONLY_RECOVERABLE"


def test_db_no_sid_would_card_never_spawn(sandbox):
    _seed(sandbox, "no-sid-seat", sid=None,
          sess_row={"resumable": True, "machine": "vps"})
    res = _run(sandbox, legacy=[])
    kinds = {d["root"]: d["class"] for d in res["divergences"]}
    assert kinds.get("no-sid-seat") == "WOULD_CARD"


def test_stale_db_online_filtered(sandbox):
    # canonical says online, but NO sessions evidence and NO live tmux -> stale.
    _seed(sandbox, "stale-online-seat", sid="sid-2", sess_row=None)
    res = _run(sandbox, legacy=[])
    kinds = {d["root"]: d["class"] for d in res["divergences"]}
    assert kinds.get("stale-online-seat") == "STALE_DB_ONLINE"


def test_live_tmux_seat_is_alive_never_a_divergence(sandbox):
    # First-armed-cycle lesson (2026-09-06 07:02): in cron mode the legacy
    # roster is empty (30-min recency cutoff), so a live healthy seat MUST NOT
    # classify DB_ONLY_RECOVERABLE — alive seats go to their own bucket
    # regardless of what legacy prints; nothing needs recovering.
    _seed(sandbox, "live-seat", sid="sid-3",
          sess_row={"resumable": True, "session_id": "sid-3", "machine": "vps"})
    res = _run(sandbox, legacy=[], live_tmux=["live-seat"])
    assert "live-seat" in res["alive"]
    assert all(d["root"] != "live-seat" for d in res["divergences"])


def test_db_only_recoverable_requires_not_alive(sandbox):
    # The true gap signal: NOT running, resume evidence present, invisible to
    # legacy. A dead seat with evidence diverges; the same seat alive does not.
    _seed(sandbox, "dead-seat", sid="sid-9",
          sess_row={"resumable": True, "session_id": "sid-9", "machine": "vps"})
    res = _run(sandbox, legacy=[])
    kinds = {d["root"]: d["class"] for d in res["divergences"]}
    assert kinds.get("dead-seat") == "DB_ONLY_RECOVERABLE"


def test_legacy_only_seat_flags_projection_gap(sandbox):
    # Legacy would recover it but the DB has no canonical row: DB gap, must be
    # surfaced, never silently dropped.
    res = _run(sandbox, legacy=["flat-only-seat"])
    kinds = {d["root"]: d["class"] for d in res["divergences"]}
    assert kinds.get("flat-only-seat") == "LEGACY_ONLY"


def test_quiescent_status_not_a_candidate(sandbox):
    # Active-status allowlist carries over: quiescent = parked-resumable, manual only.
    _seed(sandbox, "parked-seat", status="quiescent", sid="sid-4",
          sess_row={"resumable": True, "session_id": "sid-4", "machine": "vps"})
    res = _run(sandbox, legacy=[])
    assert all(d["root"] != "parked-seat" for d in res["divergences"])


def test_fail_open_counter_and_alarm(sandbox):
    (sandbox["state"] / "RECOVERY_SHADOW_ARMED").touch()
    os.remove(sandbox["db"])  # DB unreadable -> fail-open path
    sessions_file = sandbox["state"] / "agent-sessions.json"
    sessions_file.write_text("{}")
    esc = sandbox["state"] / "recovery-shadow" / "escalation.json"
    for i in range(rs.FAIL_OPEN_ALARM_N):
        res = rs.run_cycle(orchestra_dir=str(sandbox["orch"]),
                           legacy_roster=[], live_tmux_sessions=set())
        assert res["ran"] is True and res["fail_open"] is True
    assert esc.exists(), "escalation must fire at FAIL_OPEN_ALARM_N consecutive fail-opens"
    data = json.loads(esc.read_text())
    assert data["consecutive_failures"] >= rs.FAIL_OPEN_ALARM_N


def test_fail_open_counter_resets_on_success(sandbox):
    counter = sandbox["state"] / "recovery-shadow" / "fail-open-count"
    (sandbox["state"] / "recovery-shadow").mkdir()
    counter.write_text(str(rs.FAIL_OPEN_ALARM_N - 1))
    _seed(sandbox, "ok-seat", sid="s", sess_row={"resumable": True, "session_id": "s", "machine": "vps"})
    _run(sandbox, legacy=[])
    assert counter.read_text().strip() == "0"


def test_zero_spawn_capability():
    # Condition 4 at the module level: the shadow cannot spawn. Check the AST
    # (imports + attribute calls), not raw text — docstrings legitimately
    # DESCRIBE the ban (the watchdog T9 lesson: comments trip naive greps).
    import ast
    tree = ast.parse((HERE / "recovery_shadow.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) \
                else [node.module or ""]
            for n in names:
                assert not n.startswith(("subprocess", "pty", "pexpect")), \
                    f"detect-only module imports spawn-capable {n!r}"
        if isinstance(node, ast.Attribute) and node.attr in ("system", "popen", "spawn", "exec"):
            assert False, f"detect-only module calls .{node.attr}()"


def test_agent_recovery_print_roster_mode():
    # agent-recovery.sh --print-roster prints the legacy roster and exits without
    # recovering anything (the shadow's verbatim-guard legacy source).
    script = ORCH / "scripts" / "agent-recovery.sh"
    assert "--print-roster" in script.read_text(), "print-roster mode missing"
    out = subprocess.run(["bash", str(script), "--print-roster"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    for line in out.stdout.strip().splitlines():
        if line:
            assert line.count("|") >= 3, f"roster line shape: {line!r}"
