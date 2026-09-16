#!/usr/bin/env python3
"""
scripts/reconcile_fleet_stores.py — Atomic Fleet Store & Generation Synchronizer

Reconciles metadata across:
  - registry.json
  - state/agent-sessions.json
  - state/agents/<agent_id>.json
  - live tmux processes (Claude hook events and Gemini/Antigravity presence locks)

Ensures that:
  1. Generation numbers are monotonically synchronized to the latest/highest proven generation.
  2. Active session IDs match positively proven live process PIDs/FDs/hook events.
  3. No store falls behind or serves stale metadata to watch_gateway or the UI.

Usage:
  python3 scripts/reconcile_fleet_stores.py [--fix] [--check]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from registry_lock import registry_lock

ORCH_DIR = Path(os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
REGISTRY_PATH = ORCH_DIR / "registry.json"
SESSIONS_PATH = ORCH_DIR / "state" / "agent-sessions.json"
AGENTS_DIR = ORCH_DIR / "state" / "agents"
PANES_DIR = ORCH_DIR / "state" / "agent-events" / "panes"
PRESENCE_DIR = Path.home() / ".gemini" / "antigravity-cli" / "presence"


# --- identity-store cutover seam (INERT until the operator-armed) --------------------
# Under cutover the DB is the single identity truth, so cross-store DRIFT is
# impossible and this reconciler's fixes are obsolete (spec §3). A cheap flag-file
# check (no store import needed) degrades it to report-only under the flag; while
# unarmed it fixes drift byte-identically.
def _cutover_active() -> bool:
    # Resolve the flag path at CALL time from the (test-injectable) ORCH_DIR module
    # global so the check is hermetic against the live armed flag. Cheap flag-FILE
    # check; imports nothing new on the flag-off path.
    return (ORCH_DIR / "state" / "identity-store-cutover.flag").exists() \
        or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"


def _load_json(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def get_live_tmux_sessions() -> set[str]:
    try:
        out = subprocess.check_output(["tmux", "list-sessions", "-F", "#{session_name}"],
                                      text=True, stderr=subprocess.DEVNULL)
        return {line.strip() for line in out.splitlines() if line.strip()}
    except Exception:
        return set()


def get_pane_info(sess: str) -> tuple[str, str]:
    """Returns (pane_id, pane_pid) for tmux session."""
    try:
        out = subprocess.check_output(
            ["tmux", "list-panes", "-t", sess, "-F", "#{pane_id}:#{pane_pid}"],
            text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            first = out.splitlines()[0]
            parts = first.split(":")
            return parts[0], parts[1] if len(parts) > 1 else ""
    except Exception:
        pass
    return "", ""


def get_gemini_live_sid(pane_pid: str) -> str | None:
    """Inspect child processes of pane_pid for open antigravity presence/brain lock targets."""
    if not pane_pid or not pane_pid.isdigit():
        return None
    try:
        child_pids = subprocess.check_output(
            ["pgrep", "-P", pane_pid], text=True, stderr=subprocess.DEVNULL).strip().splitlines()
    except Exception:
        child_pids = []

    for pid in [pane_pid] + child_pids:
        fd_dir = Path(f"/proc/{pid}/fd")
        if not fd_dir.is_dir():
            continue
        try:
            for fd_entry in fd_dir.iterdir():
                try:
                    target = os.readlink(str(fd_entry))
                    m = re.search(r"antigravity-cli/(?:presence|brain)/([0-9a-f-]{36})", target)
                    if m:
                        return m.group(1)
                except OSError:
                    continue
        except OSError:
            continue
    return None


def get_claude_live_sid(pane_id: str) -> str | None:
    """Check agent-events/panes/<pane_id>.json for Claude session_id."""
    clean_id = pane_id.lstrip("%")
    ev_file = PANES_DIR / f"{clean_id}.json"
    if ev_file.is_file():
        try:
            ev = json.loads(ev_file.read_text())
            sid = ev.get("session_id")
            if sid and isinstance(sid, str) and len(sid) >= 10:
                return sid
        except Exception:
            pass
    return None


def reconcile(fix: bool = True) -> dict:
    # cutover: the DB is the single identity truth — cross-store drift is
    # impossible, so degrade to report-only and NEVER write a projector-owned
    # artifact (spec §3). INERT: while unarmed this is a no-op and the fix path
    # below runs byte-identically.
    if fix and _cutover_active():
        fix = False
    with registry_lock():
        reg = _load_json(REGISTRY_PATH)
        sess_store = _load_json(SESSIONS_PATH)
        agents_reg = reg.get("agents", {}) if isinstance(reg, dict) else {}
        live_sessions = get_live_tmux_sessions()

        all_agent_ids = set(agents_reg.keys()) | set(sess_store.keys())
        for f in AGENTS_DIR.glob("*.json"):
            all_agent_ids.add(f.stem)

        changes = []
        reg_changed = False
        sess_changed = False

        for agent_id in sorted(all_agent_ids):
            state_file = AGENTS_DIR / f"{agent_id}.json"
            state_data = _load_json(state_file)
            r_entry = agents_reg.get(agent_id, {})
            s_entry = sess_store.get(agent_id, {})

            tmux_sess = r_entry.get("tmux_session") or s_entry.get("tmux_session") or agent_id
            is_live = tmux_sess in live_sessions

            # 1. Resolve generation
            gens = [
                g for g in [
                    r_entry.get("generation"),
                    s_entry.get("generation"),
                    state_data.get("generation")
                ] if isinstance(g, int) and g >= 1
            ]
            max_gen = max(gens) if gens else None

            if max_gen is not None:
                if r_entry and r_entry.get("generation") != max_gen:
                    changes.append(f"[{agent_id}] registry gen: {r_entry.get('generation')} -> {max_gen}")
                    if fix:
                        r_entry["generation"] = max_gen
                        reg_changed = True
                if s_entry and s_entry.get("generation") != max_gen:
                    changes.append(f"[{agent_id}] sessions gen: {s_entry.get('generation')} -> {max_gen}")
                    if fix:
                        s_entry["generation"] = max_gen
                        sess_changed = True
                if state_data and state_data.get("generation") != max_gen:
                    changes.append(f"[{agent_id}] state gen: {state_data.get('generation')} -> {max_gen}")
                    if fix:
                        state_data["generation"] = max_gen
                        _save_json(state_file, state_data)

            # 2. Positive live SID attribution
            live_sid = None
            if is_live:
                pane_id, pane_pid = get_pane_info(tmux_sess)
                # Check Gemini first
                live_sid = get_gemini_live_sid(pane_pid)
                if not live_sid:
                    live_sid = get_claude_live_sid(pane_id)

            if live_sid:
                if r_entry and r_entry.get("session_id") != live_sid:
                    changes.append(f"[{agent_id}] registry sid: {r_entry.get('session_id')} -> {live_sid}")
                    if fix:
                        r_entry["session_id"] = live_sid
                        reg_changed = True
                if s_entry and s_entry.get("session_id") != live_sid:
                    changes.append(f"[{agent_id}] sessions sid: {s_entry.get('session_id')} -> {live_sid}")
                    if fix:
                        s_entry["session_id"] = live_sid
                        sess_changed = True
                if state_data and state_data.get("session_id") != live_sid:
                    changes.append(f"[{agent_id}] state sid: {state_data.get('session_id')} -> {live_sid}")
                    if fix:
                        state_data["session_id"] = live_sid
                        _save_json(state_file, state_data)

        if fix:
            if reg_changed:
                reg["agents"] = agents_reg
                _save_json(REGISTRY_PATH, reg)
            if sess_changed:
                _save_json(SESSIONS_PATH, sess_store)

        return {
            "changes_count": len(changes),
            "changes": changes,
            "fixed": fix
        }


def main():
    parser = argparse.ArgumentParser(description="Reconcile agent generation and session IDs across stores")
    parser.add_argument("--check", action="store_true", help="Report discrepancies without modifying files")
    parser.add_argument("--fix", action="store_true", default=True, help="Automatically fix discrepancies (default)")
    args = parser.parse_args()

    fix_mode = not args.check
    res = reconcile(fix=fix_mode)
    if res["changes_count"] == 0:
        print("All stores and live sessions are fully synchronized.")
    else:
        action = "Fixed" if res["fixed"] else "Found"
        print(f"{action} {res['changes_count']} discrepancies across fleet stores:")
        for ch in res["changes"]:
            print(f"  • {ch}")


if __name__ == "__main__":
    main()
