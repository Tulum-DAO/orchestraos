# resolve_providers.py — impure candidate gatherers for the speech resolver (spec §3.1 field map).
#
# resolve.py is PURE (candidates injected). THIS module does the I/O: it UNIONS a durable-catalog
# provider (Phase 1: live `/agents`) + a LIVE-approvals provider (`/pending-approvals`) + a bounded
# CONTENT provider (the focused/named agent's recent output). All network is via injected callables
# (`gw_get`, `get_output`) so it stays unit-testable.
#
# The three convergence rules (fork 3, so Phase-2 is a ZERO-migration provider swap):
#   1. Namespaced type-prefixed ids  — id = "<kind>:<name>"
#   2. aliases-with-confidence       — [{alias, confidence: exact|curated|inferred}]
#   3. approvals stay a LIVE-UNION source in BOTH phases (never a durable registry catalog type)
# Only the DURABLE-catalog provider (agents) swaps backend in Phase 2; approvals + content unchanged.

import hashlib
from datetime import datetime

from services.arturo.resolve import _STOP
from services.arturo.call_journal import _norm_fuzzy

_MAX_ALIASES = 8
_MIN_ALIAS_LEN = 3


def _salient_aliases(text, confidence="inferred", limit=_MAX_ALIASES):
    """Salient content nouns of `text` as aliases-with-confidence. DOCUMENT-ORDER + first-occurrence
    dedup (deterministic — never a set's arbitrary order), dropping stopwords, bare digits, and very
    short tokens so the earliest meaningful nouns ("recommendations", "options") survive the cap."""
    seen, out = set(), []
    for tok in _norm_fuzzy(text or ""):       # ordered token stream (not a set)
        if (tok in seen or tok in _STOP or tok.isdigit() or len(tok) < _MIN_ALIAS_LEN):
            continue
        seen.add(tok)
        out.append({"alias": tok, "confidence": confidence})
        if len(out) >= limit:
            break
    return out


def _iso_to_epoch(ts):
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# pure entity builders (dict-in → Entity-out; no I/O)
# ---------------------------------------------------------------------------

def approval_entity(row):
    """A pending approval_requests row → Entity{kind:"approval"} (live provider; pending=True)."""
    rid = row.get("id")
    label = (row.get("question") or row.get("summary") or "approval").strip()
    bag = " ".join(str(x) for x in (row.get("question"), row.get("summary"),
                                    row.get("feature")) if x)
    return {
        "kind": "approval", "id": f"approval:{rid}", "label": label,
        "aliases": _salient_aliases(bag),
        "from_agent": row.get("from_agent"), "feature": row.get("feature"),
        "recency_ts": _iso_to_epoch(row.get("created_at")), "pending": True,
    }


def agent_entity(row):
    """A live `/agents` row → Entity{kind:"agent"}. recency = last_used_ts (a live ranking signal
    Arturo owns — NOT the registry's verified_at). pending = has_pending_menu."""
    sess = row.get("tmux_session") or row.get("session") or row.get("id")
    name = row.get("id") or sess
    aliases = []
    if name and name != sess:
        aliases.append({"alias": name, "confidence": "curated"})
    return {
        "kind": "agent", "id": f"agent:{sess}", "label": sess,
        "aliases": aliases,
        "recency_ts": float(row.get("last_used_ts") or 0.0),
        "pending": bool(row.get("has_pending_menu")),
    }


def content_entity(agent, text, now, capture_ts=None):
    """Focused/named agent's recent OUTPUT → Entity{kind:"content"} (§0a-A). label = a short digest,
    aliases = salient nouns in the output ("recommendations"/"options"/"strategy")."""
    text = (text or "").strip()
    h = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()[:8]
    # digest = first meaningful line, trimmed
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    digest = (first_line[:80] or f"{agent}'s recent output").strip()
    return {
        "kind": "content", "id": f"content:{agent}:{h}",
        "label": digest, "aliases": _salient_aliases(text),
        "from_agent": agent,
        "recency_ts": float(capture_ts if capture_ts is not None else now),
        "pending": False,
    }


# ---------------------------------------------------------------------------
# gather_candidates — the union (injected callables; degrades to [] on failure)
# ---------------------------------------------------------------------------

def gather_candidates(gw_get, get_output=None, focused_agent=None, named_agents=None,
                      now=None, include_content=True):
    """Union of live approvals + live agents + bounded content. `gw_get(path)->json|None` and
    `get_output(session)->str` are injected. CONTENT is scanned ONLY for the focused agent plus any
    agents the operator named in the same breath (never a full-fleet output scan — spec §3.1 bounded)."""
    import time as _time
    now = now if now is not None else _time.time()
    cands = []

    # live approvals
    try:
        pa = gw_get("/pending-approvals")
        for row in (pa.get("pending") if isinstance(pa, dict) else None) or []:
            if isinstance(row, dict) and row.get("id"):
                cands.append(approval_entity(row))
    except Exception:
        pass

    # durable catalog (Phase 1: live /agents; Phase 2: registry catalog — same Entity shape)
    try:
        ag = gw_get("/agents")
        for row in (ag.get("agents") if isinstance(ag, dict) else None) or []:
            if isinstance(row, dict) and (row.get("tmux_session") or row.get("id")):
                cands.append(agent_entity(row))
    except Exception:
        pass

    # bounded content: focused agent + explicitly-named agents only
    if include_content and callable(get_output):
        want, seen = [], set()
        for a in [focused_agent] + list(named_agents or []):
            if a and a not in seen:
                seen.add(a)
                want.append(a)
        for a in want:
            try:
                txt = get_output(a)
            except Exception:
                txt = ""
            if txt and txt.strip():
                cands.append(content_entity(a, txt, now))

    return cands
