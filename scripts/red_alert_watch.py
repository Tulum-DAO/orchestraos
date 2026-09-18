#!/usr/bin/env python3
"""red_alert_watch.py — the always-on RED ALERT watchdog (docs/RED_ALERT.md §4-5).

    python3 scripts/red_alert_watch.py            # one tick (cron */1)
    python3 scripts/red_alert_watch.py --dry-run  # classify + print, file nothing
    python3 scripts/red_alert_watch.py --seat gm  # only this seat

Per tick, for every live registered CLI seat (registry status online, runtime claude/gemini/
codex, tmux session present):
  1. classify via red_alert.classify — ps STAT containing T (the ^Z class), dead pane,
     out-of-usage / API error / login screen / bypass dialog on screen.
  2. a NEW finding files a report (dedup: one open report per seat+class) and posts the card
     (approval.py, options Repair now / Wait / Show me) + Telegram + the Arturo mirror
     (msg_store row to gm, type red_alert).
  3. the decision core (`decide`, pure) turns (report, card answer, attached?, armed?) into
     post_card / wait / hold / show / repair / escalate. No answer for 120s -> repair.
  4. repairs are the catalogue's immediate fixes ONLY, each verified by effect
     (re-classify -> None). Never on a pane a client is attached to unless the operator tapped
     "Repair now". Two failed attempts -> escalate: red_alert.escalate + a diagnosis seat.

Kill switch: `state/red-alert/DISARM` (present = report + card only, never repair).
Exclusions: `state/red-alert/EXCLUDE` (one seat per line). Lock: flock on .watch.lock.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import red_alert as RA  # noqa: E402

ORCH = RA.ORCH            # data dir
CODE_ROOT = RA.CODE_ROOT  # scripts live here
WINDOW_S = 120          # the operator: "if not acted upon in 2 minutes, repairs the system"
HOLD_S = 1800           # "Wait" = 30 minutes
MAX_ATTEMPTS = 2        # third failure never happens: escalate after two
VERIFY_WAIT_S = 20      # seconds a respawned CLI gets before the by-effect check
CARD_FROM = "red-alert-builder"
CLI_RUNTIMES = ("claude", "gemini", "codex")
CARD_ONLY = {"login_screen", "gateway_unreachable", "tmux_server_dead", "process_suspended"}
MODEL_LADDER = ["claude-opus-5[1m]", "claude-sonnet-5[1m]", "gemini", "codex"]

def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        # under RED_ALERT_LOG_DIR (tests) this never touches the live watch.log
        with open(os.path.join(RA.log_dir(), "watch.log"), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# pure core
# ---------------------------------------------------------------------------
def live_seats(registry: dict, *, tmux_sessions: set[str]) -> list[tuple[str, str]]:
    """[(seat_id, tmux_session)] for online CLI seats whose tmux session exists."""
    out = []
    for aid, row in (registry.get("agents") or {}).items():
        if row.get("status") != "online" or row.get("runtime") not in CLI_RUNTIMES:
            continue
        sess = row.get("tmux_session")
        if sess and sess in tmux_sessions:
            out.append((aid, sess))
    return out


def norm_answer(card: dict | None) -> str | None:
    if not card or card.get("status") != "answered":
        return None
    a = (card.get("answer") or "").strip()
    if a.lower() == "option":
        a = (card.get("answer_text") or "").strip()
    low = a.lower()
    if low in ("repair now", "approve", "yes", "repair", "go"):
        return "Repair now"
    if low in ("wait", "hold", "deny", "no"):
        return "Wait"
    if low.startswith("show"):
        return "Show me"
    return a or None


def decide(report: dict, *, attached: bool, answer: str | None, now: float, armed: bool) -> dict:
    cls = report.get("class")
    fix = (RA.CATALOGUE.get(cls) or {}).get("immediate_fix", {}).get("action")
    if not report.get("card_id"):
        return {"action": "post_card"}
    failed = sum(1 for a in report.get("repair_attempts", []) if not a.get("ok"))
    if failed >= MAX_ATTEMPTS:
        return {"action": "escalate", "reason": f"{failed} failed repairs"}
    if answer == "Show me":
        return {"action": "show"}
    if not armed:
        return {"action": "wait", "reason": "disarmed"}
    if cls in CARD_ONLY:
        return {"action": "wait", "reason": "card_only"}
    hold_until = report.get("hold_until")
    if answer == "Wait":
        if hold_until and now < hold_until:
            return {"action": "hold", "hold_until": hold_until}
        if not hold_until:
            return {"action": "hold", "hold_until": now + HOLD_S}
        return {"action": "repair", "fix": fix, "reason": "hold expired"}
    if attached and fix == "switch_provider":
        return {"action": "wait", "reason": "attached"}   # never /model on a pane a human is in (gm, ra_8a6b4ca9)
    if answer == "Repair now":
        return {"action": "repair", "fix": fix, "reason": "answered"}
    if attached:
        return {"action": "wait", "reason": "attached"}
    age = now - float(report.get("created_at_epoch") or now)
    if age >= WINDOW_S:
        return {"action": "repair", "fix": fix, "reason": "window elapsed"}
    return {"action": "wait", "remaining_s": WINDOW_S - age}


def resume_cmd(procs: list[dict], row: dict | None) -> str | None:
    row = row or {}
    for p in procs:
        cmd = p.get("cmd", "")
        if re.search(r"(^|/)(claude|gemini|codex)(\s|$)", cmd) and "--resume" in cmd:
            return cmd
    if row.get("resume_command"):
        return row["resume_command"]
    if row.get("session_id") and row.get("runtime", "claude") == "claude":
        return f"claude --resume {row['session_id']} --dangerously-skip-permissions"
    return None


def next_model(current: str, *, enabled: list[str]) -> str | None:
    ladder = [m for m in MODEL_LADDER if m.split("-")[0] in enabled]
    if current in ladder:
        i = ladder.index(current)
        return ladder[i + 1] if i + 1 < len(ladder) else None
    fam = current.split("-")[0]
    rest = [m for m in ladder if m.split("-")[0] != fam]
    return rest[0] if rest else None


# ---------------------------------------------------------------------------
# filing + card
# ---------------------------------------------------------------------------
def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def file_finding(seat: str, finding: dict, ev: dict, *, capture=RA.capture_evidence) -> dict:
    for r in RA.list_reports():
        if r["status"] != "resolved" and seat in r["seats"] and r.get("class") == finding["class"]:
            return r
    rep = RA.report(reported_by="watchdog", channel="watchdog", severity=finding["severity"], seats=[seat],
                    symptom=f"{finding['class']} on {seat}: {finding['signal']}", cls=finding["class"], capture=capture)
    log(f"FILED {rep['id']} {seat} {finding['class']} ({finding['signal']}) -> {rep['_path']}")
    return rep


def _view(r: dict) -> dict:
    """Report + the watchdog's own fields flattened for decide()."""
    w = r.get("watch") or {}
    return {**r, "created_at_epoch": w.get("card_at") or _epoch(r["created_at"]), "hold_until": w.get("hold_until")}


