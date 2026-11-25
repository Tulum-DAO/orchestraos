"""E1 — lane context read API: the PM contract (spec §E2).

get_context(lane, kinds, since, limit) -> {
    "api_version": 1,                 # consumers capability-gate on this (D5 analog)
    "freshness": {content_hash, last_source_event_ts, ingest_lag_s,
                  ok, error, sources},   # LOAD-BEARING, on EVERY read (binding)
    "facts": [ranked typed facts],
}

Freshness semantics: ingest_lag_s = seconds since the OLDEST source's last
ingest run (worst-case staleness). A missing/empty db reads explicitly
not-ok — never a silent empty (W1 analog). Content currency of the SOURCES
is freshness.py's job (E3); this header proves bus currency.

Ranking (fixed v1 weights): recency x confidence x lane-affinity
(direct 1.0 > alias 0.8 > co-mention 0.6). Active decisions/commitments are
pinned — they never age out of top-N until their state leaves 'active'.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import bus
from facts_schema import is_served

API_VERSION = 1
_AFFINITY_WEIGHT = {"direct": 1.0, "alias": 0.8, "co-mention": 0.6}
_RECENCY_HALF_LIFE_S = 3 * 24 * 3600      # 3 days
_PINNED_KINDS = {"decision", "commitment"}


def _epoch(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return 0.0


def _score(fact: dict, lane: str, now: float) -> float:
    age = max(0.0, now - _epoch(fact["ts"]))
    recency = 0.5 ** (age / _RECENCY_HALF_LIFE_S)
    affinity = _AFFINITY_WEIGHT.get(
        (fact.get("provenance") or {}).get("lane_affinity", {}).get(lane),
        1.0)  # lanes set without affinity metadata rank as direct
    return recency * float(fact.get("confidence", 1.0)) * affinity


def _freshness(db_path: str, facts: list) -> dict:
    content = hashlib.sha256(json.dumps(
        [f["fact_id"] for f in facts], sort_keys=True).encode()).hexdigest()
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"facts.db missing: {db_path}",
                "content_hash": content, "last_source_event_ts": None,
                "ingest_lag_s": None, "sources": {}}
    meta = bus.ingest_meta(db_path)
    if not meta:
        return {"ok": False, "error": "facts.db has no ingest_meta — never ingested",
                "content_hash": content, "last_source_event_ts": None,
                "ingest_lag_s": None, "sources": {}}
    now = time.time()
    sources, worst_lag, latest_event = {}, 0.0, None
    for kind, m in meta.items():
        lag = max(0.0, now - _epoch(m["last_ingest_at"]))
        sources[kind] = {**m, "ingest_lag_s": round(lag, 1)}
        worst_lag = max(worst_lag, lag)
        ev = m.get("last_source_event_ts")
        if ev:
            latest_event = max(latest_event or ev, ev)
    return {"ok": True, "error": None, "content_hash": content,
            "last_source_event_ts": latest_event,
            "ingest_lag_s": round(worst_lag, 1), "sources": sources}


def get_context(lane: str, *, db_path: str | None = None,
                kinds: list | None = None, since: str | None = None,
                limit: int | None = None) -> dict:
    """Ranked facts for one lane + the load-bearing freshness header."""
    db_path = db_path or bus.DEFAULT_DB
    if not os.path.exists(db_path):
        return {"api_version": API_VERSION,
                "freshness": _freshness(db_path, []), "facts": []}

    rows = bus.query(db_path, kinds=kinds, since=since)
    in_lane = [f for f in rows if lane in f["lanes"] and is_served(f["state"])]
    now = time.time()
    ranked = sorted(in_lane, key=lambda f: _score(f, lane, now), reverse=True)

    if limit is not None and len(ranked) > limit:
        top = ranked[:limit]
        pinned = [f for f in ranked[limit:]
                  if f["kind"] in _PINNED_KINDS and f["state"] == "active"]
        if pinned:
            # pinned items displace the lowest-scored unpinned tail entries
            keep = [f for f in top if not (f["kind"] in _PINNED_KINDS
                                           and f["state"] == "active")]
            already = [f for f in top if f not in keep]
            room = max(0, limit - len(already) - len(pinned))
            top = already + pinned + keep[:room]
            top = sorted(top, key=lambda f: _score(f, lane, now), reverse=True)
        ranked = top[:limit] if len(top) > limit else top

    return {"api_version": API_VERSION,
            "freshness": _freshness(db_path, ranked), "facts": ranked}
