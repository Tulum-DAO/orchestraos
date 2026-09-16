"""Build C — wal -> TranscriptEnvelope projection (INERT, read-only).

The thin, DETERMINISTIC map from seq-ordered WAL rows (store.py grammar) to the
grammar-v2 TranscriptEnvelope (contract/transcript/transcript.v2.schema.json)
that iOS/watch (OrchestraUltra), the web dashboard, and FABLE-5 render. This is
Build B1(b)'s "replace-with-clean" reconcile target: the fast pty token stream
is replaced by the clean block rendered through THIS projection.

SAFETY CENTERPIECE — render THROUGH the stage-2 sanitizer, never raw WAL bodies:
  * MODEL VOICE (prompt / response / thinking / tool_call args) is dispositioned
    through normalize.render_body — the SAME seam Build A/B1 use, NOT
    re-implemented here. Order is load-bearing (normalize-before-check,
    fail-CLOSED, class-defense flagged->block, then known-instance court_scrub).
    A FLAGGED lineage block-renders a STRUCTURAL summary (the WAL row's
    content-free summary) — ZERO verbatim model voice ever reaches the envelope.
  * WORLD OUTPUT (tool_result) is court_scrub'd (layer-2 known-instance) but not
    class-defense-blocked — world output MAY render for flagged lineages
    (the contagion boundary is model-voice-specific, digest.py §3.3 precedent).

Provider-agnostic: the runtime column drives render_body's normalization
(gemini protobuf-decode, claude/codex identity) so contamination re-serialized
by any provider is normalized to canonical text BEFORE any scrub/flag check.

READ-ONLY: reads store.events(lineage_root) + an injected resolve_body callback.
No writes to the WAL, no /proc, no pipe-pane, no wall-clock in the output ->
same seq-ordered rows in, same envelope out (seek-safe).
"""
import json

from .normalize import normalize_body, render_body
from .court_scrub import court_scrub

GRAMMAR_VERSION = 2

# kinds that carry model voice (rendered through the class-defense sanitizer)
_MODEL_VOICE_KINDS = ("prompt", "response", "tool_call")
# kinds with NO defined TranscriptEnvelope item -> omitted (never fabricate a
# mapping the grammar doesn't define — the Build A gemini-step=marker lesson).
_OMIT_KINDS = ("file_mod", "git", "proc", "ctx", "marker")

_INPUT_CAP = 2000
_TRUNC = "…[truncated]"
_SUMMARY_MAX = 120
_SPAWN_BRIEF_MIN = 800


# --------------------------------------------------------------------------
# sanitizer boundary helpers (REUSE normalize.render_body / court_scrub)
# --------------------------------------------------------------------------

def _withheld(summary):
    """Structural, provably content-free placeholder for a blocked model-voice
    item. The WAL summary is structural by the adapter DP-1 contract (kind+size /
    tool name); belt-and-suspenders court_scrub it anyway (digest.py precedent)."""
    base = f"[withheld: {summary}]" if summary else "[content withheld]"
    clean, tripped = court_scrub(base)
    return "[content withheld]" if tripped else clean


def _voice_text(row, resolve_body, lineage_flagged, flag_readable):
    """Resolve + disposition a MODEL-VOICE body. Returns (text, blocked).
    Never returns raw bytes: render_body normalizes-then-checks and returns
    text='' for block, so a blocked item renders only the structural summary."""
    raw = resolve_body(row["body_ref"]) if (resolve_body and row["body_ref"]) else None
    disp = render_body(row["runtime"], raw if raw is not None else b"",
                       lineage_flagged=lineage_flagged, flag_readable=flag_readable)
    if disp["stream_mode"] == "block":
        return _withheld(row["summary"]), True
    return disp["text"], False


def _world_text(row, resolve_body):
    """Resolve + court_scrub a WORLD-OUTPUT body (tool_result). Known-instance
    scrub only; not class-defense-blocked (world output may render for flagged
    lineages). A tripped scrub hard-excludes the span (empty)."""
    raw = resolve_body(row["body_ref"]) if (resolve_body and row["body_ref"]) else None
    if raw is None:
        return ""
    # normalize-before-scrub, provider-correct (gemini protobuf-decode) — the
    # same seam model voice uses; scrub NEVER runs on provider-raw bytes.
    text = normalize_body(row["runtime"], raw)
    clean, tripped = court_scrub(text)
    return "" if tripped else clean


# --------------------------------------------------------------------------
# structured tool args
# --------------------------------------------------------------------------

def _cap(v):
    if isinstance(v, str) and len(v) > _INPUT_CAP:
        return v[:_INPUT_CAP] + _TRUNC, True
    return v, False


def _cap_input(args):
    """Size-cap each string value (contract: 2000 chars + '…[truncated]'); build
    input_full ONLY when something was truncated (contract format: 'key: value'
    for single-line, '── key ──\\n<value>' for multi-line, joined by newlines)."""
    capped = {}
    parts = []
    truncated = False
    for k, v in args.items():
        cv, tr = _cap(v)
        capped[k] = cv
        truncated = truncated or tr
        if isinstance(v, str) and "\n" in v:
            parts.append(f"── {k} ──\n{v}")
        elif isinstance(v, str):
            parts.append(f"{k}: {v}")
        else:
            parts.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return capped, ("\n".join(parts) if truncated else None)


