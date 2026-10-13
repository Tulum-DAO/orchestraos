"""The "While you were away" card's data (RFC 0002, docs/rfcs/0002-away-card.md).

Pure over a data dir and the gateway's agents list, so tests drive it with a temp dir. The gateway's
GET /away is a thin wrapper (scripts/watch_gateway.py handle_away).

  anchor   your last real action: an answered card, a voice call, or a chat message (anchor="action"),
           or the last time an app of yours was on screen (anchor="screen")
  moved    what happened after it: new cards for you, tasks done, rotations, messages to you, and
           registered seats that are crashed now
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
_JOURNALS_SCANNED = 40
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


def _device_label(stamp):
    """'<device_id>:<label>' (answer_device) -> label; None for None or the bare id."""
    if not stamp or ":" not in str(stamp):
        return None
    return str(stamp).split(":", 1)[1].strip() or None


# ---- anchor candidates ----------------------------------------------------------------------------

def _answer_candidate(data_dir):
    rows = _rows(_p(data_dir, ("state", "tasks.db")),
                 "SELECT * FROM approval_requests WHERE status='answered' AND answered_at IS NOT NULL "
                 "ORDER BY answered_at DESC LIMIT 50")
    for r in rows:
        # The operator's answers only: a self-authored gate (answered_by=<agent>) or a CLI answer is not you.
        if r.get("answered_by") not in (None, "", "operator") or r.get("answer_surface") == "agent_cli":
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
        return {"state": "waiting_on_you", "link": link}
    if state == "idle" and (row.get("last_used_ts") or 0) > anchor["at"]:
        return {"state": "done", "link": link}
    return {"state": "unknown", "link": link}


# ---- moved since ------------------------------------------------------------------------------------

def moved_since(data_dir, at, agents, pending_cards):
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
    services = _service_ids(data_dir)
    for a in agents:
        # Crashed NOW: the same rule as /briefing. A registered agent seat that is `crashed`, or `stopped` in
        # its own pane; never an unregistered pane or a seat whose runtime is "service" (no agent CLI, so it
        # always reads `stopped`). The fleet scan carries no crash time, so these are current state, undated.
        if a.get("state") == "crashed" or (a.get("state") == "stopped" and not a.get("unregistered")
                                           and a.get("id") not in services):
            out.append({"kind": "crash", "at": None, "agent": a.get("id"),
                        "text": f"{a.get('id')} is down", "link": f"/agents/{a.get('id')}"})
    dated = sorted((m for m in out if m["at"] is not None), key=lambda m: m["at"], reverse=True)
    crashes = [m for m in out if m["at"] is None]
    return [dict(m, at=iso(m["at"]) if m["at"] is not None else None) for m in crashes + dated]


def _service_ids(data_dir):
    try:
        agents = json.loads(open(_p(data_dir, ("registry.json",))).read()).get("agents") or {}
    except (OSError, ValueError, AttributeError):
        return set()
    return {k for k, v in agents.items() if isinstance(v, dict) and v.get("runtime") == "service"} \
        if isinstance(agents, dict) else set()


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
        return {"ok": True, "anchor": None,
                "reason": "no_presence_yet" if mode == "screen" else "no_action_yet",
                "moved": [], "moved_count": 0, "summary": None}
    moved = moved_since(data_dir, anchor["at"], agents, pending_cards)
    aid = anchor_id(anchor)
    out_anchor = {k: anchor.get(k) for k in ("kind", "surface", "device", "device_source", "said", "target")}
    out_anchor["said"] = _safe(out_anchor.get("said"), scan)
    out_anchor.update(id=aid, at=iso(anchor["at"]))
    if anchor["kind"] != "screen":
        out_anchor["outcome"] = outcome(anchor, agents, pending_cards)
    shown = moved[:MOVED_MAX]
    for m in shown:
        m["text"] = _safe(m["text"], scan) or "(hidden: looked like a secret)"
    return {"ok": True, "anchor": out_anchor, "moved": shown, "moved_count": len(moved),
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


def record_message(data_dir, target, said, *, device=None, surface=None, scan, now=None):
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return row
