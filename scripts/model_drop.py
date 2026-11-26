"""model_drop: a seat out of Fable credits drops to Opus 5.5, run by limit_watch --model-drop.

The operator's rule (2026-11-26, denying the login switch on apr_bafb66c3): "This is a fable issue
itself. If we see the fable limit, we drop down to opus 5.5 for the given model." A Fable credits
banner ("You've reached your Fable limit") is a cap on ONE model, not on the login: the 5-hour and
weekly windows read fine while it shows, and the other login's Fable credits may be out too. So the
seat that shows it moves to Opus 5.5 (the seats' 1M id), and the shared login stays where it is.
Conditions, each one a test (test_model_drop.py):
  1. trigger: a limited-seat row (state/usage/limits.json) whose window is "credits" and whose model
     is Fable or unnamed, seen in the last 2 hours;
  2. the switch goes through the gateway's PUT /agent-model, the one path that holds the session's
     switch lock, defers a busy seat to its turn end and puts the host default model back; the
     gateway itself checks the seat is on a Fable model now, so a seat already moved is left alone;
  3. once per seat per episode per session (a new generation is a new drop); a failed switch is retried after 10 minutes, at most 3 times, then
     gm hears it HIGH once;
  4. a seat with no tmux client attached whose screen still shows the banner is nudged once to
     continue; an attached seat is not typed into (the person there sees the banner), and the client
     list is read fresh right before typing: unreadable means no nudge;
  5. every drop is logged and gm hears one line per run naming the seats.
No automatic return: the gateway stores Opus 5.5 as the seat's model, so it stays there until a person
switches it back. A seat PINNED to Fable (state/model_pins.json) comes back on Fable at its next
rotation; if Fable is still out, this drops it again.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import limit_event as LE

TARGET = "claude-opus-5-5[1m]"
ACTOR = "automation:limit-watch"
FABLE = re.compile(r"fable", re.I)
FRESH_S = 2 * 3600           # a credits row with no newer sighting than this is not acted on
RETRY_S = 600
MAX_TRIES = 3
NUDGE = ("[limit-watch] This seat hit its Fable limit, so it now runs on Opus 5.5. "
         "Continue where you left off.")


def wanted(row) -> bool:
    """A limited-seat row this module acts on: a Claude credits cap that is Fable's or names no model."""
    return (row.get("runtime") == "claude" and row.get("window") == "credits"
            and (not row.get("model") or bool(FABLE.search(str(row["model"])))))


class LiveDeps:
    """The real world. Tests pass a fake with the same methods."""

    def __init__(self, paths, mail_fn):
        self.paths, self.mail = paths, mail_fn

    def sessions(self):
        """{root: tmux_session} for canonical seats, or None when the registry cannot be read."""
        try:
            c = sqlite3.connect(f"file:{self.paths.registry_db}?mode=ro", uri=True, timeout=5)
            try:
                return {r: s or r for r, s in c.execute("SELECT root, tmux_session FROM canonical")}
            finally:
                c.close()
        except sqlite3.Error:
            return None

    def _gateway(self, method, path, payload=None):
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return 599, {"error": "blocked under pytest (no stand-in gateway)"}
        import gateway_deliver as GD
        req = urllib.request.Request(
            f"{GD.gateway_url().rstrip('/')}{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GD.gateway_token()}",
                     "X-Actor": ACTOR})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return getattr(resp, "status", 200), json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except ValueError:
                return e.code, {}
        except Exception as e:  # noqa: BLE001 — unreachable or timed out: said plainly, never raised
            return 0, {"error": f"gateway unreachable: {type(e).__name__}"}

    def switch_model(self, session, model):
        """(status, body) of PUT /agent-model."""
        return self._gateway("PUT", "/agent-model", {"session": session, "model": model})

    def attached_sessions(self):
        import limit_switch as LS
        return LS.LiveDeps(self.paths, self.mail).attached_sessions()

    def pane_text(self, session):
        import limit_switch as LS
        return LS.LiveDeps(self.paths, self.mail).pane_text(session)

    def nudge(self, session, text):
        import limit_switch as LS
        return LS.LiveDeps(self.paths, self.mail).nudge(session, text)