def post_card(r: dict, ev: dict, seat: str) -> str | None:
    cls, rid = r["class"], r["id"]
    fix = RA.CATALOGUE.get(cls, {}).get("immediate_fix", {})
    q = f"RED ALERT {rid}: {seat} — {cls.replace('_', ' ')}. Repair it?"
    summary = (f"**What happened:** {r['symptom']}\n\n**What I will do if you don't answer in 2 minutes:** "
               f"{fix.get('action', '-')} — {fix.get('how', '')}\n\nReport: `{r['_path']}`\n"
               f"Snapshot: `{(ev.get('pane_snapshot') or {}).get(seat, '-')}`")
    out = subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "request", "--from", CARD_FROM,
                          "--worker-kind", "node", "--op-key", f"red-alert:{rid}", "--options", "Repair now,Wait,Show me",
                          "--risk", "medium", "--reversibility", "easy", "--feature", "RED ALERT",
                          "--summary", summary, "--seat-id", seat, q], capture_output=True, text=True, timeout=60)
    card = (out.stdout.strip().splitlines() or [""])[-1]
    if not card.startswith("apr_"):
        log(f"CARD FAILED {rid}: {out.stdout[-200:]} {out.stderr[-300:]}")
        return None
    RA.update(rid, by="watchdog", note="card posted", card_id=card, status="awaiting-approval",
              watch={"card_at": time.time(), "hold_until": None})
    # mirrors — best effort, each by effect in its own log
    tg = subprocess.run([os.path.join(HERE, "tg-notify.sh"), "--from", "RED ALERT",
                         f"🚨 {rid} {seat}: {cls.replace('_', ' ')}\n{r['symptom'][:300]}\nCard {card}: Repair now / Wait / Show me. "
                         f"No answer in 2 min → {fix.get('action')}."], capture_output=True, text=True, timeout=60)
    body = f"{q}\n\n{summary}\n\ncard={card}"
    bf = os.path.join(RA.log_dir(), f".{rid}-arturo.txt")
    with open(bf, "w") as f:
        f.write(body)
    ms = subprocess.run([sys.executable, os.path.join(CODE_ROOT, "msg_store.py"), "send", "--from", CARD_FROM, "--to", "gm",
                         "--type", "red_alert", "--subject", f"RED ALERT {rid}: {cls} on {seat}", "--body-file", bf],
                        capture_output=True, text=True, timeout=60)
    log(f"CARD {rid} -> {card}; tg rc={tg.returncode}; arturo={ms.stdout.strip()[-60:]}")
    return card


