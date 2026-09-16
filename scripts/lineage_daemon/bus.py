"""WS-A hook-event message BUS — transport built against the LOCKED schema.

DEC-1786731957 CONSENSUS_REACHED; event-schema LOCKED with platform-builder
(commit 7c9865b1f, event_schema.py PB-versioned). This is the bus TRANSPORT — my
lane. It consumes PB's pure schema (build_raw_event / validate_raw_event /
validate_event / advance_receipt) and NEVER edits it.

The five locked pieces (build+test only; ARMING — the live hook rewrite + cron
install — is the operator-gated and deliberately NOT done here):
  1. transport WRITER — append_event: fail-closed validate_raw_event, then append
     ONE json line to state/event-stream/events-YYYY-MM-DD.jsonl (immutable,
     greppable; NOT sqlite — a single-writer sqlite bus is the jarvis-ledger
     corruption SPOF we locked AGAINST).
  2. turn-boundary CONSUMER — consume_beat: read the hot stream, dedup by event_id,
     resolve+stamp agent at INGEST (Q2), strict-validate, advance receipt to
     'delivered'.
  3. msg_store PROMOTION — promote: actionable events (turn_ended primarily) become
     msg_store delivery ROWS (source-of-truth), keyed by receipt.nonce so a
     re-delivered event can't double-promote (H4/H8 idempotency).
  4. advance_receipt WIRING — stamp: apply PB's monotonic advance_receipt to the
     delivery row's receipt (JSONL stays immutable; the mutable ladder lives on the
     msg_store row — Q1 hybrid).
  5. cron liveness-WATCHDOG — watchdog_check: the bus is a SPOF; a stale beat
     cursor => presumed-down => restart+re-page (§4.5 flag 1). Pure predicate here;
     the cron that ACTS on it is the operator-gated.

Dead-bus discipline (§4.5 flag 1): the dumb hook keeps APPENDING to JSONL (never
lost) and msg_store is untouched (delayed-not-lost); on restart the consumer drains
the backlog from its cursor. Everything is pure over injected seams (stream_dir,
now, resolve_fn, send_fn, cursor) so tests are hermetic — no real hook, tmux, DB,
or cron.
"""
import json
import os
import time

from scripts.focus_registry.event_schema import (
    validate_raw_event, validate_event, advance_receipt,
)

# The actionable types the bus promotes to msg_store delivery rows.
#
# INTERIM (gm-directed 2026-08-15): OBSERVE-ONLY — EMPTY, so NOTHING is promoted.
# The placeholder promote wrote self-addressed "[bus] <type> for X" rows (a phantom
# flood, 41 rows), because promoting a bare turn_ended is NOT mail delivery. The
# drain still consumes/dedups/advances-receipts (the stream is the liveness record);
# it just delivers no rows. The REAL semantics (resolved turn_ended for X -> deliver
# X's PENDING msg_store queue, the hook-boundary router) is a separate build gated on
# gm+agy congruence (moved surface). Until that lands + is armed, this stays empty.
ACTIONABLE_TYPES = ()

STREAM_SUBDIR = os.path.join("state", "event-stream")
BEAT_MAX_GAP_S = 180.0   # a consumer beat gap beyond this => bus presumed down (§4.5 flag 1)


# --- 1. transport WRITER ------------------------------------------------------

def _day_of(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def stream_path(stream_dir, now=None, day=None):
    """The per-UTC-day append file: <stream_dir>/events-YYYY-MM-DD.jsonl."""
    day = day or _day_of(now)
    return os.path.join(stream_dir, f"events-{day}.jsonl")


def append_event(raw_event, *, stream_dir, now=None):
    """Append ONE raw event as a json line to today's stream file. Fail-closed:
    a malformed raw event is REJECTED (a malformed event on a bus is unactionable,
    not merely lossy — the one place strict beats fail-open). Returns
    {appended, path, errors}."""
    v = validate_raw_event(raw_event)
    if not v["valid"]:
        return {"appended": False, "path": None, "errors": v["errors"]}
    path = stream_path(stream_dir, now=now)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(raw_event, separators=(",", ":")) + "\n")
    return {"appended": True, "path": path, "errors": []}


