"""R4 Stage-1 DETECT-ONLY recovery-roster shadow (DEC-1788691687768192).

Observes what a DB-first agent-recovery roster WOULD look like and diffs it
against the EXACT legacy roster (agent-recovery.sh --print-roster — the legacy
guards stay verbatim in the bash; this module re-implements nothing of them).
It only ever writes reports under state/recovery-shadow/. It cannot respawn,
inject, or touch tmux BY CONSTRUCTION: no subprocess, no os.system, and the
zero-spawn property is asserted by test_zero_spawn_capability.

Binding conditions (gm ruling msg_fb0c05c8):
  #1 fail-open ALARMED: a DB read failure falls open (cycle still completes on
     legacy data) but bumps a consecutive-failure counter; at FAIL_OPEN_ALARM_N
     an escalation file is written for pulse to pick up. Success resets to 0.
  #2 stale-online filter: canonical 'online' with no evidence (no resumable
     sessions row with a sid, no live tmux) classifies STALE_DB_ONLINE — never
     a recovery candidate. Active-status allowlist carries over (only
     online/active are candidates; quiescent stays parked).
  #3 evidence-but-no-sid classifies WOULD_CARD — a crash card is the only
     permitted action for such a seat, never a blind spawn (acting on it is
     Stage-1 ARMED scope, not detect-only).
  #4 never-respawn-live-pane: detect-only performs no actions at all.
  INERT until state/RECOVERY_SHADOW_ARMED exists — creating it is gm-gated.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

ARM_FLAG = "RECOVERY_SHADOW_ARMED"
FAIL_OPEN_ALARM_N = 5

# Statuses whose canonical rows are auto-recovery candidates — mirrors the
# active-status allowlist in agent-recovery.sh (quiescent = parked-resumable,
# manual resume only; missing status on a DB row is NOT fail-open here: the DB
# always stamps status, unlike legacy flat rows).
CANDIDATE_STATUSES = ("online", "active")


def _load_sessions(state: Path) -> dict:
    try:
        with open(state / "agent-sessions.json") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _db_rows(db_path: Path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT c.root, c.status, c.tmux_session, g.session_id "
            "FROM canonical c LEFT JOIN generations g ON g.id = c.generation_id"
        ).fetchall()
    finally:
        conn.close()
    return rows


def classify_db_roster(rows, sessions: dict, live_tmux: set):
    """Classify each DB canonical row. Returns (candidates, classified) where
    candidates = roots the DB-first roster would consider recoverable, and
    classified = {root: class} for the non-candidate/evidence classes."""
    candidates = set()
    alive_set = set()
    classified = {}
    for root, status, tmux_session, gen_sid in rows:
        if (status or "") not in CANDIDATE_STATUSES:
            continue  # parked/retired/held rows are never auto-candidates
        sess = sessions.get(root) if isinstance(sessions.get(root), dict) else None
        sess_sid = (sess or {}).get("session_id") or gen_sid
        resumable = bool((sess or {}).get("resumable"))
        alive = (tmux_session or root) in live_tmux or root in live_tmux
        if alive:
            # First-armed-cycle lesson: alive = nothing to recover on EITHER
            # path — its own bucket, never a divergence (legacy's cron-mode
            # roster is empty by recency design, that's not a legacy blind spot).
            alive_set.add(root)
        elif resumable and sess_sid:
            candidates.add(root)
        elif resumable and not sess_sid:
            classified[root] = "WOULD_CARD"  # cond #3: card, never blind-spawn
        else:
            classified[root] = "STALE_DB_ONLINE"  # cond #2: no evidence
    return candidates, alive_set, classified


def run_cycle(orchestra_dir: str, legacy_roster, live_tmux_sessions: set):
    """One detect-only cycle. legacy_roster = agent ids from
    agent-recovery.sh --print-roster (first |-field per line, pre-split by the
    caller or plain ids). Returns a summary dict; writes reports when armed."""
    orch = Path(orchestra_dir)
    state = orch / "state"
    out_dir = state / "recovery-shadow"

    if not (state / ARM_FLAG).exists():
        return {"ran": False, "reason": "not armed"}

    out_dir.mkdir(parents=True, exist_ok=True)
    counter_file = out_dir / "fail-open-count"
    legacy = {str(x).split("|")[0] for x in legacy_roster if str(x).strip()}

    fail_open = False
    db_candidates: set = set()
    alive_set: set = set()
    classified: dict = {}
    try:
        rows = _db_rows(state / "orchestra-registry.db")
        sessions = _load_sessions(state)
        db_candidates, alive_set, classified = classify_db_roster(
            rows, sessions, set(live_tmux_sessions))
        counter_file.write_text("0")
    except Exception as e:
        fail_open = True
        try:
            n = int(counter_file.read_text().strip())
        except Exception:
            n = 0
        n += 1
        counter_file.write_text(str(n))
        if n >= FAIL_OPEN_ALARM_N:
            # Condition #1: alarmed, never silent. Pulse picks this file up.
            (out_dir / "escalation.json").write_text(json.dumps({
                "kind": "recovery_shadow_fail_open",
                "consecutive_failures": n,
                "alarm_threshold": FAIL_OPEN_ALARM_N,
                "error": repr(e),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "action_required": "identity DB unreadable for the recovery "
                "shadow — DB-only seats are re-hidden from recovery observation",
            }, indent=2))

    divergences = []
    if not fail_open:
        for root in sorted(db_candidates - legacy):
            divergences.append({"root": root, "class": "DB_ONLY_RECOVERABLE"})
        for root in sorted(legacy - db_candidates):
            divergences.append({"root": root, "class": "LEGACY_ONLY"})
        for root, cls in sorted(classified.items()):
            divergences.append({"root": root, "class": cls})

    summary = {
        "ran": True,
        "fail_open": fail_open,
        "agree": sorted(db_candidates & legacy),
        "alive": sorted(alive_set),
        "divergences": divergences,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(out_dir / "divergence.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")
    (out_dir / "latest.json").write_text(json.dumps(summary, indent=2))
    return summary
