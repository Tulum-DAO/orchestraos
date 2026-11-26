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
The weekly Fable allowance is also READ (usage_poll's "weekly-fable" window, from the usage body's
limits[]; operator ask 2026-11-26, gm order msg_514c57ad), so tick_window() adds:
  6. proactive: when the shared login's fresh weekly-fable reading is at 95% or more, every Claude seat
     on a Fable model moves to Opus 5.5 BEFORE the banner (the banner leg above stays the backstop);
  7. return: a seat this module moved goes back to the Fable model it was on once that window has
     reset (a fresh reading taken after the recorded reset, under 95%), if it is still on Opus 5.5;
     a seat someone moved by hand since is left alone and forgotten. A drop with no known prior model
     never returns on its own.
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
PROACTIVE_PCT = 95.0         # the weekly-fable reading that moves Fable seats before the banner
USAGE_FRESH_S = 30 * 60      # an older usage reading is no reading
LEARN_AFTER_S = 600          # a banner drop's first reading under 95% this long after it = the window has reset
RETRY_S = 600
MAX_TRIES = 3
NUDGE = ("[limit-watch] This seat hit its Fable limit, so it now runs on Opus 5.5. "
         "Continue where you left off.")
RETURNS = "returns to its Fable model when the weekly Fable window resets"


def fable_reading(u, now):
    """(used_pct, resets_at, as_of) of a fresh weekly-fable reading in a usage file's dict, else None."""
    if not isinstance(u, dict):
        return None
    try:
        as_of = float(u.get("as_of") or 0)
    except (TypeError, ValueError):
        return None
    w = (u.get("windows") or {}).get("weekly-fable") if isinstance(u.get("windows"), dict) else None
    used = w.get("used_pct") if isinstance(w, dict) else None
    if now - as_of > USAGE_FRESH_S or isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    reset = w.get("resets_at")
    return float(used), (float(reset) if isinstance(reset, (int, float)) and not isinstance(reset, bool) else None), as_of


def _record(state, seat, session, prior, now, resets_at):
    """Remember a drop for the return leg: only a Fable model we saw is ever switched back to."""
    state.setdefault("dropped", {})[seat] = {
        "from": prior if isinstance(prior, str) and FABLE.search(prior) else None,
        "session": session, "at": now, "resets_at": resets_at}


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

    def seat_state(self, session):
        """GET /agent-model: {"model", "last_switch", "pending"} for this seat, or None when the gateway cannot say."""
        from urllib.parse import quote
        status, body = self._gateway("GET", f"/agent-model?session={quote(session)}")
        if status != 200 or not isinstance(body.get("model"), str):
            return None
        return {"model": body["model"], "last_switch": body.get("last_switch"), "pending": body.get("pending")}

    def seat_model(self, session):
        """The model the gateway says this seat is on now, or None."""
        st = self.seat_state(session)
        return st["model"] if st else None

    def usage(self, account):
        import limit_switch as LS
        return LS.LiveDeps(self.paths, self.mail).usage(account)

    def claude_seats(self):
        import limit_switch as LS
        return LS.LiveDeps(self.paths, self.mail).claude_seats()

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
        prior = deps.seat_model(session)
        status, body = deps.switch_model(session, TARGET)
        row = {"ts": stamp, "seat": seat, "session": session, "key": r.get("key"), "to": TARGET,
               "http": status, "result": body.get("switching") or body.get("status") or body.get("error")}
        if status in (200, 202) and body.get("ok"):
            d["done"] = True
            _record(state, seat, session, prior, now, None)
            nudged = None
            if status == 200:
                attached = deps.attached_sessions()           # fresh, right before typing; unreadable = hold
                if attached is not None and session not in attached and LE.from_text(deps.pane_text(session)):
                    nudged = deps.nudge(session, NUDGE)
            log(dict(row, kind="model_dropped", why="banner", nudged=nudged, returns=RETURNS))
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
                  f"The shared login was not switched. Each {RETURNS}.\n")
    return done


