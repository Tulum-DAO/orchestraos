"""Arturo in-call FACTS recall — bounded, read-only, facts-only.

Sources (merged, one ranking scale — see query_sources):
  1. $ORCHESTRA_DIR/facts/facts_db.json — the dashboard/API facts store. This is the
     source a clean install has: write a fact in the dashboard Facts pane (or
     `POST /api/facts`), restart, and Arturo carries it in the FACTS block next turn.
  2. $ORCHESTRA_DIR/state/brain/facts.db — an optional sqlite store an ingest job may
     populate (goals/insights/decisions/facts). Absent on a fresh install; used when present.

No private imports: this module depends only on the standard library. There is no
belief-derivation layer here — it is facts-only by design.

Posture (mirrors semantic_recall.py's safety contract):
  - INERT unless ARTURO_FACTS_RECALL=1 (checked before any work). Flag off = no-op, no reads.
  - READ-ONLY: sqlite opens mode=ro; the JSON store is only ever read.
  - BOUNDED for the live voice path: keyword-scored over bounded candidate windows,
    top-K, behind a HARD BUDGET WALL in a worker thread + SINGLE-FLIGHT, so it can never
    stall or overlap a live turn — on timeout the turn proceeds with no FACTS block.
  - Every failure/skip path degrades to '' (empty) — never raises into the turn.
  - Prompt-echo / tool-output ingestion artifacts are filtered so only real facts surface.
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from services.arturo import facts_index
except ImportError:                                    # loaded as a top-level module (the proxy's fallback import)
    import facts_index

log = logging.getLogger("arturo-facts-recall")

FLAG = "ARTURO_FACTS_RECALL"
BUDGET_ENV = "ARTURO_FACTS_RECALL_BUDGET_MS"
DEFAULT_BUDGET_MS = 250
K = 5                         # facts rendered
CANDIDATE_LIMIT = 250        # rows materialized before scoring (bounded scan cap)
MAX_KEYWORDS = 6
MIN_QUERY_CHARS = 8
MIN_KEYWORD_LEN = 3
SNIPPET_CHARS = 160
MAX_CHARS = 250 * 4          # ~250-token hard clamp, same budget shape as semantic recall

_REPO_ROOT = Path(__file__).resolve().parents[2]


def orchestra_dir():
    """Data dir, resolved at CALL time (not import time) so the supervisor's
    ORCHESTRA_DIR export — and a test's monkeypatch — always win. Falls back to the
    checkout itself, which is where `orchestra init` puts data by default."""
    return Path(os.environ.get("ORCHESTRA_DIR", str(_REPO_ROOT)))

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
    return str(orchestra_dir() / "state" / "brain" / "facts.db")


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


# Time words make a question about WHEN, not a topic (2026-11-25: "what happened in the past day" matched every row
# with "day" in it and surfaced last week). They become a window and are dropped from the search terms.
_TIME_WORDS = set("yesterday tonight overnight night last past day days hours hour week weeks morning recent "
                  "recently since lately earlier happened happening new".split())
_N_UNITS = re.compile(r"\b(?:past|last)\s+(\d{1,3})\s+(hours?|days?|weeks?)\b")
_PHRASES = (("last night", 18), ("overnight", 18), ("yesterday", 36), ("past day", 24), ("last day", 24),
            ("this morning", 12), ("this week", 168), ("past week", 168), ("last week", 168),
            ("last few days", 72), ("past few days", 72))


def time_window_hours(text):
    """Hours back the question reaches, or None when it names no time."""
    low = " ".join((text or "").lower().replace("'s", "").split())
    m = _N_UNITS.search(low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * (1 if unit.startswith("hour") else 24 if unit.startswith("day") else 168)
    for phrase, hours in _PHRASES:
        if phrase in low:
            return hours
    return None


def topic_keywords(text):
    """extract_keywords without the time words (and the numbers they come with)."""
    return [w for w in extract_keywords(text) if w not in _TIME_WORDS and not w.isdigit()]


def recent_facts(hours, keywords=None, k=12, dbpath=None, now=None):
    """Facts from the last `hours`, newest first, noise and superseded rows left out; with keywords, the rows that
    match one come first (still newest first within each group). Read-only; no store, or an unreadable one: []."""
    dbpath = dbpath or default_db_path()
    if not os.path.exists(dbpath):
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=float(hours))).strftime("%Y-%m-%dT%H:%M:%S")
    noise_clause = " AND ".join(["lower(ltrim(text)) NOT LIKE ?"] * len(_NOISE_PREFIXES))
    try:
        con = _connect_ro(dbpath)
        try:
            rows = con.execute(
                "SELECT fact_id, ts, kind, text, expiry_class, confidence FROM facts "
                "WHERE ts >= ? AND COALESCE(state,'active') != 'superseded'"
                + ((" AND " + noise_clause) if noise_clause else "") + " ORDER BY ts DESC LIMIT ?",
                [cutoff] + [p + "%" for p in _NOISE_PREFIXES] + [max(k * 8, 100)]).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.error(f"facts-recall: recent_facts store error (non-fatal): {e}")
        return []
    facts = [f for f in _row_dicts(rows) if not _is_noise(f["text"])]
    if keywords:
        hit = [f for f in facts if any(kw in f["text"].lower() for kw in keywords)]
        facts = hit + [f for f in facts if f not in hit]
    return facts[:k]


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

    return _score(_row_dicts(list(rows) + list(rows2)), keywords, k)


def _row_dicts(rows):
    """sqlite Row objects -> plain dicts with the fields the scorer reads."""
    out = []
    for r in rows:
        cols = r.keys()
        out.append({"fact_id": r["fact_id"] if "fact_id" in cols else None,
                    "ts": r["ts"], "kind": r["kind"], "text": r["text"] or "",
                    "expiry_class": r["expiry_class"] if "expiry_class" in cols else "standard",
                    "confidence": r["confidence"] if "confidence" in cols else 1.0})
    return out


def _score(cands, keywords, k):
    """Keyword-density + kind + recency x tier scoring over plain-dict candidates
    (shared by every source so the sqlite store and the dashboard JSON store rank
    on one scale). De-dupes by fact_id (or ts+text), drops noise and zero-hit rows."""
    merged = {}
    for r in cands:
        key = r.get("fact_id") if r.get("fact_id") is not None else (r.get("ts"), r.get("text"))
        merged.setdefault(key, r)
    now = datetime.now(timezone.utc)
    scored = []
    for r in merged.values():
        txt = r.get("text") or ""
        if _is_noise(txt):
            continue
        low = txt.lower()
        hits = sum(1 for kw in keywords if kw in low)
        if hits == 0:
            continue
        base = r.get("confidence")
        tier = r.get("expiry_class") or "standard"
        eff_conf = effective_confidence(base if base is not None else 1.0,
                                        _age_days(r.get("ts"), now), tier)
        score = hits * 10 + _KIND_WEIGHT.get(r.get("kind"), 0) + eff_conf
        scored.append((score, {"fact_id": r.get("fact_id"), "ts": r.get("ts"),
                               "kind": r.get("kind") or "fact", "text": txt,
                               "expiry_class": tier}))
    scored.sort(key=lambda x: (x[0], x[1]["ts"] or ""), reverse=True)
    return [f for _, f in scored[:k]]


# --- dashboard JSON store (the source a clean install actually has) -----------------

def default_json_path():
    """The dashboard/API facts store: $ORCHESTRA_DIR/facts/facts_db.json
    (api/src/routes/facts.ts writes it; POST /api/facts appends rows)."""
    return str(orchestra_dir() / "facts" / "facts_db.json")


def query_facts_json(path, keywords, k=K):
    """Return up to k scored facts from the dashboard JSON store. Read-only; a
    missing or corrupt file is an empty result, never an error. Rows carry
    `fact` or `text` (legacy field name), `timestamp`/`verified_at`, optional
    `category`/`confidence`/`expiry_class`."""
    if not keywords or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:                             # noqa: BLE001 — corrupt store = inert
        log.error(f"facts-recall: json store unreadable (non-fatal): {e}")
        return []
    cands = []
    for i, row in enumerate((data or {}).get("facts") or []):
        if not isinstance(row, dict):
            continue
        txt = row.get("fact") or row.get("text") or ""
        if not txt:
            continue
        if str(row.get("state") or "active") == "superseded":
            continue
        cands.append({"fact_id": f"json:{row.get('id', i)}",
                      "ts": row.get("timestamp") or row.get("verified_at") or "",
                      "kind": row.get("category") or row.get("kind") or "fact",
                      "text": txt,
                      "expiry_class": row.get("expiry_class") or "standard",
                      "confidence": row.get("confidence")})
    return _score(cands, keywords, k)


def query_sources(keywords, k=K, dbpath=None, jsonpath=None, text=None):
    """ONE entrypoint over every facts source on the box — the sqlite ingest store
    when it exists (private nightly cron) and the dashboard JSON store — merged and
    re-ranked on the shared scale. Both the passive FACTS block and the knowledge
    tool read through here so a fact written on the dashboard surfaces on every
    path Arturo answers from.

    With `text` (the caller's own words) and ARTURO_FACTS_HYBRID=1, the ranking is
    the hybrid one (query_hybrid). Any other case, and any time the hybrid index is
    not ready, returns exactly the keyword ranking below."""
    if text and facts_index.enabled():
        try:
            hy = query_hybrid(text, keywords, k=k, dbpath=dbpath, jsonpath=jsonpath)
        except Exception as e:                         # noqa: BLE001 — hybrid trouble = keyword recall
            log.error(f"facts-recall: hybrid error, keyword recall used (non-fatal): {e}")
            hy = None
        if hy is not None:
            return hy
    return _query_sources_keyword(keywords, k=k, dbpath=dbpath, jsonpath=jsonpath)


def _query_sources_keyword(keywords, k=K, dbpath=None, jsonpath=None):
    dbpath = dbpath or default_db_path()
    jsonpath = jsonpath or default_json_path()
    cands = []
    if os.path.exists(dbpath):
        try:
            cands.extend(query_facts(dbpath, keywords, k=k))
        except Exception as e:                         # noqa: BLE001
            log.error(f"facts-recall: sqlite store error (non-fatal): {e}")
    cands.extend(query_facts_json(jsonpath, keywords, k=k))
    for c in cands:                                    # re-rank: give the scorer confidence back
        c.setdefault("confidence", 1.0)
    return _score(cands, keywords, k)


def _facts_by_id(dbpath, fact_ids):
    """facts.db rows for these ids, as scorer dicts, with the same state and noise filters as query_facts:
    the index only points; what may be served is decided here, on the live row. Unknown ids drop out."""
    if not fact_ids:
        return {}
    noise_clause = " AND ".join(["lower(ltrim(text)) NOT LIKE ?"] * len(_NOISE_PREFIXES))
    con = _connect_ro(dbpath)
    try:
        rows = con.execute(
            "SELECT fact_id, ts, kind, text, expiry_class, confidence FROM facts "
            f"WHERE fact_id IN ({','.join('?' * len(fact_ids))}) AND COALESCE(state,'active') != 'superseded'"
            + ((" AND " + noise_clause) if noise_clause else ""),
            list(fact_ids) + [p + "%" for p in _NOISE_PREFIXES]).fetchall()
    finally:
        con.close()
    return {f["fact_id"]: f for f in _row_dicts(rows) if not _is_noise(f["text"])}


def query_hybrid(text, keywords, k=K, dbpath=None, jsonpath=None, index_path=None):
    """Plan step 1 (Shaw's option B, card apr_39e1b560): keyword ranking + FTS5 bm25 + MiniLM vectors,
    fused by reciprocal rank. Returns None when the index cannot answer yet (the caller then serves the
    keyword ranking unchanged).

    Three ranked lists are fused:
      1. today's keyword ranking (_query_sources_keyword, every source, its kind and recency terms included),
         so a fact keyword recall finds is never silently dropped;
      2. FTS5 bm25 over the indexed fact text;
      3. cosine similarity of the question to every fact vector, which finds the same fact in other words.
    Lists 2 and 3 are the measured option B; list 1 keeps the dashboard JSON store and today's tie-breaks in."""
    dbpath = dbpath or default_db_path()
    if not os.path.exists(dbpath):
        return None
    lists = facts_index.search(text, index_path=index_path)
    if lists is None:
        return None
    fts_ids, vec_ids = lists
    base = _query_sources_keyword(keywords, k=facts_index.DEPTH, dbpath=dbpath, jsonpath=jsonpath) if keywords else []
    rows = {f["fact_id"]: f for f in base}
    rows.update({fid: f for fid, f in _facts_by_id(dbpath, [i for i in set(fts_ids) | set(vec_ids)
                                                            if i not in rows]).items()})
    # an id the live store no longer serves (superseded, noise, gone) takes no place in any list
    ranked = [[f["fact_id"] for f in base]] + [[i for i in lst if i in rows] for lst in (fts_ids, vec_ids)]
    score = facts_index.rrf(ranked)
    order = sorted(score, key=lambda i: (score[i], rows[i].get("ts") or ""), reverse=True)
    return [{"fact_id": i, "ts": rows[i].get("ts"), "kind": rows[i].get("kind") or "fact",
             "text": rows[i].get("text") or "", "expiry_class": rows[i].get("expiry_class") or "standard"}
            for i in order[:k]]


def _render(facts):
    lines = ["FACTS (fresh structured knowledge; cite naturally, do not read verbatim):"]
    used = len(lines[0])
    for f in facts:
        d = (f.get("ts") or "")[:10]
        snip = " ".join((f.get("text") or "").split())[:SNIPPET_CHARS]
        line = f"- [{f.get('kind', 'fact')} {d}] {snip}"
        if used + 1 + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += 1 + len(line)
    return "" if len(lines) == 1 else "\n".join(lines)


# Periodic re-warm state: boot-warm only covers turn-1-after-boot; a long-idle proxy
# re-cools its page cache and turn 1 loses the block to the budget wall. monotonic clock.
_warm_state = {"last_warm": 0.0, "last_activity": 0.0}


def _should_rewarm(now, last_warm, last_activity, interval_s, idle_s):
    """True when the cache is due a warm: every interval_s regardless, and during
    an idle stretch (no query for idle_s) once the last warm is also idle_s old."""
    if (now - last_warm) >= interval_s:
        return True
    return (now - last_activity) >= idle_s and (now - last_warm) >= idle_s


def keep_warm(interval_s=1800, idle_s=900, tick_s=60, stop=None):
    """Run inside the facts-cache-warm daemon thread. Never raises, never exits on
    a warm error; `stop` (threading.Event) ends it for tests."""
    while not (stop is not None and stop.is_set()):
        try:
            now = time.monotonic()
            if _should_rewarm(now, _warm_state["last_warm"],
                              _warm_state["last_activity"], interval_s, idle_s):
                warm_cache()
        except Exception as e:                         # noqa: BLE001 — daemon must survive
            try:
                _warm_state["last_warm"] = time.monotonic()   # don't hot-loop a failing warm
                log.error(f"facts-recall re-warm error (non-fatal): {e}")
            except Exception:
                pass
        if stop is not None:
            stop.wait(tick_s)
        else:
            time.sleep(tick_s)


def warm_cache(dbpath=None):
    """Boot-warm: run ONE read-only query so turn 1 after a cold boot doesn't lose
    the block to the budget wall. A non-matching keyword still scans every source,
    which is exactly the warm we need. Best-effort: returns elapsed ms, 0.0 when no
    source exists or on any error, never raises."""
    t0 = time.perf_counter()
    _warm_state["last_warm"] = time.monotonic()
    try:
        dbpath = dbpath or default_db_path()
        if not os.path.exists(dbpath) and not os.path.exists(default_json_path()):
            return 0.0
        query_sources(["__bootwarm__"], k=K, dbpath=dbpath)
        if facts_index.enabled():
            facts_index.search("boot warm")             # starts the embedder + vector load off the turn path
        ms = (time.perf_counter() - t0) * 1000.0
        log.info(f"facts-recall: cache warm in {ms:.0f}ms")
        return ms
    except Exception as e:                             # noqa: BLE001 — never fatal at boot
        log.error(f"facts-recall warm error (non-fatal): {e}")
        return 0.0


def facts_preamble(user_text, dbpath=None, budget_ms=None):
    """Return a <=250-token FACTS block for this turn, or '' (strictly additive — every
    failure/skip path degrades to no-block, never an error into the turn)."""
    if not enabled():
        return ""
    _warm_state["last_activity"] = time.monotonic()
    text = (user_text or "").strip()
    if len(text) < MIN_QUERY_CHARS:
        return ""
    keywords = extract_keywords(text)
    if not keywords:
        return ""
    dbpath = dbpath or default_db_path()
    if not os.path.exists(dbpath) and not os.path.exists(default_json_path()):
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

    def _work():
        try:
            _t0 = time.perf_counter()
            result["r"] = query_sources(keywords, k=K, dbpath=dbpath, text=text)
            result["ms"] = (time.perf_counter() - _t0) * 1000.0
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
    # one INFO breadcrumb per turn so a real call is provable from the proxy log
    log.info(f"facts-recall: facts={len(result['r'])} ms={result.get('ms', 0):.0f}")
    if not result["r"]:
        return ""
    try:
        return _render(result["r"])
    except Exception as e:                    # noqa: BLE001
        log.error(f"facts-recall render error (non-fatal): {e}")
        return ""
