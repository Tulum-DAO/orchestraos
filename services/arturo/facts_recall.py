"""Arturo in-call FACTS recall — bounded read-side wiring to state/brain/facts.db.

WHY (semantic-memory-audit 2026-09-14): Arturo's semantic-recall preamble queries a
docs-only vector store that was indexed ONCE on 2026-09-07 and never refreshed. Meanwhile
state/brain/facts.db holds ~23.5k structured facts (goals/insights/decisions/facts)
refreshed DAILY by an active cron (scripts/brain/ingest_run.py 04:15 + 08:30) — the single
largest, freshest knowledge source on the box — and Arturo had ZERO wiring to it. This
module adds an ADDITIVE, read-only, bounded retrieval that surfaces the most relevant fresh
facts for the caller's question, rendered as a small FACTS block appended alongside the
existing semantic RECALL block. It never replaces or reranks semantic recall — the two are
independent, separately-gated blocks.

Posture (mirrors semantic_recall.py's safety contract):
  - INERT unless ARTURO_FACTS_RECALL=1 (checked before any work). Flag off = no-op, no DB open.
  - READ-ONLY: opens facts.db read-only (mode=ro); never writes, never touches the cron.
  - BOUNDED for the live voice path: keyword-scored over a recency-ordered candidate window
    (CANDIDATE_LIMIT rows via idx_facts_ts), returns top-K. Measured ~40-100ms on the live
    23.5k-row db; runs behind a HARD BUDGET WALL in a worker thread + SINGLE-FLIGHT, so it
    can never stall or overlap a live turn — on timeout the turn proceeds with no FACTS block.
  - Every failure/skip path degrades to '' (empty) — never raises into the turn.
  - Prompt-echo / tool-output ingestion artifacts are filtered so only real facts surface.
"""
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("arturo-facts-recall")

FLAG = "ARTURO_FACTS_RECALL"
L2_FLAG = "ARTURO_WORLDVIEW_L2"   # P1.d (DEC-1789388743247449): FLAG-INERT until the
                                  # gm-gated P1.e flip — default off = legacy facts path
                                  # byte-identical, zero reads of beliefs/statements.
BUDGET_ENV = "ARTURO_FACTS_RECALL_BUDGET_MS"
DEFAULT_BUDGET_MS = 250
K = 5                         # facts rendered
CANDIDATE_LIMIT = 250        # rows materialized before scoring (bounded scan cap)
MAX_KEYWORDS = 6
MIN_QUERY_CHARS = 8
MIN_KEYWORD_LEN = 3
SNIPPET_CHARS = 160
MAX_CHARS = 250 * 4          # ~250-token hard clamp, same budget shape as semantic recall

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR", Path.home() / "scripts/agent-orchestra"))

# Ingestion artifacts / prompt echoes that pollute kind='fact'. These are NOT knowledge —
# they are the distiller's own instruction prompts and tool banners captured verbatim.
_NOISE_PREFIXES = (
    "distill this",
    "for each of the following",
    "stop hook feedback",
    "[queue-digest]",
    "emit a json",
    "you are ",
    # hotfix 2026-09-14 (the operator live-test: "last conversation with Alex" / "last
    # thing about Noah" returned distiller prompt-echoes): these extraction/
    # classification prompt templates were captured verbatim as kind='fact' and
    # crowded real Noah/Alex facts out of recall. Add the uncaught prefixes.
    "from the conversation transcript below",
    "classify each pocket recording",
    "you extract ",
    "# you are the dream agent",
)

# Stopwords + question scaffolding — dropped so the LIKE terms are the real topic tokens.
_STOP = set(
    "what whats the whats a an is are was were on with about of to for in and or how "
    "i you he she it they we me my his her their our your can do does did will would "
    "should could im ive got get going up down any some this that these those there "
    "here whos who whom which when where why not no yes going tell give show know "
    "whats going lately currently now today".split()
)