def tick_window(active, now, log, deps, state):
    """Rules 6 and 7, on the shared login's weekly-fable reading. Returns {"dropped": [...], "returned": [...]}."""
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    out = {"dropped": [], "returned": []}
    r = fable_reading(deps.usage(active), now) if active else None
    if r is None:
        return out
    used, resets, as_of = r
    dropped = state.setdefault("dropped", {})
    for d in dropped.values():
        # a banner drop with no reading at the time learns its window's reset from the first reading after it.
        # At 95% or more that reading is still the exhausted window. Under 95%, taken 10 min or more after the
        # drop, the window has already reset (the reading never sees the old reset): eligible now. Sooner: wait.
        if d.get("resets_at") is None and resets is not None:
            if used >= PROACTIVE_PCT:
                d["resets_at"] = resets
            elif as_of >= float(d.get("at") or 0) + LEARN_AFTER_S:
                d["resets_at"] = float(d.get("at") or 0)
    if used >= PROACTIVE_PCT:
        seats = deps.claude_seats()
        if seats is None:
            log({"ts": stamp, "kind": "model_drop_held", "reason": "seat registry unreadable"})
            return out
        tried = state.setdefault("proactive", {})
        for root, session in seats:
            if tried.get(root) and now - float(tried[root]) < RETRY_S:
                continue
            prior = deps.seat_model(session)
            if not (isinstance(prior, str) and FABLE.search(prior)):
                continue
            tried[root] = now
            status, body = deps.switch_model(session, TARGET)
            row = {"ts": stamp, "seat": root, "session": session, "to": TARGET, "http": status, "why": "proactive",
                   "weekly_fable": used, "resets_at": resets,
                   "result": body.get("switching") or body.get("status") or body.get("error")}
            if status in (200, 202) and body.get("ok"):
                _record(state, root, session, prior, now, resets)
                log(dict(row, kind="model_dropped", returns=RETURNS))
                out["dropped"].append(f"{root}{' (at its turn end)' if status == 202 else ''}")
            else:
                log(dict(row, kind="model_drop_failed"))
        if out["dropped"]:
            deps.mail("gm", "medium", f"Fable weekly at {used:.0f}%: {len(out['dropped'])} seat(s) moved to Opus 5.5",
                      f"The shared login {active} has used {used:.0f}% of its weekly Fable allowance"
                      f" ({LE.reset_label(resets)}), so these seats now run {TARGET} before the limit stops them: "
                      f"{', '.join(out['dropped'])}. Each {RETURNS}.\n")
        return out
    sessions = None
    for seat, d in sorted(dropped.items()):
        reset = d.get("resets_at")
        if not d.get("from") or reset is None or now < float(reset) or as_of < float(reset):
            continue                         # no known model to go back to, or the window has not reset yet
        if d.get("return_last") and now - float(d["return_last"]) < RETRY_S:
            continue
        if sessions is None:
            sessions = deps.sessions()
            if sessions is None:
                log({"ts": stamp, "kind": "model_return_held", "reason": "seat registry unreadable"})
                return out
        session = sessions.get(seat)
        st = deps.seat_state(session) if session else None
        if session and st is None:
            continue                         # the gateway cannot say now: keep the record, try next run
        current = (st or {}).get("model")
        last = (st or {}).get("last_switch") or {}
        pending = (st or {}).get("pending") or {}
        if current != TARGET and pending.get("model") == TARGET:
            continue                         # our drop is still waiting for the seat's turn to end
        if current != TARGET or not (last.get("by") == ACTOR.split(":", 1)[1] and last.get("model") == TARGET):
            # moved by a person (or anything else) since our drop, rotated onto another model, or gone:
            # theirs stands, forget it
            log({"ts": stamp, "kind": "model_return_skipped", "seat": seat, "session": session, "current": current,
                 "last_by": last.get("by")})
            del dropped[seat]
            continue
        d["return_last"] = now
        status, body = deps.switch_model(session, d["from"])
        row = {"ts": stamp, "seat": seat, "session": session, "to": d["from"], "http": status, "weekly_fable": used,
               "result": body.get("switching") or body.get("status") or body.get("error")}
        if status in (200, 202) and body.get("ok"):
            del dropped[seat]
            log(dict(row, kind="model_returned"))
            out["returned"].append(f"{seat}{' (at its turn end)' if status == 202 else ''}")
        else:
            log(dict(row, kind="model_return_failed"))
    if out["returned"]:
        deps.mail("gm", "medium", f"Fable weekly reset: {len(out['returned'])} seat(s) back on Fable",
                  f"The weekly Fable window on {active} has reset ({used:.0f}% used now), so these seats went back "
                  f"to the Fable model they were on: {', '.join(out['returned'])}.\n")
    return out


def prune(state, rows, now, keep_s=86400):
    """Forget drops whose seat is no longer limited, a day after their last try; a recorded drop that never
    learned a reset, after 14 days; proactive try stamps after a day."""
    for seat in [s for s, d in state.get("dropped", {}).items() if now - float(d.get("at") or 0) > 14 * 86400]:
        del state["dropped"][seat]
    for seat in [s for s, t in state.get("proactive", {}).items() if now - float(t or 0) > keep_s]:
        del state["proactive"][seat]
    live = {f"{s}|{r.get('key')}|{r.get('session')}" for s, r in rows.items()}
    for k in [k for k, d in state.get("drops", {}).items()
              if k not in live and now - float(d.get("last") or 0) > keep_s]:
        del state["drops"][k]
