#!/usr/bin/env python3
"""bus_beat.py — WS-A bus DRAIN entrypoint (UNARMED until cron-installed).

The consume-side of the WS-A bus arming wiring (gm/the operator commission msg_85983af9).
One drain beat: read the hot event stream -> resolve pane/session -> canonical
agent at INGEST -> dedup by event_id (cursor `seen`) -> validate -> promote
actionable (turn_ended/session_end/notification) to nonce-keyed msg_store rows ->
advance the cursor -> watchdog_check. PARALLEL-SHADOW: this delivers ALONGSIDE the
existing message-router.py; it does NOT decommission the router (condition A). The
router keeps running until the bus is proven with no mail gap.

Reversible-arm parity (condition C): this file is INERT until a crontab line runs
it; removing that line + unregistering the feeder hook restores today's router path
exactly. No live effect from mere presence.

`run_drain` is pure over injected seams (resolve_fn / send_fn / now / cursor path)
so it is hermetically testable; __main__ wires the real msg_store + a registry-based
resolver. Fail-open at the beat level: a bad line is skipped, an unresolved agent is
left in the stream for a later drain, the cursor only advances over delivered rows.
"""
import json
import os
import sys
import time

# Bootstrap: make `scripts.*` importable when run directly as a script (the cron
# entry), not only when imported as a package module (pytest). Must precede the
# package import below.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from scripts.lineage_daemon import bus

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _orchestra_dir():
    """DATA dir: $ORCHESTRA_DIR (orchestra.toml [data] dir) else the checkout."""
    return os.environ.get("ORCHESTRA_DIR", _ROOT)


STREAM_DIR = os.environ.get(
    "ORCH_EVENT_STREAM_DIR", os.path.join(_orchestra_dir(), "state", "event-stream"))
CURSOR_PATH = os.environ.get(
    "ORCH_BUS_CURSOR", os.path.join(_orchestra_dir(), "state", "bus-cursor.json"))

# Kill-switch sentinel (the operator arm-condition): if this file exists, the drain is an
# INSTANT pure no-op — no stream read, no promote, cursor UNTOUCHED. gm/the operator e-stop.
# Reinstate = rm the sentinel -> the next drain resumes from the SAVED cursor with no
# dropped events (the stream is immutable + append-only; the cursor's `seen`/position
# is exactly where the last successful drain left off).
DRAIN_DISABLED_FILE = os.path.join("state", "BUS_DRAIN_DISABLED")   # in-tree (.stignore'd)
DRAIN_DISABLED_BASENAME = "BUS_DRAIN_DISABLED"


def _runtime_dir():
    """The NON-SYNCED runtime dir (~/runtime, OUTSIDE Syncthing). Overridable via
    ORCH_RUNTIME_DIR (tests point it at tmp)."""
    return os.environ.get("ORCH_RUNTIME_DIR", os.path.expanduser("~/runtime"))


def drain_disabled(orchestra_dir=None) -> bool:
    """True iff the AUTHORITATIVE e-brake exists. HARDENED (gm msg_b8f1c614): ONLY the
    non-synced runtime path (~/runtime/BUS_DRAIN_DISABLED) brakes — a synced in-tree
    sentinel can be spuriously toggled EITHER direction by a stale Mac copy (VPS
    .stignore doesn't stop the Mac announcing it), so it must not start/stop the drain.
    The in-tree path is advisory-only (drain_disabled_advisory)."""
    runtime = os.path.join(_runtime_dir(), DRAIN_DISABLED_BASENAME)
    return os.path.exists(runtime)


def drain_disabled_advisory(orchestra_dir=None) -> bool:
    """True iff the in-tree (synced, advisory) drain sentinel is present — NOT a brake,
    just a logged warning that a stale synced file exists (authoritative = ~/runtime)."""
    od = orchestra_dir or _orchestra_dir()
    return os.path.exists(os.path.join(od, DRAIN_DISABLED_FILE))


def hot_days(now=None, window_days=2):
    """The UTC day strings the drain reads (today + `window_days-1` back) — the hot
    window; older days are cold-archive (retention handled separately)."""
    now = now if now is not None else time.time()
    return [time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
            for i in range(window_days)][::-1]


