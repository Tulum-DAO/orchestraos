"""Recall's WORLDVIEW read path: current beliefs and recent statements from facts.db, rendered as one block.

Moved here unchanged from the voice facts recall, which is facts-only on this tree. The recall service calls it
next to the FACTS block. Read-only: every connection is mode=ro with query_only.
"""
import json
import os

from services.arturo.facts_recall import (K, MAX_CHARS, SNIPPET_CHARS, UNSOURCED_FLAG, _connect_ro, served,
                                         sourced_first, unsourced)


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
    """WAL->semantic drain §5.2 reader refusal (step 4). A `source_type='wal'` statement is
    recallable ONLY if its metadata.anchor carries a seq — the anchor is corroboration, never
    the claim (§5.4), and a wal row that cannot be corroborated must not be surfaced as if it
    were grounded. Writer refusal (§5.1, wal_emit.assert_anchored) is the first wall; this is
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
            except Exception:
                sids = []
            if sids:
                # A belief stands only on statements recall may serve: cite the newest servable one, and drop
                # the belief when every statement under it is unserved. A belief whose statements are not in
                # this db is served as before.
                q = ",".join("?" * len(sids))
                found = {r[0]: r for r in con.execute(
                    f"SELECT statement_id, source_type, ts, metadata FROM statements WHERE statement_id IN ({q})",
                    sids).fetchall()}
                ok = [found[s] for s in sids if s in found and _statement_served(found[s][3])]
                if found and not ok:
                    continue
                if ok:
                    cite = f"per {ok[-1][1]} {str(ok[-1][2])[:10]}"
                # a belief is unsourced when every statement it may stand on has no source quote found
                b_unsourced = bool(ok) and all(unsourced(_statement_state(r[3])) for r in ok)
            else:
                b_unsourced = False
            # stmt-cluster beliefs store subject = value[:80] (worldview_migrate
            # _derive_beliefs), so "{subj}: {value}" would print the prefix twice.
            text = value if (value or "").startswith(subj or "") else f"{subj}: {value}"
            out.append({"kind": "belief", "tier": tier, "text": text,
                        "ts": updated, "cite": cite, "conf": conf, "unsourced": b_unsourced})
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
            if not _statement_served(meta):
                continue                                  # copied from a superseded or archived fact
            if stype == "wal" and not _wal_anchored(meta):
                continue                                  # §5.2: unanchored wal row is not recallable
            sp = _sp(content)
            key = sp[0] if sp else " ".join((content or "").lower().split())[:80]
            if key not in newest:
                newest[key] = {"kind": "fluid", "tier": "ephemeral", "text": content,
                               "ts": ts, "cite": f"per {stype} {str(ts)[:10]}", "conf": 1.0,
                               "unsourced": unsourced(_statement_state(meta))}
        out.extend(list(newest.values())[: max(0, k - len(out)) + 2])
        # WAL->semantic drain §5.2 (step 4): the wal pass. The fluid pass above reads ONLY
        # ephemeral rows; the drain writes `standard` (a commit is not ephemeral), so without
        # this pass 497 drained facts were unrecallable on the first live dry run — written
        # but invisible, a green pass with nothing behind it. Anchored rows only (the reader's
        # half of §5); keyword-matched; newest first; bounded by the same LIMIT and the same
        # k+2 contract as everything else in this function.
        # RESERVED SLOTS, not leftovers. My first version ran this pass only
        # `if len(out) < k + 2` — and on the LIVE facts.db 120 matching beliefs filled every
        # slot, 2 fluid rows took the '+2', and 497 freshly drained facts were unrecallable
        # while the unit test (a db with no beliefs) stayed green. So: up to WAL_RESERVE
        # matching anchored wal rows are guaranteed a place INSIDE the k+2 total by trimming
        # the tail of what is already there (the lowest-ranked belief/fluid entries), never by
        # exceeding it. No match => nothing reserved, nothing trimmed.
        w_rows = con.execute(
            f"SELECT statement_id, ts, content, metadata FROM statements"
            f" WHERE source_type='wal' AND ({s_like})"
            f" ORDER BY ts DESC LIMIT ?", s_params + [fluid_limit]).fetchall()
        wal_entries = []
        for sid, ts, content, meta in w_rows:
            if not _statement_served(meta):
                continue
            if not _wal_anchored(meta):
                continue                                  # §5.2: unanchored is not recallable
            try:
                m = _json.loads(meta) if isinstance(meta, str) else (meta or {})
            except (ValueError, TypeError):
                m = {}
            wal_entries.append({"kind": "wal", "tier": m.get("expiry_class", "standard"),
                                "text": content, "ts": ts, "cite": f"per wal {str(ts)[:10]}",
                                "conf": m.get("confidence", 0.5), "unsourced": unsourced(_statement_state(meta))})
            if len(wal_entries) >= WAL_RESERVE:
                break
        if wal_entries:
            out = out[: max(0, (k + 2) - len(wal_entries))] + wal_entries
        return sourced_first(out)[: k + 2]          # unsourced entries rank last, so they are trimmed first
    finally:
        con.close()


# WAL->semantic drain §5.2: how many matching ANCHORED wal facts query_worldview guarantees a
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
