"""attention_detect.py — failed transitions and the observations timeout rules act on. Pure builders plus
apply_tick(), which opens, refreshes and clears rows in attention_store.

The detector only OBSERVES. It records failed conditions itself (crashed, out of usage, signed out), each
once per condition and cleared once. "No progress for X" and "unanswered for X" are not events: they are
observations (NoProgress, Unanswered) handed to the person's timeout rules (attention_rules.py), which
decide whether anything happens and when. There is no fixed stuck threshold here.

Scan success is tracked per source ('agents', 'pane:<seat>', 'headless:<seat>', 'perm', 'ledger'). A
condition counts as absent only when the source that would have reported it scanned successfully this
tick; a failed or skipped source leaves its rows untouched. Rows tied to a question resolve from the
ledger / questionnaire store (the authoritative status), never from absence, and rule-made no-progress
rows are resolved by the rules engine when the agent moves again.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import red_alert  # noqa: E402

_CLASS_TO_REASON = {"out_of_usage": "usage_limit", "login_screen": "signed_out"}
_TITLES = {"crashed": "{a} stopped (crashed)", "usage_limit": "{a} is out of usage",
           "signed_out": "{a} is signed out"}


@dataclass(frozen=True)
class Condition:
    kind: str
    agent: str
    reason: str
    key: str
    title: str
    body: str | None = None
    source_id: str | None = None
    source: str = "agents"


@dataclass(frozen=True)
class NoProgress:
    agent: str
    since: datetime


@dataclass
class Unanswered:
    item: dict
    since: datetime


def _failed(agent, reason, source, cfg=None):
    if cfg is not None and reason not in cfg.failed_reasons:
        return None
    return Condition("failed", agent, reason, f"failed:{agent}:{reason}", _TITLES[reason].format(a=agent),
                     source=source)


def from_agents(rows, cfg):
    out = []
    for r in rows or []:
        if r.get("state") == "crashed" and r.get("id"):
            c = _failed(r["id"], "crashed", "agents", cfg)
            if c:
                out.append(c)
    return out


def from_screen(agent, text, cfg=None):
    """A tmux seat's screen: red_alert's CLI-banner classes (prose never classifies)."""
    got = red_alert.classify_screen(text or "")
    reason = _CLASS_TO_REASON.get(got[0]) if got else None
    return _failed(agent, reason, f"pane:{agent}", cfg) if reason else None


def from_headless_result(agent, ev, cfg=None):
    """A headless seat's newest stream-json `result`: an error whose text matches the same banner rules."""
    if not isinstance(ev, dict) or ev.get("type") != "result" or ev.get("is_error") is not True:
        return None
    text = str(ev.get("result") or "")
    for rx, cls in red_alert._SCREEN_RULES:
        reason = _CLASS_TO_REASON.get(cls)
        if reason and rx.search(text):
            return _failed(agent, reason, f"headless:{agent}", cfg)
    return None


def last_headless_result(events_path, session_id=None, tail_bytes=262144):
    """The newest `result` event of the seat's CURRENT CLI and session, else None.

    Lines are the supervisor's records ({kind: 'sup'|'out', ev?: <stream-json event>}). Only results after the
    last `sup`/`start` record (this CLI process) whose session_id is the seat's current one count, so an old
    error from a previous CLI or session never raises a condition. A missing file is None; other read errors
    raise, so the caller can mark this source's scan failed."""
    p = Path(events_path)
    if not p.exists():
        return None
    with p.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]                                 # the first line may be cut in half
    newest = None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("kind") == "sup" and rec.get("what") == "start":
            newest = None                                 # a new CLI: nothing before it counts
            continue
        ev = rec.get("ev")
        if rec.get("kind") == "out" and isinstance(ev, dict) and ev.get("type") == "result":
            if session_id is None or ev.get("session_id") == session_id:
                newest = ev
    return newest


def _parse_ts(v):
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def no_progress(rows, now, stall_s=600):
    """Stalled agents with the moment progress stopped: the detector calls a seat stalled after stall_s of
    no change, and state_age_s counts from that moment (pane and headless paths alike)."""
    out = []
    for r in rows or []:
        age = r.get("state_age_s")
        if r.get("state") == "stalled" and isinstance(age, (int, float)) and r.get("id"):
            out.append(NoProgress(r["id"], now - timedelta(seconds=int(age) + int(stall_s))))
    return out


def unanswered(waiting_items):
    out = []
    for it in waiting_items or []:
        since = _parse_ts(it.get("created_at"))
        if since is not None:
            out.append(Unanswered(item=it, since=since))
    return out


def _question_item(key):
    return key.split(":question:", 1)[1] if ":question:" in key else None


def _still_pending(item_id, ledger, qstore):
    """True / False from the authoritative store; None when it cannot be told (no store for this id)."""
    if item_id.startswith("qnr_"):
        if qstore is None:
            return None
        q = qstore.get(item_id)
        return bool(q) and q.get("status") == "pending"
    if item_id.startswith("perm:"):
        return None
    if ledger is None:
        return None
    r = ledger.get(item_id)
    return bool(r) and r.get("status") == "pending"


def apply_tick(store, conditions, now, cfg, live_agents, scans=None, ledger=None, qstore=None):
    """Open every present condition; resolve or count down every open failed/stuck row that is absent."""
    scans = scans or {}
    present = set()
    for c in conditions:
        store.open_condition(c.kind, c.agent, c.reason, c.key, c.title, body=c.body, source_id=c.source_id,
                             now=now)
        present.add(c.key)
    agents_ok = scans.get("agents", "ok") == "ok"
    for row in store.open_events(kinds=("failed", "stuck")):
        key = row["dedup_key"]
        if key in present:
            continue
        qid = _question_item(key)
        if qid and not qid.startswith("perm:"):
            pending = _still_pending(qid, ledger, qstore)
            if pending is False:
                store.resolve(row["id"], "answered", now=now)
            continue
        if row["agent"] not in live_agents:
            if agents_ok:
                store.resolve(row["id"], "agent_gone", now=now)
            continue
        if row["kind"] == "stuck" and not qid:
            continue                                      # rule-made: the engine resolves it on progress
        if qid:
            sources = ["perm"]
        elif row["reason"] == "crashed":
            sources = ["agents"]
        else:
            sources = [f"pane:{row['agent']}", f"headless:{row['agent']}"]
        # The agents scan is assumed done unless reported; a pane / headless source only counts when scanned.
        status = [scans.get(s, "ok" if s == "agents" else None) for s in sources]
        status = [s for s in status if s is not None]
        if not status or any(s != "ok" for s in status):
            continue                                      # not scanned, or the scan failed: proves nothing
        if store.note_absent(row["id"]) >= cfg.clear_after_ticks:
            store.resolve(row["id"], "cleared", now=now)
