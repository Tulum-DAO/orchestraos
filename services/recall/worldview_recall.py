"""Recall's WORLDVIEW read path: current beliefs and recent statements from facts.db, rendered as one block.

Moved here unchanged from the voice facts recall, which is facts-only on this tree. The recall service calls it
next to the FACTS block. Read-only: every connection is mode=ro with query_only.
"""
import itertools
import json
import os

from services.arturo.facts_recall import (K, MAX_CHARS, SNIPPET_CHARS, UNSERVED_STATES, UNSOURCED_FLAG,
                                         UNSOURCED_STATE, _connect_ro, served, sourced_first, sourced_window,
                                         unsourced)


def _statement_state(meta):
    """A statement carries the state of the fact it was copied from in its metadata. One without a state
    (written straight from its source, or unreadable) reads as active."""
    try:
        m = json.loads(meta) if isinstance(meta, str) else (meta or {})
    except ValueError:
        return ""
    return str(m.get("state") or "") if isinstance(m, dict) else ""


def _statement_served(meta):
    return served(_statement_state(meta))


def _window_class(state, is_wal, meta):
    """A statement's place in a window: None when it is never served (copied from a superseded or archived fact, or
    a wal row with no anchor), else whether it is unsourced."""
    if not served(state) or (is_wal and not _wal_anchored(meta)):
        return None
    return unsourced(state)


# The same reads in SQL, so SQLite skips the rows a window will not keep. Python still decides every row it is
# handed; the SQL only has to keep every row Python would keep. Metadata SQLite may read differently from json.loads
# (a repeated key, NaN or Infinity, an escape) is always handed to Python.
_META_JSON = "(typeof(metadata) = 'text' AND json_valid(metadata))"
_META_STATE = f"COALESCE(CASE WHEN {_META_JSON} THEN json_extract(metadata, '$.state') END, '')"
_META_ANCHORED = (f"({_META_JSON} AND json_type(metadata, '$.anchor') = 'object'"
                  " AND COALESCE(json_type(metadata, '$.anchor.seq'), 'null') != 'null')")
_META_ODD = ("(instr(metadata, '\\') > 0 OR instr(metadata, 'NaN') > 0 OR instr(metadata, 'Infinity') > 0"
             " OR metadata GLOB '*\"state\"*\"state\"*' OR metadata GLOB '*\"anchor\"*\"anchor\"*'"
             " OR metadata GLOB '*\"seq\"*\"seq\"*')")
_meta_odd = None


def _meta_odd_sql(con):
    """_META_ODD, or every row when this SQLite's json_valid accepts text json.loads rejects."""
    global _meta_odd
    if _meta_odd is None:
        lax = con.execute("SELECT json_valid('{a: 1}') OR json_valid(char(65279) || '{}')").fetchone()[0]
        _meta_odd = "1" if lax else _META_ODD
    return _meta_odd


def _servable_sql(con, wal, sourced_only=False):
    """SQL for the statements a window may keep: served, anchored when a wal row, and sourced when asked."""
    out = ",".join(f"'{s}'" for s in UNSERVED_STATES + ((UNSOURCED_STATE,) if sourced_only else ()))
    anchored = _META_ANCHORED if wal else f"(source_type IS NOT 'wal' OR {_META_ANCHORED})"
    return f"({_meta_odd_sql(con)} OR ({_META_STATE} NOT IN ({out}) AND {anchored}))"


def _statement_window(con, select, where, params, limit, wal_only):
    """The window of the newest `limit` servable statements, sourced ones first, each row as (row, state): an
    iterator over its sourced rows, newest first, read only as far as the caller reads it, and the list of the
    unverified rows among the newest `limit`. `select` reads metadata last and, unless every row is a wal row,
    source_type second. SQLite skips the rows the window will not keep; each row it hands back is checked here, and
    one never served is skipped. When the newest `limit` rows hold no unverified row, or are every row there is, the
    sourced rows are read from them; otherwise from a second query that SQLite filters to sourced rows."""
    def rows(sourced_only):
        cur = con.execute(f"{select} WHERE {where} AND {_servable_sql(con, wal_only, sourced_only)}"
                          f" ORDER BY ts DESC", params)
        for r in cur:
            state = _statement_state(r[-1])
            u = _window_class(state, wal_only or r[1] == "wal", r[-1])
            if u is not None and not (sourced_only and u):
                yield r, state
    head = list(itertools.islice(rows(False), max(0, limit)))
    uns = [rs for rs in head if unsourced(rs[1])]
    if not uns or len(head) < limit:
        return iter([rs for rs in head if not unsourced(rs[1])]), uns
    return itertools.islice(rows(True), limit), uns


