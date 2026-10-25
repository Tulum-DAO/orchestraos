"""The "While you were away" card's data (RFC 0002, docs/rfcs/0002-away-card.md).

Pure over a data dir and the gateway's agents list, so tests drive it with a temp dir. The gateway's
GET /away is a thin wrapper (scripts/watch_gateway.py handle_away).

  anchor   your last real action: an answered card, a voice call, or a chat message (anchor="action"),
           or the last time an app of yours was on screen (anchor="screen")
  moved    what happened after it: new cards for you, tasks done, rotations, messages to you (dated only)
  down_now registered agent seats that are down now: current state, kept apart from `moved` because the
           fleet scan has no crash time
  summary  one paragraph per anchor, cached; a rules sentence until (or unless) a brain writes one

Nothing here guesses a device. A device is named only from a label the client sent or the
"<device_id>:<label>" stamped on an answer; otherwise the card gets the channel ("phone", "watch",
"web") and says so through device_source.
"""
import datetime as _dt
import glob
import hashlib
import json
import os
import sqlite3
import tempfile
import time

LEDGER = ("state", "operator-actions.jsonl")
PRESENCE = ("state", "away-presence.json")
SURFACE = ("state", "arturo", "active-surface.json")
SUMMARY_DIR = ("state", "away-summary")
VOICE_CALLS = ("state", "voice-calls")
PRESENCE_DEBOUNCE_S = 60
SAID_MAX = 140
MOVED_MAX = 6
_JOURNALS_SCANNED = 500     # newest first; stops at the first journal with your words
_KIND_ORDER = {"answer": 0, "call": 1, "message": 2, "screen": 3}   # deterministic tie-break


def _p(data_dir, parts):
    return os.path.join(str(data_dir), *parts)


def epoch(ts):
    """ISO 8601 (with or without a zone, 'Z' allowed), SQLite's 'YYYY-MM-DD HH:MM:SS' (UTC), or a number
    -> epoch seconds. None when it cannot be read."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        d = _dt.datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(text, n=SAID_MAX):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _rows(db, sql, args=()):
    """Read-only rows, [] when the DB or table is absent: a fresh install has neither."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()
    except sqlite3.Error:
        return []


_SURFACES = ("voice", "phone", "watch", "web", "unknown")
_SURFACE_ALIASES = {"ios": "phone", "iphone": "phone", "ipad": "phone"}   # /surface device keys, X-Surface


def norm_surface(value):
    """One of voice | phone | watch | web | unknown (RFC 0002 §5.1). The iOS app's iPhone and iPad builds
    both report "phone" (and /surface keys them "ios"); anything else unrecognised is "unknown"."""
    v = str(value or "").strip().lower()
    v = _SURFACE_ALIASES.get(v, v)
    return v if v in _SURFACES else "unknown"


def _device_label(stamp):
    """'<device_id>:<label>' (answer_device) -> label; None for None or the bare id."""
    if not stamp or ":" not in str(stamp):
        return None
    return str(stamp).split(":", 1)[1].strip() or None


# ---- anchor candidates ----------------------------------------------------------------------------

def _answer_candidate(data_dir):
    # Filtered in SQL, so the newest OPERATOR answer is found however many agent gates were answered since.
    rows = _rows(_p(data_dir, ("state", "tasks.db")),
                 "SELECT * FROM approval_requests WHERE status='answered' AND answered_at IS NOT NULL "
                 "AND answered_by='operator' AND COALESCE(answer_surface,'') != 'agent_cli' "
                 "ORDER BY answered_at DESC LIMIT 20")
    for r in rows:
        # The operator's answers only: a self-authored gate (answered_by=<agent>) or a CLI answer is not you.
        # Only rows stamped answered_by='operator' (RFC 0002 §5.2). Older rows carry NULL, and so did agents'
        # self-authored gates before the R7d stamp existed, so NULL cannot be taken as the operator.
        if r.get("answered_by") != "operator" or r.get("answer_surface") == "agent_cli":
            continue
        at = epoch(r.get("answered_at"))
        if at is None:
            continue
        label = _device_label(r.get("answer_device"))
        return {"kind": "answer", "ref": r["id"], "at": at, "target": r.get("from_agent"),
                "said": _trim(r.get("answer")), "question": _trim(r.get("question")),
                "surface": r.get("answer_surface") or "unknown", "device": label,
                "device_source": "answer_device" if label else "none", "card_id": r["id"]}
    return None