def read_card(card_id: str) -> dict | None:
    out = subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "get", card_id],
                         capture_output=True, text=True, timeout=30).stdout
    try:
        return json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return None


def close_card(card_id: str, text: str) -> None:
    """A repaired seat must not leave a pending card on the operator's phone: self-answer it
    (answered_by = the watchdog, provenance only) so the queue reflects reality."""
    subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "answer", "--id", card_id, "--answer", "option",
                    "--option-n", "1", "--answer-text", text, "--surface", "agent_cli", "--answered-by", CARD_FROM],
                   capture_output=True, text=True, timeout=30)


def note_card(card_id: str, text: str) -> None:
    """Append to the card's summary (patch takes a whole summary; read-modify-write)."""
    card = read_card(card_id) or {}
    subprocess.run([sys.executable, os.path.join(HERE, "approval.py"), "patch", "--id", card_id, "--from", CARD_FROM,
                    "--summary", (card.get("summary") or "") + text], capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# repairs (live, tmux) — each returns (ok, detail); ok only by effect
# ---------------------------------------------------------------------------
def tmux(*a) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *a], capture_output=True, text=True)


def _keys(session: str, *keys: str, gap: float = 0.6) -> None:
    for k in keys:
        tmux("send-keys", "-t", session, k)
        time.sleep(gap)


def _healthy(seat: str) -> tuple[bool, dict]:
    ev = RA.capture_evidence([seat], to_disk=False)
    return RA.classify(ev, seat) is None and not ev["pane_dead"][seat], ev


def repair_respawn(seat: str, session: str, ev: dict) -> tuple[bool, str]:
    """HARD RULE (the operator, 2026-09-18): respawn ONLY a pane whose process is already
    gone — no -k, no signal of any kind. A suspended (STAT T) or otherwise present process is
    a card to the operator, never a repair."""
    procs = ev["process_state"].get(seat) or []
    if procs:
        return False, f"process still present ({len(procs)} pids, e.g. {procs[0].get('stat')}) — refusing to respawn (no kills rule); card only"
    cmd = resume_cmd([], ev["registry_rows"].get(seat))
    if not cmd:
        return False, "no resume command derivable (no registry resume_command, no sid)"
    cwd = (ev["registry_rows"].get(seat) or {}).get("cwd") or ORCH
    r = tmux("respawn-pane", "-t", session, "-c", cwd, cmd)
    if r.returncode != 0:
        return False, f"respawn-pane (dead pane) failed: {r.stderr.strip()}"
    time.sleep(VERIFY_WAIT_S)
    ok, ev2 = _healthy(seat)
    procs2 = ev2["process_state"].get(seat) or []
    cls = RA.classify(ev2, seat)
    return ok and bool(procs2), f"respawn-pane (dead pane) '{cmd[:90]}…' -> classify={cls and cls['class']} procs={len(procs2)}"