def run_drain(*, stream_dir, cursor, resolve_fn, send_fn, now=None,
              window_days=2, max_gap_s=bus.BEAT_MAX_GAP_S, orchestra_dir=None,
              actionable=bus.ACTIONABLE_TYPES):
    """Run ONE drain beat over the hot stream. Returns
    {consumed, watchdog, cursor, disabled} where cursor is the UPDATED cursor to
    persist.

    KILL-SWITCH: if the sentinel is present, this is an INSTANT pure no-op — NO
    stream read, NO promote, and the cursor is returned UNCHANGED (so the saved
    position survives the e-stop; rm-sentinel -> the next drain resumes from exactly
    here with no dropped events; the stream is immutable + append-only).

    Idempotent: `seen` (from the cursor) is passed to consume_beat so a restart
    re-drain over an overlapping window can't double-promote. The cursor's
    last_beat_ts is stamped to `now` (for the liveness watchdog)."""
    now = now if now is not None else time.time()
    if drain_disabled(orchestra_dir):
        return {"consumed": {"processed": [], "promoted": [], "skipped": [],
                             "skipped_lines": 0},
                "watchdog": {"live": True, "gap_s": None, "action": "disabled"},
                "cursor": cursor, "disabled": True}      # cursor UNCHANGED
    read = bus.read_events(stream_dir, now=now, days=hot_days(now, window_days))
    out = bus.consume_beat(read["events"], resolve_fn=resolve_fn, send_fn=send_fn,
                           seen=set(cursor.get("seen", ())), now=now,
                           actionable=actionable)
    new_cursor = {"last_beat_ts": now, "seen": list(out["seen"])}
    wd = bus.watchdog_check({"last_beat_ts": cursor.get("last_beat_ts")}, now,
                            max_gap_s=max_gap_s)
    return {"consumed": {"processed": out["processed"], "promoted": out["promoted"],
                         "skipped": out["skipped"],
                         "skipped_lines": read["skipped_lines"]},
            "watchdog": wd, "cursor": new_cursor, "disabled": False}


# --- default INGEST resolver (pane/session -> canonical agent) -----------------

def default_resolve(orchestra_dir=None):
    """Build the real resolve_fn(pane, session_id, cwd) -> canonical agent id|None.

    Reverse-maps the raw event's source identity to an agent via agent-sessions.json
    (session_id or tmux pane/session), then follows the succession chain to the
    canonical live head (so an event from a since-rotated predecessor resolves to its
    successor). Returns None when unresolved -> the event stays in the stream for a
    later drain (never promoted to a wrong address). This is the ONE live-integration
    seam to validate at arm time."""
    od = orchestra_dir or _orchestra_dir()
    try:
        with open(os.path.join(od, "state", "agent-sessions.json")) as fh:
            meta = json.load(fh)
    except Exception:  # noqa: BLE001
        meta = {}

    def _canonical_head(aid):
        # follow succeeded_by to the terminal head (bounded walk).
        seen = set()
        cur = aid
        while cur and cur not in seen:
            seen.add(cur)
            nxt = (meta.get(cur) or {}).get("succeeded_by")
            if not nxt:
                return cur
            cur = nxt
        return cur

    def resolve(pane, session_id, cwd):
        # 1) exact claude session_id match.
        if session_id:
            for aid, e in meta.items():
                if e.get("session_id") == session_id:
                    return _canonical_head(aid)
        # 2) tmux pane/session match (pane like '%3' or a session name).
        if pane:
            for aid, e in meta.items():
                if e.get("tmux_session") in (pane, pane.lstrip("%")):
                    return _canonical_head(aid)
        return None

    return resolve


# --- gm wake-on-delivery riding this beat (DEC-1786828407) ---------------------

WAKE_COOLDOWN_PATH = os.environ.get(
    "ORCH_WAKE_COOLDOWN", os.path.join(_orchestra_dir(), "state", "wake-cooldown.json"))
WAKE_LOG_PATH = os.path.join(_orchestra_dir(), "logs", "wake-transport.jsonl")


def wake_mode(argv):
    """OPT-IN ONLY: the wake step runs solely under an explicit flag, so the
    LIVE per-minute crontab line (no flag) stays wake-inert — merely committing
    this file arms NOTHING. --wake-dry = the mandatory dry window (inject +
    verify-visible, NO Enter, 'would submit' log); --wake-armed = full verified
    inject-AND-submit, only ever installed via the separate gm+the operator arm step."""
    argv = argv or []
    if "--wake-armed" in argv:
        return "armed"
    if "--wake-dry" in argv:
        return "dry"
    return None


