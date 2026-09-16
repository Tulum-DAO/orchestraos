#!/usr/bin/env python3
"""(B) first-fire watcher — CHEAP, read-only, no recomputation.

gm-approved (msg_8dcc1ba3): ping the moment an ARMED T2 seat crosses into HARD
(ctx >= 0.80) WITH a richness-complete committed handoff (markdown OR json — A2),
the natural conditions under which the lineage daemon autonomously hard_rotates it
(the genuine unattended (B) proof). Both conditions required: a HARD seat WITHOUT a
richness-complete handoff just defers per (A)/A2, so it is not a fire.

DESIGN — deliberately cheap:
- Reads the LAST fleet-beat.log block (already computed by the 15-min cron beat);
  does NOT recompute ctx or spin a loop. One-shot; intended to run right AFTER
  cron_beat in the same crontab line.
- A seat's HARD band is read from the beat's own `reason=ctx:HARD` token.
- json-block check = grep the committed canonical handoff for a fenced json block
  (the (A) richness precondition for SOFT_READY -> hard_rotate).
- De-dupes via a marker file so it pings ONCE per (seat, handoff-rev), never spams.

Exit 0 always (never wedge the cron line).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Default: this file lives at <repo>/scripts/b_fire_watcher.py, so its
# grandparent is the repo root — a portable fallback when ORCHESTRA_DIR isn't set.
ORCH = os.environ.get("ORCHESTRA_DIR") or str(Path(__file__).resolve().parent.parent)
BEAT_LOG = os.path.join(ORCH, "logs", "fleet-beat.log")
ARM_FILE = os.environ.get("SELF_RETIRE_ARMED", os.path.join(ORCH, "state", "self_retire_armed"))
MARKER = os.path.join(ORCH, "state", ".b-fire-watcher-pinged")
WAL_DIR = os.path.join(ORCH, "state", "wal")

# Comma-separated seat ids to alert on for a BG fire; empty by default (watch
# everything armed) — set ORCHESTRA_BFIRE_WATCH to scope it down, e.g.
# "ios-watch-dev,codex-dev-1".
WATCH = set(filter(None, os.environ.get("ORCHESTRA_BFIRE_WATCH", "").split(",")))

# bg_state states that mean an autonomous BG fire is UNDERWAY (worth a durable alert).
BG_FIRE_STATES = ("PREWARMING", "READY", "SWAPPING", "DRAINED", "DEGRADED")


def bg_armed_seats(wal_dir=WAL_DIR):
    """Seats armed for the autonomous BG fire = have `<seat>.bg_enabled` AND the
    global `BG_DISABLED` kill-switch is absent. (This is the BG arm truth — distinct
    from the self-retire ARM_FILE the ctx:HARD ping above reads.)"""
    if os.path.exists(os.path.join(wal_dir, "BG_DISABLED")):
        return set()
    out = set()
    try:
        for name in os.listdir(wal_dir):
            if name.endswith(".bg_enabled"):
                out.add(name[: -len(".bg_enabled")])
    except OSError:
        pass
    return out


def bg_fire_signal(bg_json_path):
    """If the seat's bg_state is a fire state AND the latest history entry is a REAL
    transition (its state differs from the entry before it — or it's the very first
    entry, which is always a real first fire), return (state, dedupe_key); else None.

    Without this a same-state re-write (a beat re-appending 'DRAINED->DRAINED
    reason=swap-complete' every tick, gm msg_96438e1d) would still change the history
    depth and so mint a NEW dedupe key each tick — paging gm forever for a non-event.
    dedupe_key ties to the transition (state + history depth) so each distinct fire
    transition notifies exactly once."""
    try:
        with open(bg_json_path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    state = d.get("state")
    if state not in BG_FIRE_STATES:
        return None
    history = d.get("history", []) or []
    if len(history) >= 2 and history[-2].get("state") == history[-1].get("state"):
        return None  # non-transition (same-state re-write) — never a fire signal
    depth = len(history)
    return state, f"{state}:{depth}"


def notify_gm_on_fire(seat, state, key, *, notify_fn, marker):
    """Idempotent durable notify: fire notify_fn(seat, state) once per transition
    key, recording it in marker under 'bg:<seat>'."""
    mk = f"bg:{seat}"
    if marker.get(mk) == key:
        return False
    notify_fn(seat, state)
    marker[mk] = key
    return True


def _notify_gm_fire(seat, state):
    """Durable alert to whoever holds the gm seat NOW + Telegram, so a days-off
    autonomous first fire is never missed (a session Monitor would be)."""
    body = (f"BG AUTONOMOUS FIRE: seat '{seat}' bg_state -> {state}. The first "
            f"unattended blue-green rotation is underway. Machine gate holds on "
            f"non-strict-PASS; observe (WAL capture + bg_state + first-fire-watch). "
            f"Abort: touch state/FLEET_BEAT_DISABLED. -- b-fire-watcher")
    subprocess.run(
        ["python3", os.path.join(ORCH, "msg_store.py"), "send",
         "--from", "b-fire-watcher", "--to", "gm",
         "--subject", f"BG FIRE — {seat} -> {state}", "--body", body],
        cwd=ORCH, capture_output=True)
    try:
        subprocess.run([os.path.join(ORCH, "scripts", "tg-notify.sh"), "--from",
                        "b-fire-watcher", f"BG FIRE: {seat} -> {state} (first autonomous rotation underway)"],
                       cwd=ORCH, capture_output=True, timeout=20)
    except Exception:
        pass


def _armed_lineages():
    try:
        with open(ARM_FILE) as f:
            return {ln.strip() for ln in f
                    if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


def _last_beat_block(text):
    """Lines of the most recent beat (from the last SUMMARY back to the previous)."""
    lines = text.splitlines()
    # find last SUMMARY; the block is everything after the prior SUMMARY up to it
    summ_idxs = [i for i, ln in enumerate(lines) if "SUMMARY" in ln]
    if not summ_idxs:
        return lines
    last = summ_idxs[-1]
    prev = summ_idxs[-2] if len(summ_idxs) >= 2 else -1
    return lines[prev + 1:last + 1]


def _hard_seats(block):
    """Seat ids in the last beat whose reason is ctx:HARD."""
    out = set()
    for ln in block:
        m = re.search(r"\[fleet-beat\]\s+(\S+)\s+.*reason=ctx:HARD", ln)
        if m:
            out.add(m.group(1))
    return out


def _fireable_handoff(seat):
    """True iff the seat's canonical committed handoff is RICHNESS-COMPLETE — the
    real post-A2 readiness the beat uses, NOT a json-block grep.

    A2 (DEC-1788207386) made a COMPLETE MARKDOWN handoff fireable, so a json-block
    grep would MISS a markdown-complete seat crossing HARD (a missed (B) fire).
    We run the SAME recognition + validate(require_richness=True) path the beat's
    handoff_ready uses. Freshness (baseline) is beat-runtime state, not a property
    of the committed file, so the watcher only asserts richness-completeness — the
    part that lives in the handoff. Returns (fireable, rev-marker-for-dedupe)."""
    root = re.sub(r"-(gen\d+|g\d+)$", "", seat)
    path = os.path.join(ORCH, "docs", f"HANDOFF_{root}-next.md")
    if not os.path.isfile(path):
        return False, None
    # rev marker for de-dupe = git blob hash of the handoff
    try:
        rev = subprocess.run(
            ["git", "-C", ORCH, "hash-object", path],
            capture_output=True, text=True).stdout.strip()
    except Exception:
        rev = str(os.path.getmtime(path))
    try:
        import importlib.util
        ld = os.path.join(ORCH, "scripts", "lineage_daemon")

        def _load(name):
            spec = importlib.util.spec_from_file_location(
                f"bfw_{name}", os.path.join(ld, f"{name}.py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
        hp = _load("handoff_provider")
        d, _mt = hp.read_committed_handoff(root, orchestra_dir=ORCH)
        if d is None:
            return False, rev
        from importlib import import_module  # noqa: F401
        hs = _load("handoff_schema")
        h = hs.Handoff.from_dict(d)
        errs = h.validate(require_richness=True)
        return (not errs), rev
    except Exception:
        # fail-CLOSED: if we cannot prove richness-complete, do NOT ping (a false
        # (B) ping is worse than a missed beat — the next beat re-checks)
        return False, rev


def _load_marker():
    try:
        with open(MARKER) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_marker(d):
    try:
        with open(MARKER, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def _ping(seat, rev):
    body = (f"(B) FIRST-FIRE WINDOW: armed T2 seat '{seat}' is at ctx:HARD (>=0.80) "
            f"WITH a RICHNESS-COMPLETE committed handoff (rev {rev[:12]}, markdown or json — A2). The lineage "
            f"daemon should autonomously hard_rotate it on the next beat — this is "
            f"the genuine unattended (B) proof. Get in position to witness live "
            f"(0-rollback / succeeded_by / canonical-advancing / strict comprehension "
            f"PASS). orchestra-builder is watching.")
    subprocess.run(
        ["python3", os.path.join(ORCH, "msg_store.py"), "send",
         "--from", "b-fire-watcher", "--to", "gm",
         "--subject", f"(B) fire window OPEN — {seat} HARD + richness-complete handoff", "--body", body],
        cwd=ORCH, capture_output=True)


def main():
    if not os.path.isfile(BEAT_LOG):
        return 0
    with open(BEAT_LOG) as f:
        block = _last_beat_block(f.read())
    armed = _armed_lineages()
    marker = _load_marker()
    changed = False
    for seat in _hard_seats(block):
        root = re.sub(r"-(gen\d+|g\d+)$", "", seat)
        if (WATCH and root not in WATCH) or root not in armed:
            continue
        fireable, rev = _fireable_handoff(seat)
        if not fireable:
            continue
        if marker.get(root) == rev:
            continue  # already pinged for this exact handoff rev
        _ping(seat, rev)
        marker[root] = rev
        changed = True

    # --- DURABLE BG-fire-notify belt (gm ask msg_42415974): alert the CURRENT gm
    # seat + Telegram the moment a BG-armed seat's bg_state transitions into a fire.
    # Isolated + fail-open so it can never wedge the cron line. ---
    try:
        for seat in bg_armed_seats():
            sig = bg_fire_signal(os.path.join(WAL_DIR, f"{seat}.bg.json"))
            if not sig:
                continue
            state, key = sig
            if notify_gm_on_fire(seat, state, key,
                                 notify_fn=_notify_gm_fire, marker=marker):
                changed = True
    except Exception:
        pass

    if changed:
        _save_marker(marker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
