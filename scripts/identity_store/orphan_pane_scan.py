#!/usr/bin/env python3
"""orphan_pane_scan.py — Leg B raw-spawn BACKSTOP (gm msg_b76ddb60).

Leg A (spawn_adopt.py) guards only spawns that go through spawn-agent.sh. A raw
`tmux new-session + claude` bypasses everything and comes up with ZERO identity
rows — invisible to routing/recovery, orphan/dual-chip on the operator's surface. This
scanner is the beat-driven detector for that route-around:

    live AGENT-CLI pane (claude/agy/gemini/codex child) whose tmux session name
    resolves to NO identity-store lineage  ->  ALERT gm (chip), debounced.

HARD RULES: chip-only — this module NEVER terminates anything (a false positive
reaping a live seat is worse than the orphan; gm decides). Detection scope is
UNKNOWN LINEAGES: a `<root>-g<N>`/`<root>-gen<N>` alias of a KNOWN lineage is
never flagged (provisional/mid-rotation shapes are legitimate).

INERT until armed: no cron entry ships with this file — gm wires the beat at its
gate. Run ad hoc:  python3 scripts/identity_store/orphan_pane_scan.py [--dry-run]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.identity_store import orchestra_db  # noqa: E402

# Agent-CLI basenames: only these child processes make a pane "agent-like".
# Service panes (python3 watch_gateway.py, node dashboard-proxy.js, bare
# shells) never flag regardless of session name.
AGENT_CLI_BASENAMES = ("claude", "agy", "gemini", "codex")

# Re-alert cadence for a STILL-LIVE orphan (debounce; chip, not a siren).
ALERT_TTL_S = 6 * 3600

_GEN_ALIAS = re.compile(r"^(?P<root>.+?)-g(?:en)?\d+$")


def _cmd_is_agent_cli(cmdline: str) -> bool:
    if not cmdline:
        return False
    head = cmdline.split()[0]
    base = os.path.basename(head)
    return base in AGENT_CLI_BASENAMES


def _known_lineages(db_path: str) -> set:
    conn = orchestra_db.get_connection(db_path)
    try:
        roots = {r["root"] for r in conn.execute("SELECT root FROM lineages")}
        roots |= {r["root"] for r in conn.execute("SELECT root FROM canonical")}
        roots |= {r["root"] for r in conn.execute("SELECT root FROM generations")}
    finally:
        conn.close()
    return roots


def _resolves(session: str, roots: set) -> bool:
    if session in roots:
        return True
    m = _GEN_ALIAS.match(session)
    return bool(m and m.group("root") in roots)


_EPHEMERAL_PREFIXES = ("research-", "subagent-", "test-", "tmp-", "scratch-", "vchk-")


def find_orphans(panes, db_path, ignore=None):
    """Pure classifier. `panes` = [{"session": name, "child_cmdline": str}].
    Returns the subset that are agent-CLI panes with no known lineage."""
    ignore = ignore or set()
    roots = _known_lineages(db_path)
    out = []
    for p in panes:
        name = p.get("session", "")
        if not name or name in ignore:
            continue
        if any(name.startswith(pfx) for pfx in _EPHEMERAL_PREFIXES):
            continue
        if not _cmd_is_agent_cli(p.get("child_cmdline", "")):
            continue
        if _resolves(name, roots):
            continue
        out.append(p)
    return out


def alert_orphans(orphans, *, state_dir, send, now=None):
    """Debounced emission: at most one alert per session per ALERT_TTL_S.
    `send` is the injected sink (production: msg_store to gm). Never raises on a
    single bad entry — the beat must survive."""
    now = now if now is not None else time.time()
    os.makedirs(state_dir, exist_ok=True)
    sent = 0
    for o in orphans:
        name = o.get("session", "")
        stamp = os.path.join(state_dir, f"{name}.alerted")
        try:
            last = float(open(stamp).read().strip())
        except Exception:  # noqa: BLE001 -- absent/corrupt stamp = never alerted
            last = None
        if last is not None and (now - last) < ALERT_TTL_S:
            continue
        try:
            send({"session": name, "child_cmdline": o.get("child_cmdline", ""),
                  "detected_at": now})
            with open(stamp, "w") as f:
                f.write(str(now))
            sent += 1
        except Exception as e:  # noqa: BLE001 -- log-and-continue; chip-only beat
            sys.stderr.write(f"orphan-pane-scan: alert failed for {name!r}: {e}\n")
    return sent


# --- live gathering (thin; excluded from unit tests, exercised ad hoc) --------

def _live_panes():
    try:
        names = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=10).stdout.split()
    except Exception:  # noqa: BLE001 -- no tmux server = nothing to scan
        return []
    panes = []
    for name in names:
        try:
            pane_pid = subprocess.run(
                ["tmux", "list-panes", "-t", name, "-F", "#{pane_pid}"],
                capture_output=True, text=True, timeout=10).stdout.split()[0]
            kids = subprocess.run(
                ["ps", "-o", "args=", "--ppid", pane_pid],
                capture_output=True, text=True, timeout=10).stdout.splitlines()
        except Exception:  # noqa: BLE001 -- session vanished mid-scan
            continue
        cmd = next((k.strip() for k in kids if _cmd_is_agent_cli(k.strip())), "")
        try:
            created = float(subprocess.run(
                ["tmux", "display-message", "-t", name, "-p",
                 "#{session_created}"],
                capture_output=True, text=True, timeout=10).stdout.strip())
        except Exception:  # noqa: BLE001 -- absent = no grace info (fail toward flag)
            created = None
        panes.append({"session": name, "child_cmdline": cmd,
                      "pane_created": created})
    return panes


def _send_gm_alert(payload):
    body = (f"LEG-B ORPHAN PANE (raw-spawn backstop): tmux session "
            f"{payload['session']!r} runs an agent CLI "
            f"({payload['child_cmdline'][:120]}) but resolves to NO "
            f"identity-store lineage — unregistered seat: invisible to "
            f"routing/recovery, dual-chip risk. NO action taken (chip-only, "
            f"per your no-auto-kill rule). Adopt it (spawn_adopt.py / "
            f"promote path) or reap it — your call.")
    subprocess.run(
        [sys.executable, os.path.join(_ROOT, "msg_store.py"), "send",
         "--from", "orchestra-builder", "--to", "gm",
         "--subject", f"ORPHAN PANE: {payload['session']} (unregistered agent CLI)",
         "--body", body],
        cwd=_ROOT, capture_output=True, text=True, timeout=30)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="print orphans, alert nothing")
    p.add_argument("--ignore", default="",
                   help="comma-separated session names to skip")
    args = p.parse_args(argv)
    orch = os.environ.get("ORCHESTRA_DIR", _ROOT)
    dbp = os.path.join(orch, "state", "orchestra-registry.db")
    if not os.path.exists(dbp):
        sys.stderr.write("orphan-pane-scan: no identity DB; nothing to do\n")
        return 0
    ignore = {s for s in args.ignore.split(",") if s}
    panes = _live_panes()
    orphans = find_orphans(panes, dbp, ignore=ignore)
    resurrections = find_resurrections(panes, dbp, ignore=ignore)
    if args.dry_run:
        print(json.dumps({"orphans": [o["session"] for o in orphans],
                          "resurrections": [
                              {"session": r["session"], "reason": r["reason"]}
                              for r in resurrections]}))
        return 0
    n = alert_orphans(orphans,
                      state_dir=os.path.join(orch, "state", "orphan-pane-scan"),
                      send=_send_gm_alert)
    m = alert_orphans(resurrections,
                      state_dir=os.path.join(orch, "state", "orphan-pane-scan",
                                             "resurrections"),
                      send=_send_gm_resurrection_alert)
    print(json.dumps({"orphans": [o["session"] for o in orphans],
                      "resurrections": [
                          {"session": r["session"], "reason": r["reason"]}
                          for r in resurrections],
                      "alerted": n + m}))
    return 0


# --- Resurrection detection (gm msg_880c11d3): a live agent-CLI pane of a
# KNOWN lineage whose identity is not healthy — canonical missing (retired
# lineage respawned), canonical pointing at a retired_at generation (the
# contradictory post-adopt shape), or canonical sid NULL past a grace window
# (a legit fresh spawn has NULL sid briefly; without grace every new spawn
# would chip). Chip-only, same as the orphan rule — detection NEVER kills.

SID_GRACE_S = 6 * 3600


def _lineage_health(conn, root):
    row = conn.execute(
        "SELECT g.retired_at, g.session_id FROM canonical cn "
        "JOIN generations g ON g.id = cn.generation_id WHERE cn.root=?",
        (root,)).fetchone()
    if row is None:
        return "no-canonical"
    if row["retired_at"]:
        return "canonical-retired"
    if not row["session_id"]:
        return "null-sid"
    return None


def find_resurrections(panes, db_path, ignore=None, now=None):
    """Pure classifier over KNOWN lineages (unknown ones are find_orphans'
    business). Returns flagged panes with a `reason` field."""
    ignore = ignore or set()
    now = now if now is not None else time.time()
    roots = _known_lineages(db_path)
    conn = orchestra_db.get_connection(db_path)
    out = []
    try:
        for p in panes:
            name = p.get("session", "")
            if not name or name in ignore:
                continue
            if not _cmd_is_agent_cli(p.get("child_cmdline", "")):
                continue
            root = name
            if root not in roots:
                m = _GEN_ALIAS.match(name)
                if not (m and m.group("root") in roots):
                    continue  # unknown lineage -> orphan rule's business
                root = m.group("root")
            reason = _lineage_health(conn, root)
            if reason is None:
                continue
            if reason == "null-sid":
                created = p.get("pane_created")
                if created is not None and (now - float(created)) < SID_GRACE_S:
                    continue  # fresh spawn: sid not yet attributed — legit
            out.append({**p, "reason": reason})
    finally:
        conn.close()
    return out


def _send_gm_resurrection_alert(payload):
    body = (f"RESURRECTION/IDENTITY-HEALTH chip: tmux session "
            f"{payload['session']!r} runs an agent CLI but its lineage is "
            f"unhealthy — reason={payload['reason']} (no-canonical = retired "
            f"lineage respawned; canonical-retired = canonical points at a "
            f"retired_at generation; null-sid = canonical sid unattributed "
            f"past grace). NO action taken (chip-only). Honor-the-retirement "
            f"or repair the row — your call.")
    subprocess.run(
        [sys.executable, os.path.join(_ROOT, "msg_store.py"), "send",
         "--from", "orchestra-builder", "--to", "gm",
         "--subject",
         f"RESURRECTION CHIP: {payload['session']} ({payload['reason']})",
         "--body", body],
        cwd=_ROOT, capture_output=True, text=True, timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