def _call_candidate(data_dir):
    files = sorted(glob.glob(os.path.join(_p(data_dir, VOICE_CALLS), "*.json")),
                   key=lambda f: os.path.getmtime(f), reverse=True)[:_JOURNALS_SCANNED]
    best = None
    for f in files:
        try:
            j = json.loads(open(f).read())
        except (OSError, ValueError):
            continue
        users = [t for t in (j.get("turns") or []) if isinstance(t, dict) and t.get("role") == "user"
                 and len(str(t.get("text") or "").strip()) >= 3]
        if not users:
            continue
        at = epoch(j.get("ended_at")) or epoch(j.get("started_at"))
        if at is None or (best and at <= best["at"]):
            continue
        label = j.get("device_label")
        status = j.get("status")
        # gm: a voice anchor's outcome comes from the call journal, never a guess.
        outcome = {"state": "working" if status == "live" else ("done" if status == "ended" else "unknown"),
                   "summary": _trim(j.get("summary"), 280) if j.get("summary") else None}
        best = {"kind": "call", "ref": j.get("call_id") or os.path.basename(f), "at": at,
                "target": "arturo", "said": _trim(users[0].get("text")),
                "surface": j.get("surface") or j.get("page") or "voice",
                "device": label or None, "device_source": "label" if label else "channel",
                "journal_outcome": outcome}
    return best


def _message_candidate(data_dir):
    """The newest operator chat send the gateway delivered or held (operator-actions ledger)."""
    path = _p(data_dir, LEDGER)
    try:
        with open(path) as f:
            lines = f.readlines()[-200:]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("kind") != "message":
            continue
        at = epoch(e.get("at"))
        if at is None:
            continue
        label = _device_label(e.get("device"))
        return {"kind": "message", "ref": f"{e.get('target')}@{e.get('at')}", "at": at,
                "target": e.get("target"), "said": _trim(e.get("said")) if e.get("said") else None,
                "surface": e.get("surface") or "unknown", "device": label,
                "device_source": "answer_device" if label else ("channel" if e.get("surface") else "none")}
    return None


def _screen_candidate(data_dir):
    """The newest time an app of yours was on screen: POST /presence {"foreground": true}, or a non-null
    route in /surface (sent by shipped builds on every screen change). Never device last_seen, which
    background polling also moves."""
    best = None
    try:
        pres = json.loads(open(_p(data_dir, PRESENCE)).read())
    except (OSError, ValueError):
        pres = {}
    for key, row in (pres.items() if isinstance(pres, dict) else []):
        at = epoch((row or {}).get("foreground_at"))
        if at is not None and (best is None or at > best["at"]):
            best = {"kind": "screen", "ref": f"presence:{key}", "at": at, "surface": (row or {}).get("surface"),
                    "device": (row or {}).get("label") or None,
                    "device_source": "label" if (row or {}).get("label") else "channel"}
    try:
        surf = json.loads(open(_p(data_dir, SURFACE)).read())
    except (OSError, ValueError):
        surf = {}
    for dev, row in ((surf.get("devices") or {}).items() if isinstance(surf, dict) else []):
        if not isinstance(row, dict) or row.get("route") is None:
            continue
        at = epoch(row.get("updated_at"))
        if at is not None and (best is None or at > best["at"]):
            best = {"kind": "screen", "ref": f"surface:{dev}", "at": at, "surface": dev,
                    "device": None, "device_source": "channel"}
    return best


def pick_anchor(data_dir, mode="action"):
    if mode == "screen":
        return _screen_candidate(data_dir)
    cands = [c for c in (_answer_candidate(data_dir), _call_candidate(data_dir), _message_candidate(data_dir)) if c]
    if not cands:
        return None
    return max(cands, key=lambda c: (c["at"], -_KIND_ORDER[c["kind"]]))


# ---- outcome ----------------------------------------------------------------------------------------