def _derived_ids(derived):
    """The statement ids a belief derives from. Anything that is not a JSON list reads as none, and so does a list
    whose last entry is not an id (a string or a number); other entries that are not ids are skipped."""
    def _id(x):
        return isinstance(x, (str, int, float)) and not isinstance(x, bool)
    try:
        sids = json.loads(derived or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(sids, list) or not sids or not _id(sids[-1]):
        return []
    return [x for x in sids if _id(x)]

L2_FLAG = "ARTURO_WORLDVIEW_L2"   # flag-inert until it is turned on
                                  # on purpose — default off = legacy facts path
                                  # byte-identical, zero reads of beliefs/statements.

# Subject-key for the fluid pass: the first copula splits a short state-assertion into (subject, predicate).
# The same rule the worldview ledger groups by; kept here so this module reads no private code.
_WV_COPULAS = (" is now ", " are now ", " status is ", " status: ", " is ", " are ", " = ")


def _sp(text):
    low = " ".join((text or "").strip().lower().rstrip(".!?").split())
    for cop in _WV_COPULAS:
        i = low.find(cop)
        if i > 0:
            subj, pred = low[:i].strip(), low[i + len(cop):].strip()
            if subj and pred and len(subj.split()) <= 6 and len(pred.split()) <= 6:
                return subj, pred
            return None
    return None


def _wal_anchored(meta_json):
    """The WAL->semantic drain's reader refusal. A `source_type='wal'` statement is
    recallable ONLY if its metadata.anchor carries a seq — the anchor is corroboration, never
    the claim, and a wal row that cannot be corroborated must not be surfaced as if it
    were grounded. Writer refusal (wal_emit.assert_anchored) is the first wall; this is
    the second, so a row that slipped past the first (an older db, a hand insert, a future
    writer) still cannot be recalled. Decidable on malformed input: anything that is not a JSON
    object with anchor.seq is NOT anchored."""
    # The except is for json.loads ONLY, and only its decode errors. My first version caught
    # Exception and used a function-local alias that did not exist at module scope: the
    # NameError was swallowed and every VALID anchor read as "not anchored". Absent and broken
    # must not share a path — a decode failure is "not anchored"; anything else is a bug and
    # must raise.
    if isinstance(meta_json, str):
        try:
            meta = json.loads(meta_json)
        except (ValueError, TypeError):
            return False
    else:
        meta = meta_json or {}
    if not isinstance(meta, dict):
        return False
    anchor = meta.get("anchor")
    return isinstance(anchor, dict) and anchor.get("seq") is not None



def l2_enabled():
    return os.environ.get(L2_FLAG, "") == "1"


def _belief_entry(con, row):
    """A belief row as its entry, or None when it is dropped: it stands only on statements recall may serve."""
    subj, claim, value, conf, tier, derived, updated = row
    cite = ""
    sids = _derived_ids(derived)
    b_unsourced = False
    if sids:
        # A belief stands only on statements recall may serve; it is dropped when every statement under it
        # that this db holds is unserved. A belief whose statements are not in this db is served as before.
        q = ",".join("?" * len(sids))
        found = {r[0]: r for r in con.execute(
            f"SELECT statement_id, source_type, ts, metadata FROM statements WHERE statement_id IN ({q})",
            sids).fetchall()}
        ok = [found[s] for s in sids if s in found and _statement_served(found[s][3])]
        if found and not ok:
            return None
        last = found.get(sids[-1])
        if last is not None and _statement_served(last[3]):
            cite_row = last                       # the last statement it derives from, as always
        elif last is not None:
            # the last one is unserved: cite the newest servable one (the later one on a tie)
            cite_row = max(enumerate(ok), key=lambda ir: (str(ir[1][2] or ""), ir[0]))[1]
        else:
            cite_row = None                       # the last one is not in this db: no citation, as always
        if cite_row is not None:
            cite = f"per {cite_row[1]} {str(cite_row[2])[:10]}"
        # a belief is unsourced when every statement it may stand on has no source quote found
        b_unsourced = bool(ok) and all(unsourced(_statement_state(r[3])) for r in ok)
    # stmt-cluster beliefs store subject = value[:80] (worldview_migrate
    # _derive_beliefs), so "{subj}: {value}" would print the prefix twice.
    text = value if (value or "").startswith(subj or "") else f"{subj}: {value}"
    return {"kind": "belief", "tier": tier, "text": text,
            "ts": updated, "cite": cite, "conf": conf, "unsourced": b_unsourced}


def query_worldview(dbpath, keywords, k=K, fluid_limit=100):
    """The L2 read path (runs INSIDE the same budget wall/single-flight as query_facts).
    (1) MATERIALIZED beliefs (durable/standard) keyword-matched over subject+current_value,
        ranked by corroboration-count confidence then recency; each cites ONE L1 statement
        (source_type + date via derived_from) — the worldview stays traceable.
    (2) FLUID synthesis: newest `fluid_limit` ephemeral, servable, non-noise statements
        matching the keywords, deterministically aggregated newest-wins per subject-key
        (no LLM); the stale side of a contradiction never renders as current. The newest
        SOURCED statement wins its subject; an unsourced one wins only a subject with no sourced one.
    Sourced entries are picked before every cut (the belief, fluid and wal windows, the fluid slots, the wal
    reservation, the final k+2), so an entry with no source quote found never displaces a sourced one.
    """
    if not keywords:
        return []
    import json as _json
    like = " OR ".join(["(subject LIKE ? OR current_value LIKE ?)"] * len(keywords))
    params = [x for kw in keywords for x in (f"%{kw}%", f"%{kw}%")]
    con = _connect_ro(dbpath)
    try:
        beliefs = con.execute(
            f"SELECT subject, claim, current_value, confidence, tier, derived_from,"
            f" last_updated FROM beliefs WHERE superseded_by IS NULL AND ({like})"
            f" ORDER BY confidence DESC, last_updated DESC",
            params)
        # the window holds k*2 beliefs, sourced ones first, read in confidence order until k*2 are in: an unsourced
        # belief never pushes a sourced one out of it. A dropped belief (None) is skipped and takes no place in it.
        out = sourced_window((_belief_entry(con, r) for r in beliefs), k * 2,
                             lambda e: None if e is None else e["unsourced"])
        # every fetched belief is a candidate, so one dropped is backfilled; the window is sourced first, so the cut
        # at k takes sourced ones first
        out = out[:k]
        n_sourced = sum(1 for e in out if not e["unsourced"])
        # fluid: recent ephemeral statements, newest-wins per subject-key
        s_like = " OR ".join(["content LIKE ?"] * len(keywords))
        s_params = [f"%{kw}%" for kw in keywords]
        # the newest `fluid_limit` servable statements, sourced ones first: newer unverified ones never push a
        # sourced one out. Each row is read with its state, parsed once. A row copied from a superseded or archived
        # fact, or an unanchored wal row, is never served, so it is skipped and takes no place in the window.
        src_rows, uns_rows = _statement_window(
            con, "SELECT statement_id, source_type, ts, content, metadata FROM statements",
            f"({s_like}) AND metadata LIKE '%ephemeral%' AND metadata NOT LIKE '%\"noise\": true%'",
            s_params, fluid_limit, False)
        # the fluid slots are counted against sourced beliefs only, and filled with sourced statements first
        slots = max(0, k - n_sourced) + 2
        newest = {}

        def _fluid(row, state):
            sid, stype, ts, content, meta = row
            sp = _sp(content)
            key = sp[0] if sp else " ".join((content or "").lower().split())[:80]
            newest.setdefault(key, {"kind": "fluid", "tier": "ephemeral", "text": content,
                                    "ts": ts, "cite": f"per {stype} {str(ts)[:10]}", "conf": 1.0,
                                    "unsourced": unsourced(state)})
        # the window's sourced rows, then its unverified ones, each ts DESC: the first seen wins its subject, so the
        # newest SOURCED statement wins it and an unsourced one wins only a subject with no sourced one. Reading
        # stops once every slot has a subject: no later row can change what is served.
        n_src = 0
        for row, state in src_rows:
            n_src += 1
            _fluid(row, state)
            if len(newest) >= slots:
                break
        else:
            for row, state in uns_rows[: fluid_limit - n_src]:     # what the sourced rows left of the window
                if len(newest) >= slots:
                    break
                _fluid(row, state)
        out.extend(list(newest.values())[:slots])       # sourced subjects were seen first, so they come first
        out = sourced_first(out)
        # The WAL->semantic drain's wal pass. The fluid pass above reads ONLY
        # ephemeral rows; the drain writes `standard` (a commit is not ephemeral), so without
        # this pass 497 drained facts were unrecallable on the first live dry run — written
        # but invisible, a green pass with nothing behind it. Anchored rows only (the reader's
        # half of the anchor rule); keyword-matched; newest first; bounded by the same window and the same
        # k+2 contract as everything else in this function.
        # RESERVED SLOTS, not leftovers. My first version ran this pass only
        # `if len(out) < k + 2` — and on the LIVE facts.db 120 matching beliefs filled every
        # slot, 2 fluid rows took the '+2', and 497 freshly drained facts were unrecallable
        # while the unit test (a db with no beliefs) stayed green. So: up to WAL_RESERVE
        # matching anchored wal rows are guaranteed a place INSIDE the k+2 total by trimming
        # the tail of what is already there (the lowest-ranked belief/fluid entries), never by
        # exceeding it. No match => nothing reserved, nothing trimmed.
        # the same window rule: sourced wal rows first, so newer unverified ones never take a sourced row's
        # reservation. Only the first WAL_RESERVE of each are used: the sourced ones newest first, and the unverified
        # ones among the newest `fluid_limit`, which only ever take the tail after every sourced entry.
        src_rows, uns_rows = _statement_window(con, "SELECT statement_id, ts, content, metadata FROM statements",
                                               f"source_type='wal' AND ({s_like})", s_params, fluid_limit, True)

        def _wal_entry(row, state):
            sid, ts, content, meta = row
            m = _json.loads(meta)                         # anchored, so a JSON object
            return {"kind": "wal", "tier": m.get("expiry_class", "standard"),
                    "text": content, "ts": ts, "cite": f"per wal {str(ts)[:10]}",
                    "conf": m.get("confidence", 0.5), "unsourced": unsourced(state)}
        wal_entries = [_wal_entry(*rs) for rs in itertools.islice(src_rows, WAL_RESERVE)]
        wal_unsourced = [_wal_entry(*rs) for rs in uns_rows[:WAL_RESERVE]]
        if wal_entries:
            # only sourced wal rows are reserved a slot; `out` is sourced first, so its unsourced tail goes first
            out = out[: max(0, (k + 2) - len(wal_entries))] + wal_entries
        # unsourced wal rows take the tail; unsourced entries rank last, so they are trimmed first
        return sourced_first(out + wal_unsourced)[: k + 2]
    finally:
        con.close()


# WAL->semantic drain: how many matching ANCHORED wal facts query_worldview guarantees a
# place for inside its k+2 result — measured need on the first live dry run, where 120 matching
# beliefs starved every wal row. 2 mirrors the existing '+2' the fluid pass gets.
WAL_RESERVE = 2

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
        tag = f" · {UNSOURCED_FLAG}" if e.get("unsourced") else ""     # never framed as anyone's words
        line = f"- [{e.get('tier', '?')}{tag}] {snip}{cite}"
        if used + 1 + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += 1 + len(line)
    return "" if len(lines) == 1 else "\n".join(lines)