def _tool_node(row, resolve_body, lineage_flagged, flag_readable):
    tool = (row["summary"] or "tool").splitlines()[0][:_SUMMARY_MAX] or "tool"
    node = {"t": "tool", "tool": tool, "summary": tool, "input": {},
            "input_full": None, "seq": row["seq"]}
    text, blocked = _voice_text(row, resolve_body, lineage_flagged, flag_readable)
    if blocked or not text:
        return node  # flagged/unresolved -> tool name only, no raw args
    args = None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        inner = obj.get("input")
        args = inner if isinstance(inner, dict) else obj
    if isinstance(args, dict):
        capped, full = _cap_input(args)
        node["input"] = capped
        node["input_full"] = full
    else:
        capped, _ = _cap(text)
        node["input"] = {"raw": capped}
    return node


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------

def _parse_rows(rows, resolve_body, lineage_flagged, flag_readable):
    """Rows (seq-ordered) -> intermediate render nodes. Model voice through the
    sanitizer; side-effect kinds omitted. is_system marks the first user text
    that is a spawn brief (len>800 OR starts 'You are')."""
    nodes = []
    seen_user_text = False
    for row in rows:
        kind = row["kind"]
        if kind in _OMIT_KINDS:
            continue
        if kind == "prompt":
            text, _ = _voice_text(row, resolve_body, lineage_flagged, flag_readable)
            is_system = (not seen_user_text
                         and (len(text) > _SPAWN_BRIEF_MIN or text.startswith("You are")))
            seen_user_text = True
            nodes.append({"t": "user_text", "text": text,
                          "is_system": is_system, "seq": row["seq"]})
        elif kind == "response":
            text, _ = _voice_text(row, resolve_body, lineage_flagged, flag_readable)
            if (row["summary"] or "").startswith("thinking"):
                nodes.append({"t": "thinking", "text": text, "seq": row["seq"]})
            else:
                nodes.append({"t": "asst_text", "text": text, "seq": row["seq"]})
        elif kind == "tool_call":
            nodes.append(_tool_node(row, resolve_body, lineage_flagged, flag_readable))
        elif kind == "tool_result":
            nodes.append({"t": "tool_result",
                          "text": _world_text(row, resolve_body), "seq": row["seq"]})
    return nodes


def _flat_items(nodes):
    items = []
    for n in nodes:
        if n["t"] == "user_text":
            it = {"kind": "text", "role": "user", "text": n["text"]}
            if n["is_system"]:
                it["is_system"] = True
            items.append(it)
        elif n["t"] == "asst_text":
            items.append({"kind": "text", "role": "assistant", "text": n["text"]})
        elif n["t"] == "thinking":
            items.append({"kind": "thinking", "role": "assistant", "text": n["text"]})
        elif n["t"] == "tool":
            it = {"kind": "tool_use", "role": "assistant", "tool": n["tool"],
                  "input": n["input"], "summary": n["summary"]}
            if n["input_full"] is not None:
                it["input_full"] = n["input_full"]
            items.append(it)
        elif n["t"] == "tool_result":
            items.append({"kind": "tool_result", "role": "user", "text": n["text"]})
    return items


def _render_items(nodes):
    """Server-paired render nodes: tool_result attaches under its nearest
    preceding unpaired tool node; a result with no in-window tool is dropped
    (contract: no bare tool_result in render_items)."""
    out = []
    for idx, n in enumerate(nodes):
        key = f"{n['seq']}:{idx}"
        if n["t"] == "user_text":
            r = {"kind": "user", "text": n["text"], "key": key}
            if n["is_system"]:
                r["is_system"] = True
            out.append(r)
        elif n["t"] == "asst_text":
            out.append({"kind": "assistant", "text": n["text"], "key": key})
        elif n["t"] == "thinking":
            out.append({"kind": "thinking", "text": n["text"], "key": key})
        elif n["t"] == "tool":
            r = {"kind": "tool", "tool": n["tool"], "summary": n["summary"],
                 "input": n["input"], "key": key}
            if n["input_full"] is not None:
                r["input_full"] = n["input_full"]
            out.append(r)
        elif n["t"] == "tool_result":
            for prev in reversed(out):
                if prev["kind"] == "tool" and "result" not in prev:
                    prev["result"] = n["text"]
                    break
            # else: unpaired world output -> dropped from render_items
    return out


def project_envelope(store, lineage_root, *, agent_id, session_id,
                     resolve_body=None, lineage_flagged=False,
                     flag_readable=True, limit=None):
    """Project one lineage's WAL rows into a grammar-v2 TranscriptEnvelope.

    store          : anything exposing events(lineage_root) -> seq-ordered rows
                     (WalStore, or a read-only clone). NEVER written.
    resolve_body   : body_ref -> raw body (str/bytes) or None. Injected so the
                     projection stays read-only and file-layout-agnostic; None
                     resolves nothing (structural-only render).
    lineage_flagged / flag_readable : the durable per-lineage court flag
                     (provider-blind, keyed on lineage_root). flagged OR
                     unreadable -> model voice block-renders.
    limit          : keep only the last N envelope items (render_items pair
                     within that window).

    Deterministic + seek-safe: pure function of the seq-ordered rows.
    """
    rows = store.events(lineage_root)
    nodes = _parse_rows(rows, resolve_body, lineage_flagged, flag_readable)
    if limit is not None and limit >= 0:
        nodes = nodes[-limit:] if limit else []
    return {
        "agent_id": agent_id,
        "session_id": session_id,
        "grammar_version": GRAMMAR_VERSION,
        "items": _flat_items(nodes),
        "render_items": _render_items(nodes),
    }