def read_events(stream_dir, *, now=None, days=None):
    """Read parsed events across the hot window (default: just today). `days` is an
    explicit list of 'YYYY-MM-DD' strings to read in order. Malformed lines are
    skipped (logged via the return's `skipped_lines`), never crash the beat."""
    if days is None:
        days = [_day_of(now)]
    events, skipped = [], 0
    for day in days:
        path = stream_path(stream_dir, day=day)
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except (ValueError, json.JSONDecodeError):
                    skipped += 1
    return {"events": events, "skipped_lines": skipped}


# --- 2/3/4. INGEST + PROMOTION + receipt wiring -------------------------------

def ingest(raw_event, *, resolve_fn, now=None):
    """Resolve pane/session -> canonical agent at INGEST (Q2), stamp it, strict
    validate. resolve_fn(pane, session_id, cwd) -> canonical agent id or None.
    Returns {ok, event, errors}. An unresolved agent (rotation gap / dead session)
    is NOT ingestable -> ok False (the event stays in the raw stream for a later
    drain)."""
    rv = validate_raw_event(raw_event)
    if not rv["valid"]:
        return {"ok": False, "event": None, "errors": rv["errors"]}
    agent = resolve_fn(raw_event.get("pane"), raw_event.get("session_id"),
                       raw_event.get("cwd"))
    if not agent:
        return {"ok": False, "event": None, "errors": ["agent unresolved at ingest"]}
    event = dict(raw_event)
    event["agent"] = agent
    sv = validate_event(event)
    return {"ok": sv["valid"], "event": event if sv["valid"] else None,
            "errors": sv["errors"]}


def stamp(receipt_bearer, to_status):
    """Apply PB's monotonic advance_receipt to a receipt-bearing dict (a stream
    event OR a delivery row). Returns a NEW dict with the advanced receipt; a
    stale/lower status is a no-op (never regresses)."""
    out = dict(receipt_bearer)
    out["receipt"] = advance_receipt(out.get("receipt", {}), to_status)
    return out


def build_delivery_row(event):
    """The msg_store delivery ROW for an ingested actionable event. Keyed by the
    receipt.nonce (idempotency): msg_id = 'evt-<nonce>' so a re-delivered event
    with the same nonce maps to the SAME row and cannot double-promote."""
    nonce = event["receipt"]["nonce"]
    return {
        "msg_id": f"evt-{nonce}",
        "nonce": nonce,
        "event_id": event["event_id"],
        "to_agent": event["agent"],
        "type": event["type"],
        "payload": event.get("payload", {}),
        "receipt": event["receipt"],
    }


def promote(event, *, send_fn, now=None):
    """Promote ONE ingested actionable event to a msg_store delivery row (the
    source-of-truth). Stamps the row's receipt to 'delivered' (the bus durably
    enqueued it; the downstream consumer later stamps read/processing/acked).
    send_fn(row) -> row_id (the msg_store seam; injected). Returns
    {promoted, row_id, nonce}."""
    row = build_delivery_row(event)
    row = stamp(row, "delivered")
    row_id = send_fn(row)
    return {"promoted": True, "row_id": row_id, "nonce": row["nonce"]}


# --- 2. turn-boundary CONSUMER beat -------------------------------------------