def repair_switch_provider(seat: str, session: str, ev: dict) -> tuple[bool, str]:
    row = ev["registry_rows"].get(seat) or {}
    current = row.get("model") or "claude-opus-5[1m]"
    nxt = next_model(current, enabled=list(CLI_RUNTIMES))
    if not nxt:
        return False, f"no next model after {current}"
    if not nxt.startswith("claude-"):
        return False, f"cross-provider switch {current} -> {nxt} needs a rotation, not an in-pane /model (escalating)"
    _keys(session, "Escape", "C-u")
    tmux("send-keys", "-t", session, f"/model {nxt}")
    time.sleep(0.8)
    _keys(session, "Enter")
    time.sleep(4)
    screen = tmux("capture-pane", "-p", "-t", session).stdout
    ok = nxt.split("[")[0] in screen
    if ok:
        subprocess.run([sys.executable, os.path.join(HERE, "registry-update.py"), seat, "--field", f"model={nxt}"],
                       capture_output=True, text=True, timeout=30)
    return ok, f"/model {nxt} -> {'on screen' if ok else 'not confirmed'}"


def repair_accept_dialog(seat: str, session: str, ev: dict) -> tuple[bool, str]:
    _keys(session, "Down", "Enter")
    time.sleep(3)
    ok, _ = _healthy(seat)
    return ok, "Down+Enter on the accept row"


def repair_bare_enter(seat: str, session: str, ev: dict) -> tuple[bool, str]:
    _keys(session, "Enter")
    time.sleep(3)
    ok, _ = _healthy(seat)
    return ok, "bare Enter"


def repair_wait_then_retry(seat: str, session: str, ev: dict) -> tuple[bool, str]:
    time.sleep(5)
    ok, _ = _healthy(seat)
    if ok:
        return True, "cleared on its own"
    return repair_bare_enter(seat, session, ev)


REPAIRS = {
    "respawn_resume": repair_respawn,
    "switch_provider": repair_switch_provider,
    "accept_dialog": repair_accept_dialog,
    "bare_enter": repair_bare_enter,
    "wait_then_retry": repair_wait_then_retry,
}


# ---------------------------------------------------------------------------
# escalation (deliverable 4): a diagnosis seat on the highest model
# ---------------------------------------------------------------------------
def spawn_diagnosis(r: dict, *, force: bool = False) -> str:
    """Only after RA.escalate (an escalations[] row) unless force — a direct call on a resolved or
    un-escalated report spawned a seat with nothing to diagnose (diag seat finding, ra_11656d1a #2)."""
    rid = r["id"]
    if not force and (r.get("status") == "resolved" or not r.get("escalations")):
        log(f"DIAG {rid} refused: status={r.get('status')} escalations={len(r.get('escalations') or [])} (force=False)")
        return ""
    name = f"red-alert-diag-{rid.split('_', 1)[1]}"
    failed = sum(1 for a in r.get("repair_attempts", []) if not a.get("ok"))
    task = (f"RED ALERT {rid}: immediate repair failed {failed}x on {','.join(r['seats'])}. "
            f"Read {r['_path']} and docs/RED_ALERT.md. Fan the dirty work (log reads, ps/tmux state, registry rows) "
            f"to Haiku subagents; YOU reason. Write `diagnosis` and `permanent_fix` into the report with "
            f"`python3 scripts/red_alert.py update {rid} --diagnosis ... --permanent-fix JSON --by {name}`, then open a "
            f"dialogue with the operator through Arturo: msg_store send --from {name} --to gm --type red_alert "
            f"--subject 'RED ALERT {rid}: <one-line cause>' with the fix you propose and the question you need answered.")
    env = dict(os.environ, AGENT_RUNTIME="claude", AGENT_MODEL="claude-opus-5[1m]")
    out = subprocess.run([os.path.join(CODE_ROOT, "spawn-agent.sh"), name, "--task", task], capture_output=True, text=True,
                         timeout=180, env=env, cwd=ORCH)
    ok = tmux("has-session", "-t", name).returncode == 0
    log(f"DIAG {rid} spawn {name}: {'up' if ok else 'FAILED'} {out.stderr.strip()[-200:]}")
    return name if ok else ""


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------
def tmux_sessions() -> set[str] | None:
    """None = no tmux server (the fleet is gone), distinct from an empty server."""
    r = tmux("list-sessions", "-F", "#{session_name}")
    if r.returncode != 0 and "no server running" in (r.stderr or ""):
        return None
    return set(r.stdout.split())