def outcome(anchor, agents, pending_cards):
    """done / working / waiting_on_you / unknown, deterministic (RFC 0002 section 5.2)."""
    if anchor["kind"] == "call":
        return dict(anchor.get("journal_outcome") or {"state": "unknown"}, link="/arturo")
    target = anchor.get("target")
    if not target:
        return {"state": "unknown", "link": None}
    link = f"/agents/{target}"
    mine = [c for c in pending_cards if c.get("from_agent") == target and (epoch(c.get("created_at")) or 0) > anchor["at"]]
    if mine:
        return {"state": "waiting_on_you", "link": f"/approvals/{mine[0]['id']}", "card_id": mine[0]["id"]}
    row = next((a for a in agents if a.get("id") == target), None)
    if row is None:
        return {"state": "unknown", "link": link}
    state = row.get("state")
    if state == "working":
        return {"state": "working", "link": link}
    if state == "waiting":
        # After you ANSWERED its card, an agent still waiting with no newer card has not resumed yet: working
        # on your answer (RFC §5.2). After a message, a waiting agent is waiting on you.
        return {"state": "working" if anchor["kind"] == "answer" else "waiting_on_you", "link": link}
    if state == "idle" and (row.get("last_used_ts") or 0) > anchor["at"]:
        return {"state": "done", "link": link}
    return {"state": "unknown", "link": link}


# ---- moved since ------------------------------------------------------------------------------------

def moved_since(data_dir, at, pending_cards):
    """Dated events after the anchor. Seat STATUS (down, retired, service) is not an event and lives in
    down_now; these are cards, tasks, messages and rotations, each only when strictly after `at`."""
    out = []
    for c in pending_cards:
        t = epoch(c.get("created_at"))
        if t is not None and t > at:
            out.append({"kind": "card", "at": t, "agent": c.get("from_agent"),
                        "text": f"{c.get('from_agent')} asks: {_trim(c.get('question'), 100)}",
                        "link": f"/approvals/{c['id']}"})
    db = _p(data_dir, ("state", "tasks.db"))
    for r in _rows(db, "SELECT id, title, assigned_to, completed_at FROM tasks "
                       "WHERE status IN ('done','completed') AND completed_at IS NOT NULL "
                       "ORDER BY completed_at DESC LIMIT 50"):
        t = epoch(r.get("completed_at"))
        if t is not None and t > at:
            who = r.get("assigned_to")
            out.append({"kind": "task_done", "at": t, "agent": who,
                        "text": f"Done: {_trim(r.get('title'), 100)}" + (f" ({who})" if who else ""),
                        "link": f"/tasks/{r['id']}"})
    for r in _rows(db, "SELECT id, from_agent, subject, created_at FROM messages WHERE to_agent='operator' "
                       "ORDER BY created_at DESC LIMIT 50"):
        t = epoch(r.get("created_at"))
        if t is not None and t > at:
            out.append({"kind": "message", "at": t, "agent": r.get("from_agent"),
                        "text": f"{r.get('from_agent')}: {_trim(r.get('subject'), 100)}", "link": "/chat"})
    for r in _rows(_p(data_dir, ("state", "orchestra-registry.db")),
                   "SELECT root, generation, promoted_at FROM generations WHERE promoted_at IS NOT NULL "
                   "ORDER BY promoted_at DESC LIMIT 50"):
        t = epoch(r.get("promoted_at"))
        if t is not None and t > at:
            out.append({"kind": "rotation", "at": t, "agent": r.get("root"),
                        "text": f"{r.get('root')} moved to generation {r.get('generation')}",
                        "link": f"/agents/{r.get('root')}"})
    dated = sorted((m for m in out if m["at"] is not None), key=lambda m: m["at"], reverse=True)
    return [dict(m, at=iso(m["at"])) for m in dated]


def down_now(data_dir, agents):
    """Registered AGENT seats that are down NOW. Kept OUT of `moved`: the fleet scan has no crash time, so a
    seat down since before the anchor would read as news. A service seat (runtime "service") is never listed:
    its liveness is its declared probe, not a tmux pane (identity_store/status_reconcile.py). `crashed` is
    only ever set on registered seats, and long-dead relics read offline (compute_agents)."""
    services = _service_ids(data_dir)
    return [{"kind": "crash", "agent": a.get("id"), "text": f"{a.get('id')} is down", "link": f"/agents/{a.get('id')}"}
            for a in agents
            if a.get("id") not in services and not a.get("unregistered")
            and a.get("state") in ("crashed", "stopped")]


_NOT_DOWN_STATUSES = ("retired", "parked", "archived")


def _service_ids(data_dir):
    """Registry seats that can never be "down": runtime "service" (liveness is a declared probe), and seats
    whose own registry status says they are retired, parked or archived (not supposed to be running)."""
    try:
        agents = json.loads(open(_p(data_dir, ("registry.json",))).read()).get("agents") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    if not isinstance(agents, dict):
        return set()
    return {k for k, v in agents.items() if isinstance(v, dict)
            and (v.get("runtime") == "service" or v.get("status") in _NOT_DOWN_STATUSES)}