_HEADER = (
    "=== FACTS (current fleet knowledge from the daily fact store — relevant to what the operator just said) ===\n"
    "These are FRESH structured facts (goals/decisions/insights) distilled from recent activity.\n"
    "Prefer them for 'what's going on with X' questions; they are more current than the RECALL docs above."
)

# single-flight so overlapping turns never double-open the db on the voice path
_inflight = threading.BoundedSemaphore(1)


def _reset_for_tests():
    global _inflight
    _inflight = threading.BoundedSemaphore(1)


def enabled():
    return os.environ.get(FLAG, "") == "1"


def default_db_path():
    return str(ORCHESTRA_DIR / "state" / "brain" / "facts.db")


def latest_user_text(messages):
    """Latest non-empty user turn from an OpenAI-style message list ('' if none)."""
    for m in reversed(messages or []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


def extract_keywords(text):
    """Topic tokens for LIKE filtering: lowercase, >=3 chars, non-stopword, de-duped, capped."""
    seen = []
    for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", (text or "").lower()):
        if len(w) < MIN_KEYWORD_LEN or w in _STOP:
            continue
        if w not in seen:
            seen.append(w)
        if len(seen) >= MAX_KEYWORDS:
            break
    return seen


def _is_noise(txt):
    low = txt.lstrip().lower()
    return any(low.startswith(p) for p in _NOISE_PREFIXES)


_KIND_WEIGHT = {"goal": 3, "decision": 3, "insight": 2, "fact": 1}

# Recency x tier decay (spec R2 fork: half-lives are CONFIG, not hardcoded).
# effective_confidence = base_confidence * 0.5 ** (age_days / half_life_days).
# durable = ~5y (build-gate #2: a FINITE long value, never literal infinity —
# a mis-tagged fluid fact must still eventually decay); standard ~180d;
# ephemeral ~7d (daily churn). Unknown tier -> standard's half-life (never
# treated as no-decay). Override per-tier via ARTURO_FACTS_HALFLIFE_<TIER>_D.
DEFAULT_HALFLIFE_DAYS = {"durable": 1825.0, "standard": 180.0, "ephemeral": 7.0}


def halflife_days(tier):
    default = DEFAULT_HALFLIFE_DAYS.get(tier, DEFAULT_HALFLIFE_DAYS["standard"])
    env = os.environ.get(f"ARTURO_FACTS_HALFLIFE_{str(tier).upper()}_D")
    if env:
        try:
            v = float(env)
            if v > 0:
                return v
        except ValueError:
            pass
    return default


def effective_confidence(base, age_days, tier):
    """base_confidence scaled by tier-specific exponential decay. age clamped >=0."""
    age = max(0.0, float(age_days))
    return float(base) * (0.5 ** (age / halflife_days(tier)))


def _age_days(ts, now=None):
    """Days between an ISO-8601 ts (…Z or +00:00) and now (UTC). 0 if unparseable."""
    if not ts:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 0.0


def _connect_ro(dbpath):
    # read-only; never create, never write, never disturb the ingest cron
    uri = f"file:{dbpath}?mode=ro&immutable=0"
    con = sqlite3.connect(uri, uri=True, timeout=1.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


# Tiers that NEVER decay — pulled as candidates regardless of recency so an old
# immutable/durable fact (e.g. a company ownership record) is never starved out of the
# recency-ordered window by 250 newer matches (spec R2-3, build-gate #4).
_NODECAY_TIERS = ("durable",)
DURABLE_LIMIT = 250          # bounded cap on the second (tier-aware) candidate pass


def query_facts(dbpath, keywords, k=K, candidate_limit=CANDIDATE_LIMIT):
    """Return up to k scored facts.

    Two bounded candidate passes, then score:
      1. recency window — newest CANDIDATE_LIMIT rows matching any keyword
         (idx_facts_ts, newest-first, early-terminating LIMIT).
      2. tier-aware — durable/immutable rows matching any keyword regardless of
         recency (bounded by DURABLE_LIMIT), so no-decay facts are never starved.
    Candidates are de-duped by fact_id, then keyword-density + kind + recency
    scored in Python. Both passes are LIMIT-bounded to hold the 250ms wall.
    """
    if not keywords:
        return []
    like = " OR ".join(["text LIKE ?"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords]
    con = _connect_ro(dbpath)
    try:
        # E1: superseded (demoted) facts are down-ranked out of recall — the
        # newer fact that replaced them surfaces instead. Guarded so it works on
        # older dbs whose rows predate the state column population.
        not_sup = "COALESCE(state,'active') != 'superseded'"
        # SQL-level noise exclusion mirrors _is_noise (lstrip+lower+startswith) so
        # the bounded recency window (LIMIT ?) is filled with REAL candidates —
        # otherwise a burst of recent distiller prompt-echoes (all dated today)
        # crowds every real fact out of the 250-row window BEFORE the Python
        # filter runs. Defense-in-depth: _is_noise still runs on the survivors.
        noise_clause = " AND ".join(["lower(ltrim(text)) NOT LIKE ?"] * len(_NOISE_PREFIXES))
        noise_params = [p + "%" for p in _NOISE_PREFIXES]
        not_noise = (" AND " + noise_clause) if noise_clause else ""
        rows = con.execute(
            "SELECT fact_id, ts, kind, text, expiry_class, confidence FROM facts "
            f"WHERE ({like}) AND {not_sup}{not_noise} ORDER BY ts DESC LIMIT ?",
            params + noise_params + [candidate_limit]).fetchall()
        tier_ph = ",".join("?" * len(_NODECAY_TIERS))
        rows2 = con.execute(
            "SELECT fact_id, ts, kind, text, expiry_class, confidence FROM facts "
            f"WHERE ({like}) AND {not_sup}{not_noise} AND expiry_class IN ({tier_ph}) "
            "ORDER BY ts DESC LIMIT ?",
            params + noise_params + list(_NODECAY_TIERS) + [DURABLE_LIMIT]).fetchall()
    finally:
        con.close()

    # merge, de-dupe by fact_id (fall back to identity when a row lacks one)
    merged = {}
    for r in list(rows) + list(rows2):
        fid = r["fact_id"] if "fact_id" in r.keys() else None
        key = fid if fid is not None else (r["ts"], r["text"])
        merged.setdefault(key, r)
    cands = list(merged.values())
    now = datetime.now(timezone.utc)

    scored = []
    for r in cands:
        txt = r["text"] or ""
        if _is_noise(txt):
            continue
        low = txt.lower()
        hits = sum(1 for kw in keywords if kw in low)
        if hits == 0:
            continue
        cols = r.keys()
        base = r["confidence"] if "confidence" in cols else 1.0
        tier = r["expiry_class"] if "expiry_class" in cols else "standard"
        # recency x tier: newer + more-stable facts score higher automatically
        eff_conf = effective_confidence(base if base is not None else 1.0,
                                        _age_days(r["ts"], now), tier)
        score = hits * 10 + _KIND_WEIGHT.get(r["kind"], 0) + eff_conf
        scored.append((score, {"fact_id": (r["fact_id"] if "fact_id" in cols else None),
                               "ts": r["ts"], "kind": r["kind"], "text": txt,
                               "expiry_class": tier}))
    # tie-break by source_ts desc (spec §C) via the eff_conf term already, then ts
    scored.sort(key=lambda x: (x[0], x[1]["ts"] or ""), reverse=True)
    return [f for _, f in scored[:k]]


def l2_enabled():
    return os.environ.get(L2_FLAG, "") == "1"


def query_worldview(dbpath, keywords, k=K, fluid_limit=100):
    """P1.d L2 read path (runs INSIDE the same budget wall/single-flight as query_facts).
    (1) MATERIALIZED beliefs (durable/standard) keyword-matched over subject+current_value,
        ranked by corroboration-count confidence then recency; each cites ONE L1 statement
        (source_type + date via derived_from) — the worldview stays traceable.
    (2) FLUID synthesis: newest `fluid_limit` ephemeral, active, non-noise statements
        matching the keywords, deterministically aggregated newest-wins per E1 subject-key
        (no LLM, directive #3); the stale side of a contradiction never renders as current.
    """
    if not keywords:
        return []
    import json as _json
    try:
        from scripts.brain.worldview_migrate import subject_predicate as _sp
    except Exception:                                  # pragma: no cover
        _sp = lambda t: None
    like = " OR ".join(["(subject LIKE ? OR current_value LIKE ?)"] * len(keywords))
    params = [x for kw in keywords for x in (f"%{kw}%", f"%{kw}%")]
    con = _connect_ro(dbpath)
    try:
        beliefs = con.execute(
            f"SELECT subject, claim, current_value, confidence, tier, derived_from,"
            f" last_updated FROM beliefs WHERE superseded_by IS NULL AND ({like})"
            f" ORDER BY confidence DESC, last_updated DESC LIMIT ?",
            params + [k * 2]).fetchall()
        out = []
        for subj, claim, value, conf, tier, derived, updated in beliefs[:k]:
            cite = ""
            try:
                sids = _json.loads(derived or "[]")
                if sids:
                    row = con.execute("SELECT source_type, ts FROM statements WHERE"
                                      " statement_id=?", (sids[-1],)).fetchone()
                    if row:
                        cite = f"per {row[0]} {str(row[1])[:10]}"
            except Exception:
                pass
            # stmt-cluster beliefs store subject = value[:80] (worldview_migrate
            # _derive_beliefs), so "{subj}: {value}" would print the prefix twice.
            text = value if (value or "").startswith(subj or "") else f"{subj}: {value}"
            out.append({"kind": "belief", "tier": tier, "text": text,
                        "ts": updated, "cite": cite, "conf": conf})
        # fluid: recent ephemeral statements, newest-wins per subject-key
        s_like = " OR ".join(["content LIKE ?"] * len(keywords))
        s_params = [f"%{kw}%" for kw in keywords]
        rows = con.execute(
            f"SELECT statement_id, source_type, ts, content, metadata FROM statements"
            f" WHERE ({s_like}) AND metadata LIKE '%ephemeral%'"
            f" AND metadata NOT LIKE '%\"noise\": true%'"
            f" ORDER BY ts DESC LIMIT ?", s_params + [fluid_limit]).fetchall()
        newest = {}
        for sid, stype, ts, content, meta in rows:      # ts DESC: first seen wins per key
            sp = _sp(content)
            key = sp[0] if sp else " ".join((content or "").lower().split())[:80]
            if key not in newest:
                newest[key] = {"kind": "fluid", "tier": "ephemeral", "text": content,
                               "ts": ts, "cite": f"per {stype} {str(ts)[:10]}", "conf": 1.0}
        out.extend(list(newest.values())[: max(0, k - len(out)) + 2])
        return out[: k + 2]
    finally:
        con.close()


_WV_HEADER = "WORLDVIEW (current beliefs, traceable to sources — cite when asked how you know):"


def _wv_snippet(text):
    """Word-boundary truncation for spoken belief lines: never cut mid-token;
    ellipsis only when something was actually dropped. (L2-only — the legacy
    flag-off _render below stays byte-identical.)"""
    raw = " ".join((text or "").split())
    if len(raw) <= SNIPPET_CHARS:
        return raw
    cut = raw[:SNIPPET_CHARS]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip() + "…"


def _render_worldview(entries):
    lines = [_WV_HEADER]
    used = len(_WV_HEADER)
    for e in entries:
        snip = _wv_snippet(e.get("text"))
        cite = f" ({e['cite']})" if e.get("cite") else ""
        line = f"- [{e.get('tier', '?')}] {snip}{cite}"
        if used + 1 + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += 1 + len(line)
    return "" if len(lines) == 1 else "\n".join(lines)


def _render(facts):
    lines = [_HEADER]
    used = len(_HEADER)
    for f in facts:
        snip = " ".join((f.get("text") or "").split())[:SNIPPET_CHARS]
        date = (f.get("ts") or "")[:10]
        line = f"- [{f.get('kind', 'fact')} {date}] {snip}"
        if used + 1 + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += 1 + len(line)
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def warm_cache(dbpath=None):
    """Boot-warm (gm msg_5d10b3f0 item 3): run ONE read-only query so turn 1
    after a cold boot doesn't lose the block to the budget wall (cold page
    cache measured 2.19s on 2026-09-16). A non-matching LIKE still scans the
    tables, which is exactly the warm we need. Best-effort: returns elapsed ms,
    0.0 on missing db or any error, never raises."""
    t0 = time.perf_counter()
    try:
        dbpath = dbpath or default_db_path()
        if not os.path.exists(dbpath):
            return 0.0
        kws = ["__bootwarm__"]
        if l2_enabled():
            query_worldview(dbpath, kws, k=K)
        else:
            query_facts(dbpath, kws, k=K)
        ms = (time.perf_counter() - t0) * 1000.0
        log.info(f"worldview: cache warm in {ms:.0f}ms")
        return ms
    except Exception as e:                             # noqa: BLE001 — never fatal at boot
        log.error(f"facts-recall warm error (non-fatal): {e}")
        return 0.0


def facts_preamble(user_text, dbpath=None, budget_ms=None):
    """Return a <=250-token FACTS block for this turn, or '' (strictly additive — every
    failure/skip path degrades to no-block, never an error into the turn)."""
    if not enabled():
        return ""
    text = (user_text or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return ""
    keywords = extract_keywords(text)
    if not keywords:
        return ""
    dbpath = dbpath or default_db_path()
    if not os.path.exists(dbpath):
        return ""
    if budget_ms is None:
        try:
            budget_ms = int(os.environ.get(BUDGET_ENV, DEFAULT_BUDGET_MS))
        except ValueError:
            budget_ms = DEFAULT_BUDGET_MS

    sem = _inflight
    if not sem.acquire(blocking=False):     # single-flight: overlapping turn skips
        return ""
    result = {}

    use_l2 = l2_enabled()                    # P1.d: gm-gated flip; off = legacy byte-identical

    def _work():
        try:
            if use_l2:
                _t0 = time.perf_counter()
                result["r"] = query_worldview(dbpath, keywords, k=K)
                result["ms"] = (time.perf_counter() - _t0) * 1000.0
                result["l2"] = True
            else:
                result["r"] = query_facts(dbpath, keywords, k=K)
        except Exception as e:               # noqa: BLE001 — degrade, never raise into the turn
            result["e"] = e
        finally:
            sem.release()

    t = threading.Thread(target=_work, daemon=True, name="facts-recall-query")
    t.start()
    t.join(budget_ms / 1000.0)
    if "e" in result:
        log.error(f"facts-recall query error (non-fatal): {result['e']}")
        return ""
    if "r" not in result:
        log.info(f"facts-recall: budget {budget_ms}ms exceeded — turn proceeds without facts")
        return ""
    if result.get("l2"):
        # gm-gated breadcrumb (msg_5d10b3f0 item 2): one INFO line per L2 request so a
        # real call is provable from arturo-proxy.log. Flag-off path logs nothing.
        log.info(f"worldview: path=L2 beliefs={len(result['r'])} ms={result.get('ms', 0):.0f}")
    if not result["r"]:
        return ""
    try:
        if result.get("l2"):
            return _render_worldview(result["r"])
        return _render(result["r"])
    except Exception as e:                    # noqa: BLE001
        log.error(f"facts-recall render error (non-fatal): {e}")
        return ""