def fleet_down(reg: dict) -> dict | None:
    """One report for the whole fleet when the tmux server is gone (card-only class)."""
    online = [a for a, row in (reg.get("agents") or {}).items()
              if row.get("status") == "online" and row.get("runtime") in CLI_RUNTIMES]
    if not online:
        return None
    ev = {"process_state": {"fleet": []}, "pane_dead": {"fleet": True}, "attached": {"fleet": False},
          "screen": {}, "pane_snapshot": {}, "sids": {}, "registry_rows": {}, "online_seats": online,
          "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    f = {"class": "tmux_server_dead", "severity": "crash", "signal": f"tmux: no server running; {len(online)} online seats registered",
         "immediate_fix": RA.CATALOGUE["tmux_server_dead"]["immediate_fix"]}
    r = file_finding("fleet", f, ev, capture=lambda seats, **k: ev)
    if not r.get("card_id"):
        post_card(r, ev, "fleet")
    return r


def armed() -> bool:
    return not os.path.exists(os.path.join(RA.state_dir(), "DISARM"))


def excluded() -> set[str]:
    p = os.path.join(RA.state_dir(), "EXCLUDE")
    if not os.path.exists(p):
        return set()
    return {l.strip() for l in open(p) if l.strip() and not l.startswith("#")}


def handle(seat: str, session: str, r: dict, ev: dict, *, dry: bool) -> None:
    rid = r["id"]
    v = _view(r)
    card = read_card(r["card_id"]) if r.get("card_id") else None
    d = decide(v, attached=bool(ev["attached"].get(seat)), answer=norm_answer(card), now=time.time(), armed=armed())
    log(f"{rid} {seat} {r['class']} -> {d}")
    if dry:
        return
    a = d["action"]
    if a == "post_card":
        post_card(r, ev, seat)
    elif a == "hold":
        if (r.get("watch") or {}).get("hold_until") != d["hold_until"]:
            RA.update(rid, by="watchdog", note="the operator: Wait (30 min hold)", watch={**(r.get("watch") or {}), "hold_until": d["hold_until"]})
    elif a == "show":
        if not (r.get("watch") or {}).get("shown"):
            snap = (ev.get("pane_snapshot") or {}).get(seat) or (r["evidence"].get("pane_snapshot") or {}).get(seat)
            tail = "\n".join((ev.get("screen", {}).get(seat) or "").splitlines()[-15:])
            note_card(r["card_id"], f"\n\n**Show me:** snapshot `{snap}`\n```\n{tail}\n```")
            subprocess.run([os.path.join(HERE, "tg-notify.sh"), "--from", "RED ALERT", f"{rid} {seat} screen tail:\n{tail}"],
                           capture_output=True, text=True, timeout=60)
            RA.update(rid, by="watchdog", note="shown", watch={**(r.get("watch") or {}), "shown": True})
    elif a == "repair":
        fn = REPAIRS.get(d["fix"])
        if not fn:
            RA.record_attempt(rid, action=d["fix"] or "none", ok=False, detail="no executor for this fix")
            return
        RA.update(rid, by="watchdog", note=f"repairing ({d.get('reason')})", status="repairing")
        ok, detail = fn(seat, session, ev)
        RA.record_attempt(rid, action=d["fix"], ok=ok, detail=detail)
        log(f"REPAIR {rid} {seat} {d['fix']} ok={ok} {detail}")
        if r.get("card_id"):
            note_card(r["card_id"], f"\n\n**Auto-repair ({d.get('reason')}):** {d['fix']} → {'OK' if ok else 'FAILED'} — {detail}")
            if ok:
                close_card(r["card_id"], f"repaired automatically: {d['fix']} — {detail[:160]}")
        subprocess.run([os.path.join(HERE, "tg-notify.sh"), "--from", "RED ALERT",
                        f"{rid} {seat}: {d['fix']} {'✅ repaired' if ok else '❌ failed'} — {detail[:200]}"],
                       capture_output=True, text=True, timeout=60)
    elif a == "escalate":
        if not (r.get("watch") or {}).get("diag"):
            RA.escalate(rid, reason=d["reason"], by="watchdog")
            name = spawn_diagnosis(RA.show(rid))
            RA.update(rid, by="watchdog", note=f"diagnosis seat {name or 'spawn failed'}", watch={**(r.get("watch") or {}), "diag": name or "failed"})
            subprocess.run([os.path.join(HERE, "tg-notify.sh"), "--from", "RED ALERT",
                            f"{rid} {seat}: two repairs failed → diagnosis seat {name or 'FAILED to spawn'} (Opus 5)"],
                           capture_output=True, text=True, timeout=60)