# ---- summary ----------------------------------------------------------------------------------------

def anchor_id(anchor):
    return "a_" + hashlib.sha256(f"{anchor['kind']}|{anchor['ref']}".encode()).hexdigest()[:12]


def rules_summary(moved):
    if not moved:
        return "Nothing moved since then."
    head = "; ".join(m["text"] for m in moved[:3])
    more = len(moved) - 3
    return head + (f"; and {more} more." if more > 0 else ".")


def _safe(text, scan):
    """The text, or None when the secret scan flags it (agent_file.scan_secrets: the product's own scanner)."""
    if text is None:
        return None
    try:
        return None if scan(text.encode("utf-8")) else text
    except Exception:   # noqa: BLE001 — a scanner that breaks must not let text through
        return None


def summary(data_dir, aid, moved, *, scan, write_brain=None):
    """The cached summary for this anchor. The cache is keyed on the anchor id and holds the moved count it
    was written for; a new anchor or a changed count is a miss. A miss returns the rules sentence now and,
    when write_brain is given, hands (aid, moved) to it to write a better one later (the gateway runs it off
    the request path). Every text is secret-scanned before it is stored or served."""
    path = os.path.join(_p(data_dir, SUMMARY_DIR), f"{aid}.json")
    try:
        cached = json.loads(open(path).read())
    except (OSError, ValueError):
        cached = None
    if isinstance(cached, dict) and cached.get("moved_count") == len(moved) and _safe(cached.get("text"), scan):
        return {"text": cached["text"], "anchor_id": aid, "generated_at": cached.get("generated_at"),
                "source": cached.get("source", "rules")}
    text = _safe(rules_summary(moved), scan) or f"{len(moved)} things moved since then."
    store_summary(data_dir, aid, len(moved), text, "rules", scan=scan)
    if write_brain is not None:
        write_brain(aid, moved)
    return {"text": text, "anchor_id": aid, "generated_at": iso(time.time()), "source": "rules"}


def store_summary(data_dir, aid, moved_count, text, source, *, scan):
    text = _safe(text, scan)
    if text is None:
        return False
    d = _p(data_dir, SUMMARY_DIR)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump({"moved_count": moved_count, "text": text, "source": source,
                   "generated_at": iso(time.time())}, f)
    os.replace(tmp, os.path.join(d, f"{aid}.json"))
    return True


# ---- the card ---------------------------------------------------------------------------------------

def build(data_dir, agents, pending_cards, *, mode="action", scan, write_brain=None):
    anchor = pick_anchor(data_dir, mode)
    if anchor is None:
        if mode == "screen":
            return {"ok": True, "anchor": None, "reason": "no_presence_yet",
                    "moved": [], "moved_count": 0, "down_now": down_now(data_dir, agents), "summary": None}
        # No action of yours yet (a fresh install): "Welcome back" with everything since the install started
        # (RFC 0002 §5.2), i.e. every dated event, newest first.
        moved = moved_since(data_dir, 0.0, pending_cards)
        return {"ok": True, "anchor": None, "reason": "no_action_yet", "moved": moved[:MOVED_MAX],
                "moved_count": len(moved), "down_now": down_now(data_dir, agents), "summary": None}
    moved = moved_since(data_dir, anchor["at"], pending_cards)
    aid = anchor_id(anchor)
    out_anchor = {k: anchor.get(k) for k in ("kind", "surface", "device", "device_source", "said", "target")}
    out_anchor["surface"] = norm_surface(out_anchor.get("surface"))
    out_anchor["said"] = _safe(out_anchor.get("said"), scan)
    out_anchor.update(id=aid, at=iso(anchor["at"]))
    if anchor["kind"] != "screen":
        out_anchor["outcome"] = outcome(anchor, agents, pending_cards)
        if out_anchor["outcome"].get("summary") is not None:
            out_anchor["outcome"]["summary"] = _safe(out_anchor["outcome"]["summary"], scan)
    shown = moved[:MOVED_MAX]
    for m in shown:
        m["text"] = _safe(m["text"], scan) or "(hidden: looked like a secret)"
    return {"ok": True, "anchor": out_anchor, "moved": shown, "moved_count": len(moved),
            "down_now": down_now(data_dir, agents),
            "summary": summary(data_dir, aid, moved, scan=scan, write_brain=write_brain)}