def tick(rows, now, log, deps, state):
    """One pass over the limited-seat rows. `state` (dict, persisted by the caller) carries
    drops: {"<seat>|<episode key>": {"tries", "last", "done", "told"}}."""
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    drops = state.setdefault("drops", {})
    todo = []
    for seat, r in sorted(rows.items()):
        if not wanted(r):
            continue
        seen = LE._iso_to_dt(r.get("last_seen") or r.get("since"))
        if seen is None or now - seen.timestamp() > FRESH_S:
            continue
        k = f"{seat}|{r.get('key')}|{r.get('session')}"      # a new generation (new session) is a new drop
        d = drops.get(k) or {}
        if d.get("done") or int(d.get("tries") or 0) >= MAX_TRIES:
            continue
        if d.get("last") and now - float(d["last"]) < RETRY_S:
            continue
        todo.append((seat, r, k))
    if not todo:
        return []
    sessions = deps.sessions()
    if sessions is None:
        log({"ts": stamp, "kind": "model_drop_held", "reason": "seat registry unreadable"})
        return []
    done = []
    for seat, r, k in todo:
        d = drops.setdefault(k, {"tries": 0})
        session = sessions.get(seat)
        if not session:
            log({"ts": stamp, "kind": "model_drop_skipped", "seat": seat, "key": r.get("key"),
                 "reason": "no canonical tmux session for this seat"})
            d.update(done=True)
            continue
        d["tries"], d["last"] = int(d.get("tries") or 0) + 1, now
        status, body = deps.switch_model(session, TARGET)
        row = {"ts": stamp, "seat": seat, "session": session, "key": r.get("key"), "to": TARGET,
               "http": status, "result": body.get("switching") or body.get("status") or body.get("error")}
        if status in (200, 202) and body.get("ok"):
            d["done"] = True
            nudged = None
            if status == 200:
                attached = deps.attached_sessions()           # fresh, right before typing; unreadable = hold
                if attached is not None and session not in attached and LE.from_text(deps.pane_text(session)):
                    nudged = deps.nudge(session, NUDGE)
            log(dict(row, kind="model_dropped", nudged=nudged,
                     returns="stays on Opus; return to Fable is manual or v2 on credits reset (a seat pinned to Fable returns at its next rotation)"))
            done.append(f"{seat}{' (at its turn end)' if status == 202 else ''}")
        elif body.get("reason") == "not_fable":
            # the gateway says this seat is not on a Fable model now (moved by hand, or rotated): nothing to do
            d["done"] = True
            log(dict(row, kind="model_drop_skipped", reason=body.get("error")))
        else:
            log(dict(row, kind="model_drop_failed"))
            if d["tries"] >= MAX_TRIES and not d.get("told"):
                d["told"] = True
                deps.mail("gm", "high", f"Fable limit: could not move {seat} to Opus 5.5",
                          f"{seat} ({session}) shows the Fable limit. {MAX_TRIES} tries to switch it to {TARGET} "
                          f"through the gateway failed; last: HTTP {status} {json.dumps(body)[:300]}.\n")
    if done:
        deps.mail("gm", "medium", f"Fable limit: {len(done)} seat(s) moved to Opus 5.5",
                  f"Fable credits are out on these seats, so they now run {TARGET}: {', '.join(done)}. "
                  f"The shared login was not switched. They stay on Opus; return to Fable is manual or v2 on credits "
                  f"reset (a seat pinned to Fable returns to it at its next rotation).\n")
    return done


def prune(state, rows, now, keep_s=86400):
    """Forget drops whose seat is no longer limited, a day after their last try."""
    live = {f"{s}|{r.get('key')}|{r.get('session')}" for s, r in rows.items()}
    for k in [k for k, d in state.get("drops", {}).items()
              if k not in live and now - float(d.get("last") or 0) > keep_s]:
        del state["drops"][k]
