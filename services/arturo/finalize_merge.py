# finalize_merge.py — client-authoritative finalize merge (Piece A / DEC-1786426323, AGY-approved).
#
# When the operator ends a call the app POSTs the client-authoritative transcript to the gateway, which
# writes vc_client_<key>.json (source=client, status=ended, full-text spine) + notifies the proxy
# POST /finalize-call. This module holds the PURE merge logic: link the overlapping SERVER journal
# (the one the proxy built turn-by-turn, carrying the tool events), fold its TOOL turns + the
# surface-sidecar UI-nav events into the client spine by timestamp, and produce the summary. The
# server journal is then marked superseded_by:<client_id> so it never separately injects to GM.
#
# Linking priority (server journals have NO conv_id before conv-id stamping lands, so all three
# paths matter): (a) conv_id equality [primary], (b) the surface sidecar's overlay entity.id ==
# the server vc id [secondary — verified on real call vc_client_2f140442c137 -> vc_e91877ed787b1316],
# (c) [started_at, ended_at] time-window overlap [fallback].
import json as _json
import logging as _logging

log = _logging.getLogger(__name__)


def extract_surface_server_ids(surface_lines):
    """Parse .surface.jsonl lines → the set of server vc ids referenced by any stack overlay
    entity (the live-call overlay names the server journal). Robust to malformed lines."""
    ids = set()
    for line in surface_lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = _json.loads(line)
        except Exception:
            continue
        for frame in ev.get("stack", []) or []:
            ent = frame.get("entity") or {}
            vid = ent.get("id")
            if isinstance(vid, str) and vid.startswith("vc_") and not vid.startswith("vc_client_"):
                ids.add(vid)
    return ids


def _overlaps(a_start, a_end, b_start, b_end):
    if None in (a_start, a_end, b_start, b_end):
        return False
    return a_start <= b_end and b_start <= a_end


def pick_server_journal(client, candidates, surface_server_ids=None):
    """Choose the SERVER journal that belongs to the same physical call as `client`.
    `client`: the client journal dict. `candidates`: list[(call_id, dict)] of OTHER journals.
    `surface_server_ids`: set from extract_surface_server_ids(). Returns the server call_id or None.
    Only considers non-client, non-superseded journals. Priority conv_id > surface-overlay > time."""
    surface_server_ids = surface_server_ids or set()
    c_conv = client.get("conv_id")
    c_start, c_end = client.get("started_at"), client.get("ended_at")

    def eligible(d):
        return d.get("source") != "client" and not d.get("superseded_by")

    # (a) conv_id
    if c_conv:
        for cid, d in candidates:
            if eligible(d) and d.get("conv_id") and d.get("conv_id") == c_conv:
                return cid
    # (b) surface-overlay id
    for cid, d in candidates:
        if eligible(d) and cid in surface_server_ids:
            return cid
    # (c) time-window overlap — pick the richest (most tool turns) overlapping journal.
    # A still-LIVE server journal has NO ended_at yet (esp. now that the finalize watchdog waits
    # 900s): at client-finalize time the matching server leg is usually still 'live'. Treat a
    # live candidate as ongoing (open through now) so it still overlaps — otherwise the client
    # finalize links server=None, DROPS the call's tool turns from the gm inject, and orphans a
    # live server journal that the watchdog later double-injects (live-caught build-162, 2026-09-06).
    import time as _time
    _now = _time.time()
    best, best_tools = None, -1
    for cid, d in candidates:
        if not eligible(d):
            continue
        d_start, d_end = d.get("started_at"), d.get("ended_at")
        if d_end is None:                       # live/ongoing server journal — open through now
            d_end = max(_now, c_end or _now, d_start or _now)
        if _overlaps(c_start, c_end, d_start, d_end):
            n_tools = sum(1 for t in d.get("turns", []) if t.get("role") == "tool")
            if n_tools > best_tools:
                best, best_tools = cid, n_tools
    return best


