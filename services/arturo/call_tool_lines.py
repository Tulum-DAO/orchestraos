"""call_tool_lines.py — every tool a voice call ran, as a line in that call's chat thread.

A native (dashboard GPT-Live) call keeps its tool runs in the native service's own ledger (native/tool_ledger.py,
keyed by the call's conversation id), never in the call journal, so its thread showed what was said and nothing of
what was done (Shaw, 2026-11-26: "The issue wasn't that the tool calls didn't happen, it's that they weren't logged in
the chat."). When the call is archived into its thread (CallThreads.archive), its runs are read from that ledger and
each becomes one short line where it happened among the spoken ones: the tool, the gist of the ask, the outcome
("Asked gm: <ask> · gm has it"). Never the raw arguments or the result text; secret shapes blanked; a spending tool
(private finances) shows its name and outcome only.
"""
import ast
import json
import logging
import sqlite3

log = logging.getLogger("arturo.call_tool_lines")

KIND = "call_tool"
GIST_MAX = 120
SPENDING_PREFIX = "spending_"          # services/arturo/spending_tools.py PREFIX
# The argument that says what was asked, first one present wins.
ASK_KEYS = ("prompt", "question", "query", "message", "task", "text", "title", "agent_id", "name", "url", "target")
# Ledger rows this long before the call's first timed line or after its last still belong to it (a tool started as the
# call opened; one that finished after the last caption). The call id match is exact; the window only bounds the read.
WINDOW_BEFORE_S = 120
WINDOW_AFTER_S = 600


def _clip(text, n=GIST_MAX):
    from services.arturo.thread_context import _redact
    text = " ".join(_redact(str(text or "")).split())
    if len(text) <= n:
        return text
    cut = text[:n - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def _args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        for parse in (json.loads, ast.literal_eval):        # the relay journal keeps str(dict)
            try:
                out = parse(raw)
                return out if isinstance(out, dict) else {}
            except (ValueError, SyntaxError, TypeError):
                continue
    return {}


def _gist(args):
    for k in ASK_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return _clip(v)
    return ""


def _gm_outcome(ok, text):
    t = (text or "").lstrip()
    if t.startswith("gm says:"):
        return "gm answered"
    if "gm has it" in t or t.startswith("Sent to gm") or "already went to gm" in t:
        return "gm has it"
    return "didn't reach gm" if not ok or t.startswith("Could not reach gm") else "sent"


def outcome(name, result):
    """(ok, word) from a tool's function output ({ok, result} or {ok: False, error, detail})."""
    r = result if isinstance(result, dict) else {"ok": bool(result), "result": str(result or "")}
    text = str(r.get("result") or "")
    if name == "gm_command":
        word = _gm_outcome(r.get("ok") is not False, text)
        return word != "didn't reach gm", word
    if r.get("ok") is False:
        return False, ("refused" if r.get("error") else "didn't finish")
    return True, "done"


def tool_line(name, args, result):
    name = str(name or "tool")
    ok, word = outcome(name, result)
    if name.startswith(SPENDING_PREFIX):
        return f"{name} · {word}"
    gist = _gist(_args(args))
    if name == "gm_command":
        return f"Asked gm: {gist} · {word}" if gist else f"Asked gm · {word}"
    return f"{name}: {gist} · {word}" if gist else f"{name} · {word}"


def line_for(t):
    """A tool turn (ledger_turns', or a relay journal's {tool, input, result, status}) as its thread line."""
    if "output" in t:
        result = t["output"]
    else:
        result = {"ok": (t.get("status") or "done") not in ("error", "failed", "refused"), "result": t.get("result")}
    return tool_line(t.get("tool") or t.get("name"), t.get("input"), result)


def line_ok(t):
    if "output" in t:
        return outcome(t.get("tool") or t.get("name"), t["output"])[0]
    return (t.get("status") or "done") not in ("error", "failed", "refused")


def _ts(t):
    ts = t.get("ts")
    if isinstance(ts, str):
        try:
            ts = float(ts)
        except ValueError:
            return None
    return float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0 else None


def ledger_turns(call_id, turns, db):
    """This call's tool runs from the native ledger at `db`, as tool turns. The ledger keys a run by the call's
    conversation id (native-...), the thread card by its journal id (vc_client_<sha1(conv)[:12]>): matched exactly.
    Read-only; no ledger, no timed line, or no run is []. Raises on a ledger it cannot read (the caller logs it)."""
    from services.arturo.relay_live_bridge import journal_call_id
    stamps = [s for s in (_ts(t) for t in turns or () if isinstance(t, dict)) if s is not None]
    if not stamps or not call_id:
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    except sqlite3.OperationalError:
        return []                                            # no native service on this host: no runs
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tool_call_id, conversation_id, name, args_json, result_json, created FROM tool_calls"
            " WHERE created BETWEEN ? AND ? ORDER BY created",
            (min(stamps) - WINDOW_BEFORE_S, max(stamps) + WINDOW_AFTER_S)).fetchall()
    except sqlite3.OperationalError:
        return []                                            # a ledger without the table yet
    finally:
        conn.close()
    out = []
    for r in rows:
        if journal_call_id(r["conversation_id"]) != call_id:
            continue
        try:
            args, result = json.loads(r["args_json"] or "{}"), json.loads(r["result_json"] or "{}")
        except ValueError:
            args, result = {}, {"ok": False}
        out.append({"role": "tool", "tool": r["name"], "input": args, "output": result, "ts": r["created"],
                    "tool_call_id": r["tool_call_id"]})
    return out


def merge(turns, tools):
    """`turns` in their own order, each tool turn put before the first timed turn that came after it (untimed turns
    are never moved past); a tool already in `turns` (same tool_call_id) is not added again; leftovers go last."""
    have = {t.get("tool_call_id") for t in turns if isinstance(t, dict) and t.get("tool_call_id")}
    pending = sorted((t for t in tools or () if t.get("tool_call_id") not in have or not t.get("tool_call_id")),
                     key=lambda t: (_ts(t) is None, _ts(t) or 0.0))
    out, i = [], 0
    for t in turns:
        at = _ts(t) if isinstance(t, dict) else None
        while at is not None and i < len(pending) and _ts(pending[i]) is not None and _ts(pending[i]) < at:
            out.append(pending[i])
            i += 1
        out.append(t)
    out.extend(pending[i:])
    return out