def tick(*, only: str | None = None, dry: bool = False, registry_path: str | None = None) -> int:
    reg = json.load(open(registry_path or os.path.join(ORCH, "registry.json")))
    sessions = tmux_sessions()
    if sessions is None:
        if not dry:
            r = fleet_down(reg)
            log(f"FLEET DOWN: no tmux server -> {r and r['id']}")
        else:
            log("DRY FLEET DOWN: no tmux server")
        return 0
    for r in RA.list_reports():          # server back: close the fleet report by effect
        if r["status"] != "resolved" and r.get("class") == "tmux_server_dead" and not dry:
            RA.resolve(r["id"], note="tmux server present again", by="watchdog")
            if r.get("card_id"):
                close_card(r["card_id"], "tmux server is back (resumed by gm)")
            log(f"RESOLVED {r['id']} fleet: tmux server back")
    excl = excluded() | {"red-alert-builder"}
    seats = [(a, s) for a, s in live_seats(reg, tmux_sessions=sessions) if a not in excl and (not only or a == only)]
    findings = 0
    for seat, session in seats:
        ev = RA.capture_evidence([seat], to_disk=False)
        f = RA.classify(ev, seat)
        if not f:
            # heal open reports whose seat is healthy again (repair by hand, or self-cleared)
            for r in RA.list_reports():
                if r["status"] != "resolved" and r["seats"] == [seat] and r["reported_by"] == "watchdog" and not dry:
                    RA.resolve(r["id"], note="seat classifies healthy on a later tick", by="watchdog")
                    if r.get("card_id"):
                        close_card(r["card_id"], "seat is healthy again (fixed by hand or cleared itself) — nothing to decide")
                    log(f"RESOLVED {r['id']} {seat} healthy by effect")
            continue
        findings += 1
        if dry:
            log(f"DRY {seat} {f['class']} attached={ev['attached'][seat]} ({f['signal']})")
            continue
        r = file_finding(seat, f, ev)
        handle(seat, session, r, ev, dry=dry)
    log(f"tick seats={len(seats)} findings={findings} armed={armed()}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seat")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--registry", help="alternate registry.json (sandbox proofs)")
    ns = ap.parse_args(argv)
    lock = open(os.path.join(RA.state_dir(), ".watch.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("red_alert_watch: another tick holds the lock", file=sys.stderr)
        return 0
    return tick(only=ns.seat, dry=ns.dry_run, registry_path=ns.registry)


if __name__ == "__main__":
    sys.exit(main())