def run_wake_beat(mode):
    """The wake pass with REAL seams (msg_store inbox, agent-status + bus stream
    gm-state, tmux composer capture, gateway/dry inject). One JSONL log line per
    pass with a decision is appended to WAKE_LOG_PATH."""
    from scripts.lineage_daemon import wake_transport as wt
    res = wt.run_wake(
        mode,
        cooldown_path=WAKE_COOLDOWN_PATH,
        inbox_fn=lambda: _gm_inbox(),
        gm_state_fn=_gm_state,
        composer_fn=lambda: _gm_composer(),
        inject_fn=_wake_inject,
        artifact_fn=_wake_failed_artifact,
        confirm_wait_s=2.0)
    try:
        rec = {"ts": time.time(), "mode": mode,
               "wake": res.get("wake"), "reason": res.get("reason"),
               "disabled": res.get("disabled"), "injected": res.get("injected"),
               "would_submit": res.get("would_submit"),
               "submitted": res.get("submitted"), "confirmed": res.get("confirmed"),
               "wake_failed": res.get("wake_failed"), "nonce": res.get("nonce")}
        os.makedirs(os.path.dirname(WAKE_LOG_PATH), exist_ok=True)
        with open(WAKE_LOG_PATH, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 -- logging is best-effort
        pass
    return res


def _gm_inbox():
    sys.path.insert(0, _ROOT)          # msg_store.py lives in the checkout, not the data dir
    from msg_store import MessageStore
    return MessageStore().inbox("gm")


def _agent_status_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_status_bb", os.path.join(_ROOT, "scripts", "agent-status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gm_state():
    from scripts.lineage_daemon import bus as _bus
    from scripts.lineage_daemon import wake_transport as wt
    st = _agent_status_mod().get_agent_status("gm")
    read = _bus.read_events(STREAM_DIR, days=hot_days())
    last = wt.last_gm_event_type(read["events"], resolve_fn=default_resolve())
    return wt.build_gm_state(st, last_event_type=last)


def _gm_composer():
    import subprocess
    from scripts.lineage_daemon import wake_transport as wt
    r = subprocess.run(["tmux", "capture-pane", "-e", "-p", "-t", "gm"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None      # unreadable -> PB fail-closed abort
    return wt.composer_lines(r.stdout)


def _wake_inject(text, submit):
    """The inject seam. submit=True (armed) rides the gateway's verified_inject
    (POST /agent-message: inject -> verify-visible -> Enter -> verify-cleared ->
    retry-Enter-once) — NEVER raw send-keys for a submit. submit=False (dry)
    injects the text literally + verifies it VISIBLE in gm's composer and stops:
    NO Enter is ever sent on the dry path (binding Q4)."""
    import subprocess
    if submit:
        import urllib.request
        gateway = os.environ.get("WATCH_GATEWAY_URL", "http://127.0.0.1:9091")
        req = urllib.request.Request(
            f"{gateway}/agent-message",
            data=json.dumps({"session": "gm", "text": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode())
        return bool(r.get("ok")), r
    # DRY: literal paste, ingest wait, verify-visible, NO Enter.
    r = subprocess.run(["tmux", "send-keys", "-t", "gm", "-l", text],
                       capture_output=True, timeout=10)
    if r.returncode != 0:
        return False, {"reason": "send_failed"}
    time.sleep(1.0)
    cap = subprocess.run(["tmux", "capture-pane", "-p", "-t", "gm"],
                         capture_output=True, text=True, timeout=10)
    marker = text.strip().splitlines()[0][:40]
    visible = cap.returncode == 0 and marker in (cap.stdout or "")
    return visible, {"reason": "dry_visible" if visible else "dry_not_visible",
                     "would_submit": True}


def _wake_failed_artifact(record):
    """Durable wake_failed artifact: a log line + a msg_store row (best-effort)."""
    try:
        os.makedirs(os.path.dirname(WAKE_LOG_PATH), exist_ok=True)
        with open(WAKE_LOG_PATH, "a") as fh:
            fh.write(json.dumps({"ts": time.time(), **record}) + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        import subprocess
        subprocess.run(
            ["python3", os.path.join(_ROOT, "msg_store.py"), "send",
             "--from", "wake-transport", "--to", "gm", "--type", "escalation",
             "--subject", f"wake_failed nonce {record.get('nonce')}",
             "--body", json.dumps(record)], timeout=20)
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    """Run one drain beat with the real seams + persist the cursor. Installed as a
    crontab line ONLY after the operator's arm-tap; a --dry-run prints without persisting."""
    dry = "--dry-run" in (argv or sys.argv[1:])
    cursor = bus.load_cursor(CURSOR_PATH)
    result = run_drain(
        stream_dir=STREAM_DIR, cursor=cursor,
        resolve_fn=default_resolve(), send_fn=bus.default_send_fn)

    if result.get("disabled"):
        # LOUD e-stop log line so an operator watching the log notices immediately.
        # Cursor NOT persisted (unchanged) — rm the sentinel to resume from here.
        print(json.dumps({
            "ts": time.time(), "DRAIN_DISABLED": True,
            "sentinel": DRAIN_DISABLED_FILE,
            "msg": "BUS DRAIN E-STOPPED — kill-switch sentinel present; no read, no "
                   "promote, cursor frozen. Reinstate: rm the sentinel -> next drain "
                   "resumes from saved cursor.", "dry_run": dry}))
        return 0

    if not dry:
        bus.save_cursor(CURSOR_PATH, result["cursor"])
    c = result["consumed"]
    print(json.dumps({
        "ts": result["cursor"]["last_beat_ts"],
        "processed": len(c["processed"]), "promoted": len(c["promoted"]),
        "skipped": len(c["skipped"]), "skipped_lines": c["skipped_lines"],
        "watchdog": result["watchdog"]["action"], "dry_run": dry}))

    # gm wake-on-delivery rides this beat — OPT-IN flag only (inert without it).
    wmode = wake_mode(argv or sys.argv[1:])
    if wmode:
        try:
            wres = run_wake_beat(wmode)
            print(json.dumps({"wake_mode": wmode, "wake": wres.get("wake"),
                              "reason": wres.get("reason"),
                              "would_submit": wres.get("would_submit"),
                              "confirmed": wres.get("confirmed")}))
        except Exception as e:  # noqa: BLE001 -- the wake path NEVER wedges the drain
            print(json.dumps({"wake_mode": wmode, "wake": False,
                              "reason": f"wake_beat_error:{type(e).__name__}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