def consume_beat(events, *, resolve_fn, send_fn, seen=None,
                 actionable=ACTIONABLE_TYPES, now=None):
    """The turn-boundary consumer: drain a batch of raw stream events into delivery
    rows. IDEMPOTENT — an event_id already in `seen` is skipped, so re-running over
    an overlapping window (a restart drain) can't double-deliver.

    For each event: dedup -> ingest (resolve+stamp agent, strict validate) ->
    advance receipt to 'delivered' -> if actionable, promote to a msg_store row.
    Returns {processed, promoted, skipped, seen} where seen is the updated set.

    This is CONSUME-only: it moves events into delivery rows; it kills/spawns/pages
    NOTHING. Arming the live hook + cron is the operator-gated."""
    seen = set(seen or ())
    processed, promoted, skipped = [], [], []
    for ev in events:
        eid = ev.get("event_id")
        if not eid or eid in seen:
            skipped.append({"event_id": eid, "reason": "duplicate-or-idless"})
            continue
        ing = ingest(ev, resolve_fn=resolve_fn, now=now)
        if not ing["ok"]:
            skipped.append({"event_id": eid, "reason": "; ".join(ing["errors"])})
            continue
        delivered = stamp(ing["event"], "delivered")
        record = {"event_id": eid, "agent": delivered["agent"],
                  "type": delivered["type"], "status": delivered["receipt"]["status"]}
        if delivered["type"] in actionable:
            p = promote(delivered, send_fn=send_fn, now=now)
            record["row_id"] = p["row_id"]
            promoted.append(p)
        processed.append(record)
        seen.add(eid)
    return {"processed": processed, "promoted": promoted, "skipped": skipped,
            "seen": seen}


# --- 5. cron liveness WATCHDOG (§4.5 flag 1) ----------------------------------

def beat_is_stale(last_beat_ts, now, max_gap_s=BEAT_MAX_GAP_S):
    """True if the consumer beat hasn't run within max_gap_s — the bus (a SPOF) is
    presumed down. No last beat at all (None) counts as stale."""
    if last_beat_ts is None:
        return True
    return (now - last_beat_ts) > max_gap_s


def watchdog_check(cursor, now, max_gap_s=BEAT_MAX_GAP_S):
    """DECIDE what the liveness watchdog would do — pure; it does NOT restart or
    page (arming the operator-gated). Returns {live, gap_s, action} where action is 'ok' or
    'restart-and-repage'. The the operator-gated cron entry calls this and, only when armed,
    restarts the bus + re-pages gm — so dead-bus is detected, not silent."""
    last = (cursor or {}).get("last_beat_ts")
    gap = None if last is None else (now - last)
    stale = beat_is_stale(last, now, max_gap_s)
    return {"live": not stale, "gap_s": gap,
            "action": "restart-and-repage" if stale else "ok"}


# --- cursor persistence (the daemon's drain pointer) --------------------------

def load_cursor(path):
    """Read the persisted consumer cursor {last_beat_ts, seen:[event_ids]}. Missing
    /corrupt -> a fresh cursor (degrade gracefully; a lost cursor just re-drains,
    and consume_beat's event_id dedup keeps that idempotent)."""
    try:
        with open(path) as fh:
            d = json.load(fh)
            d.setdefault("last_beat_ts", None)
            d.setdefault("seen", [])
            return d
    except Exception:  # noqa: BLE001
        return {"last_beat_ts": None, "seen": []}


def save_cursor(path, cursor):
    """Atomically persist the cursor (write-temp + rename). `seen` is bounded to the
    last SEEN_CAP ids so the cursor can't grow unbounded."""
    SEEN_CAP = 5000
    out = dict(cursor)
    out["seen"] = list(out.get("seen", []))[-SEEN_CAP:]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh)
    os.replace(tmp, path)


# --- default msg_store seam (real IO; used only from an ARMED path) -----------

def default_send_fn(row, *, orchestra_dir=None, from_agent="event-bus"):
    """Write a delivery row to msg_store (source-of-truth). msg_id='evt-<nonce>' so
    re-promotion is a PK no-op (idempotent). Used only when armed; tests inject a
    fake send_fn instead."""
    import sys
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    sys.path.insert(0, od)
    from msg_store import MessageStore
    st = MessageStore()
    return st.send(
        from_agent=from_agent, to_agent=row["to_agent"], type="bus_event",
        subject=f"[bus] {row['type']} for {row['to_agent']}",
        body=json.dumps({"event_id": row["event_id"], "type": row["type"],
                         "payload": row["payload"]}),
        priority="medium", source="event-bus",
        metadata={"nonce": row["nonce"], "event_id": row["event_id"],
                  "event_type": row["type"], "receipt": row["receipt"]},
        msg_id=row["msg_id"])