# ---- writers the gateway calls ----------------------------------------------------------------------

def record_presence(data_dir, key, *, label=None, surface=None, now=None):
    """Store {label, surface, foreground_at} for one device key, debounced (PRESENCE_DEBOUNCE_S): clients send
    on every foreground. Only these three fields; nothing from the screen (gm condition)."""
    now = time.time() if now is None else now
    path = _p(data_dir, PRESENCE)
    try:
        cur = json.loads(open(path).read())
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        cur = {}
    prev = cur.get(key) or {}
    if (epoch(prev.get("foreground_at")) or 0) > now - PRESENCE_DEBOUNCE_S:
        return False
    cur[key] = {"label": _trim(label, 64) if label else None, "surface": _trim(surface, 16) if surface else None,
                "foreground_at": iso(now)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(cur, f)
    os.replace(tmp, path)
    return True


LEDGER_MAX_BYTES = 2 * 1024 * 1024


def record_message(data_dir, target, said, *, device=None, surface=None, scan, now=None, person=None):
    """Append one operator chat send to the operator-actions ledger (gm msg_71cd9caa). The gateway calls it
    ONLY after a DELIVERED or HELD /agent-message. `said` is cut to SAID_MAX and secret-scanned: a flagged
    excerpt is stored as null, never in part. `device` is the gateway's "<device_id>:<label>" (None for the
    fleet bearer), never the token. The file is 0600, rotated at LEDGER_MAX_BYTES keeping one old file, and
    read by this module only."""
    path = _p(data_dir, LEDGER)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) >= LEDGER_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    row = {"kind": "message", "at": iso(time.time() if now is None else now), "target": str(target)[:200],
           "said": _safe(_trim(said), scan), "device": device, "surface": _trim(surface, 16) if surface else None}
    if person is not None:
        row["person"] = bool(person)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return row


def record_terminal(data_dir, target, event, *, device=None, surface=None, person=None, code=None,
                    reason=None, now=None):
    """Append one live-terminal open or close (GET /terminal/ws) to the operator-actions ledger: who
    (`device` "<id>:<label>", `surface`, `person`) attached to which seat and when, and at the close its
    code. The socket bypasses the verified-inject path (/agent-message's read-back), so these rows are
    the only trace of it. Never a keystroke or a byte of output: the row has no text field at all."""
    path = _p(data_dir, LEDGER)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) >= LEDGER_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    row = {"kind": "terminal", "event": "close" if event == "close" else "open",
           "at": iso(time.time() if now is None else now), "target": str(target)[:200],
           "device": device, "surface": _trim(surface, 16) if surface else None}
    if person is not None:
        row["person"] = bool(person)
    if code is not None:
        row["code"] = int(code)
    if reason:
        row["reason"] = _trim(str(reason), 64)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return row


_LAST_SENT_CACHE: dict = {}


def last_person_message_at(data_dir):
    """{target: ISO time} of the newest message a PERSON sent each agent through the gateway (a
    paired device, or a surface that says it is a person's). Agent mail never passes here, and a
    send by automation is marked person:false. A row written before that mark counts only when it
    carries a device or a surface, which automation never sends.

    Reads the whole ledger and its one rotated file (record_message keeps each under
    LEDGER_MAX_BYTES), so an agent last talked to long ago is still found; cached by each file's
    (size, mtime), which only change when someone sends."""
    base = _p(data_dir, LEDGER)
    paths = [base + ".1", base]
    key = []
    for path in paths:
        try:
            st = os.stat(path)
            key.append((path, st.st_size, st.st_mtime_ns))
        except OSError:
            key.append((path, None, None))
    key = tuple(key)
    hit = _LAST_SENT_CACHE.get(str(data_dir))
    if hit and hit[0] == key:
        return dict(hit[1])
    out = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("kind") != "message":
                continue
            person = e.get("person")
            if person is False or (person is None and not (e.get("device") or e.get("surface"))):
                continue
            at, target = e.get("at"), e.get("target")
            if isinstance(at, str) and isinstance(target, str) and epoch(at) is not None:
                if target not in out or epoch(at) > epoch(out[target]):
                    out[target] = at
    _LAST_SENT_CACHE[str(data_dir)] = (key, out)
    return dict(out)