def _ts(x):
    t = x.get("ts")
    return t if isinstance(t, (int, float)) else 0.0


def merge_timeline(client_turns, server_turns, surface_events):
    """Fold the SERVER journal's TOOL turns + the surface UI-nav events into the client spine,
    ordered by ts. The client user/arturo turns are the authoritative full-text spine (server
    user/arturo turns are dropped — truncated dupes) EXCEPT for a role the client journal is
    MISSING ENTIRELY, which is recovered from the server (see fallback below). Surface events
    become lightweight role='surface' nav markers. Stable sort keeps original order within equal ts."""
    merged = list(client_turns)                       # spine: full-text user/arturo turns
    # FALLBACK (div-coupling regression 2026-08-25): a role the client journal has ZERO of
    # cannot be a "truncated dupe" of a client turn — recover it from the server. The client
    # endcall journal came back with no user turns (its transcript source was mis-pointed to
    # the brain thread) while the server HAD transcribed them; without this, those user turns
    # are lost from the journal + the gm-injected record. Normal calls (client HAS the role)
    # still drop server dupes.
    _client_roles = {t.get("role") for t in client_turns}
    for t in server_turns:
        role = t.get("role")
        if role == "tool":
            merged.append(t)                          # fold tool events only
        elif role in ("user", "arturo") and role not in _client_roles:
            merged.append(t)                          # recover a role the client lacks entirely
    for ev in surface_events:
        focused = ev.get("focused")
        stack = ev.get("stack") or []
        hint = None
        for frame in reversed(stack):                 # topmost meaningful hint
            if frame.get("hint"):
                hint = frame.get("hint")
                break
        merged.append({"role": "surface", "ts": ev.get("ts"),
                       "focused": focused, "hint": hint})
    merged.sort(key=_ts)                              # stable → equal-ts order preserved
    return merged


def summarize(merged_turns):
    """2-line headline for the client journal's `summary` field + the GM injection (summary-only;
    the frozen [voice-call:] marker carries the path to the full client JSON for on-demand mining)."""
    n_turns = sum(1 for t in merged_turns if t.get("role") in ("user", "arturo"))
    n_tools = sum(1 for t in merged_turns if t.get("role") == "tool")
    last_arturo = next((t.get("text", "") for t in reversed(merged_turns)
                        if t.get("role") == "arturo"), "")
    return f"Voice call: {n_turns} turns, {n_tools} tool runs.\n{last_arturo}"


def native_tool_turns(conv_id, ledger_path):
    """A native call's tool runs as journal tool turns, read from the native tool ledger (tool_calls, by
    conversation_id). The native server writes no journal of its own, so finalize had no tool turns to fold in and the
    summary said "0 tool runs" on a call with three questions to gm (2026-11-26). Read-only; [] for any other call,
    a missing ledger or a read error."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    if not conv_id or not str(conv_id).startswith("native-") or not _Path(ledger_path).exists():
        return []
    try:
        con = _sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute("SELECT tool_call_id, name, args_json, result_json, created FROM tool_calls "
                               "WHERE conversation_id = ? ORDER BY created", (conv_id,)).fetchall()
        finally:
            con.close()
    except _sqlite3.Error as e:
        log.warning("native tool ledger unreadable for %s (%s): its tool runs are not in the journal", conv_id, e)
        return []
    out = []
    for call_id, name, args_json, result_json, created in rows:
        try:
            args = _json.loads(args_json or "{}")
        except ValueError:
            args = {}
        try:
            res = _json.loads(result_json or "{}")
        except ValueError:
            res = {}
        res = res if isinstance(res, dict) else {}
        # the run's own outcome: a gm_command that only promised "gm has it" is ok:false, not done
        out.append({"role": "tool", "tool": name, "input": args if isinstance(args, dict) else {},
                    "result": str(res.get("result", ""))[:300], "status": "done" if res.get("ok") is True else "not_ok",
                    "ts": float(created), "call_id": call_id, "source": "native"})
    return out
