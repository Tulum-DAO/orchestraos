"""E1 — typed-fact schema for the brain fact bus (spec §2.1).

One normalized shape for every miner's output:
  {fact_id, ts, kind, text, actor, confidence, entities, lanes[],
   provenance, state, expiry_class}

fact_id derives from provenance_hash(provenance, text) — the idempotency key:
re-ingesting the same source row is a no-op by construction.
Free-text answers (A1/A2 contract) ride in text + provenance.answer_text —
never dropped. Lanes default to ["unrouted"] (fail-open, binding).
"""
from __future__ import annotations

import hashlib
import json

KINDS = {"fact", "goal", "decision", "insight", "commitment"}
STATES = {"active", "resolved", "expired", "superseded", "archived", "unverified"}
# Never served by recall: 'superseded' and 'archived' (decayed or set aside, treated like superseded).
# 'unverified' IS served: a fact with no source quote found, flagged and ranked below sourced facts, never
# rendered as anyone's words. services/arturo/facts_recall.py holds the same tuple (it may not import this
# module; a test holds them equal).
UNSERVED_STATES = ("superseded", "archived")
UNSOURCED_STATE = "unverified"


def is_served(state) -> bool:
    return (state or "active") not in UNSERVED_STATES


def is_unsourced(state) -> bool:
    return (state or "") == UNSOURCED_STATE
EXPIRY_CLASSES = {"standard", "durable", "ephemeral"}
_REQUIRED = ("fact_id", "ts", "kind", "text", "actor", "confidence",
             "entities", "lanes", "provenance", "state", "expiry_class")

# --- volatility tier heuristic (spec REVISION 2, fork D -> D3) ---------------
# expiry_class IS the volatility tier: durable=immutable(no decay),
# standard=slow, ephemeral=fluid. Deterministic kind(+provenance)->tier map,
# so the 23k backfill is free, reproducible, and RED-first testable (no LLM,
# no RNG). LLM override is reserved for genuinely-ambiguous cases at distill
# time only; the backfill NEVER consults it.
#
# SAFETY ASYMMETRY (build-gate #2, review): a fluid fact
# mis-tagged durable pollutes recall ~forever, while a durable fact mis-tagged
# slow merely decays a little too fast. So uncertainty ALWAYS defaults to
# 'standard' (slow) — we NEVER default to durable/immutable on a guess.
_SLOW_DEFAULT = "standard"


def classify_expiry_class(kind: str, provenance: dict | None = None) -> str:
    """Deterministic volatility tier for a fact. Total (always returns a valid
    EXPIRY_CLASS) and pure (same inputs -> same output). Uncertain -> slow."""
    prov = provenance or {}
    source_kind = prov.get("source_kind")
    if kind == "decision":
        return "durable"                    # a made decision is a durable record
    if kind in ("goal", "commitment"):
        return "ephemeral"                  # intents / promises churn -> fluid
    if kind == "insight":
        return "standard"                   # observations drift slowly
    if kind == "fact":
        if source_kind == "distilled-knowledge":
            return "durable"                # identity / ownership / roster
        if source_kind == "dag-events":
            return "ephemeral"              # node states change constantly
        return _SLOW_DEFAULT                # dream-facts & unknown -> slow
    return _SLOW_DEFAULT                    # unknown kind -> slow (never durable)


# --- write-side noise filter (spec R2-4) -------------------------------------
# Distiller prompt-echoes / tool banners captured verbatim as kind='fact' — NOT
# knowledge. Mirrors facts_recall._NOISE_PREFIXES (read-side mask) so pocket/voice
# distillation can't amplify them: dropped at WRITE time here, masked read-side
# there (defense in depth). 638 such rows already existed read-side-masked.
NOISE_PREFIXES = (
    "distill this",
    "for each of the following",
    "stop hook feedback",
    "[queue-digest]",
    "emit a json",
    "you are ",
    # hotfix 2026-09-14 (mirrors facts_recall._NOISE_PREFIXES): stop new distiller
    # extraction/classification prompt-echoes from being ingested as kind='fact'.
    "from the conversation transcript below",
    "classify each pocket recording",
    "you extract ",
    "# you are the dream agent",
)


def is_noise_text(text: str) -> bool:
    low = (text or "").lstrip().lower()
    return any(low.startswith(p) for p in NOISE_PREFIXES)


def provenance_hash(provenance: dict, text: str) -> str:
    """Deterministic idempotency hash over the identifying provenance + text."""
    ident = {
        "source_kind": provenance.get("source_kind"),
        "source_id": provenance.get("source_id"),
        "path": provenance.get("path"),
        "text": text,
    }
    blob = json.dumps(ident, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def make_fact(*, ts: str, kind: str, text: str, actor: str, provenance: dict,
              confidence: float = 1.0, entities: dict | None = None,
              lanes: list | None = None, state: str = "active",
              expiry_class: str | None = None,
              superseded_by: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r}; must be one of {sorted(KINDS)}")
    if expiry_class is None:
        expiry_class = classify_expiry_class(kind, provenance)
    fact = {
        "fact_id": "fact_" + provenance_hash(provenance, text)[:16],
        "ts": ts,
        "kind": kind,
        "text": text,
        "actor": actor,
        "confidence": confidence,
        "entities": entities or {"people": [], "companies": [], "projects": []},
        "lanes": lanes or ["unrouted"],
        "provenance": dict(provenance),
        "state": state,
        "expiry_class": expiry_class,
        "superseded_by": superseded_by,
    }
    errs = validate_fact(fact)
    if errs:
        raise ValueError("invalid fact: " + "; ".join(errs))
    return fact


def validate_fact(fact: dict) -> list:
    """Return a list of problems (empty = valid). Never raises."""
    errs = []
    for field in _REQUIRED:
        if field not in fact:
            errs.append(f"missing field: {field}")
    if fact.get("kind") not in KINDS:
        errs.append(f"invalid kind: {fact.get('kind')!r}")
    conf = fact.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
        errs.append(f"confidence out of range [0,1]: {conf!r}")
    if "state" in fact and fact["state"] not in STATES:
        errs.append(f"invalid state: {fact['state']!r}")
    if "expiry_class" in fact and fact["expiry_class"] not in EXPIRY_CLASSES:
        errs.append(f"invalid expiry_class: {fact['expiry_class']!r}")
    lanes = fact.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        errs.append("lanes must be a non-empty list (fail-open = ['unrouted'])")
    if "provenance" in fact and not isinstance(fact["provenance"], dict):
        errs.append("provenance must be a dict")
    return errs
