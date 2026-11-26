"""codex WAL adapter — tail the codex rollout jsonl and NORMALIZE into canonical
wal_events (Build A per-provider adapter, mirrors adapter_claude discipline).

Source: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (append-only jsonl the codex
binary writes; the LLM authors none of the framing — we only index new bytes).
codex = "rollout token events" per the commission; the existing screen-tier
parser is agent-status.py:897 `_codex_context_pct` (rollout token_count) — this
adapter is the WAL-lane counterpart, normalizing the whole rollout stream.

Line shapes (verified vs contract/transcript/fixtures/codex-basic.input.jsonl):
  session_meta                         -> marker (carries the sid for the stream)
  response_item message role user/dev  -> prompt   (structural summary)
  response_item message role assistant -> response  (model voice: STRUCTURAL only)
  response_item reasoning              -> response  (thinking; STRUCTURAL only)
  response_item function_call          -> tool_call (summary = tool name)
  response_item function_call_output   -> tool_result (summary = size)
  event_msg token_count                -> ctx       (summary = total_tokens)

Zero-in-band-exit + crash-safety are properties of HOW we tail (whole-newline
consume + byte-offset cursor), identical to adapter_claude — a half-written line
is simply not yet a complete event.

Model-voice discipline (risk-0/DP-1): summaries for assistant text / reasoning
are STRUCTURAL (kind + size) — never a content snippet. Full bodies stay in the
rollout and are resolved later via body_ref. This keeps raw model-voice (the
court-contagion vector) out of the WAL index — provider-agnostically identical
to the claude adapter's rule.
"""
import hashlib
import json
from datetime import datetime


def _parse_ts(raw):
    if not raw:
        return 0.0
    try:
        s = raw.replace("Z", "+00:00") if isinstance(raw, str) else raw
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _text_len(content):
    """Total chars across a codex content array (input_text/output_text) without
    ever returning the text itself — structural size only."""
    if isinstance(content, str):
        return len(content)
    if not isinstance(content, list):
        return 0
    total = 0
    for item in content:
        if isinstance(item, dict):
            total += len(item.get("text", "") or "")
    return total


class CodexWalAdapter:
    runtime = "codex"

    def __init__(self, store, lineage_root, generation):
        self._store = store
        self._lineage_root = lineage_root
        self._generation = generation
        self._line_ts = 0.0

    def _dated(self, ts, source_path):
        """gm msg_396a37f3 (4)G: never write ts=0. A line with no timestamp of its own (claude
        last-prompt / mode / permission-mode / ai-title / atis-latch, unparseable lines) takes
        the last real ts seen in the same file: no later than the truth, so max(ts) readers
        are unchanged. 0.0 only before this adapter has seen any dated line in that file."""
        last = self.__dict__.setdefault("_last_ts", {})
        if ts:
            last[source_path] = ts
            return ts
        return last.get(source_path, 0.0)

    def _stamp_looked(self, source_path, off):
        """Record that this pass LOOKED at source_path and had nothing to consume.

        gm's ranked item 3: `set_cursor` — the ONLY writer of wal_cursors.updated —
        lives inside tail's try/finally, so every early return above it stamped
        NOTHING and `updated` could not tell "looked, nothing new" from "never
        looked" (the orchestra-builder 4.3h stall was invisible to it for exactly
        this reason, and bg_wal_census's own docstring asserts the stronger
        reading). The source EXISTS and was read here, so insert-or-update is
        truthful; the offset is written UNCHANGED, so this is never a consumption.
        """
        self._store.set_cursor(source_path, self._lineage_root, last_off=off,
                               last_seq=self._store.max_seq())

    def tail(self, source_path):
        """Consume new complete lines from source_path; return #events appended.

        Only whole newline-terminated lines are consumed; a partial trailing line
        is left for a later pass. The byte cursor advances only past consumed
        lines (no re-read on restart)."""
        cur = self._store.get_cursor(source_path)
        start = cur["last_off"] if cur else 0
        try:
            with open(source_path, "rb") as fh:
                fh.seek(start)
                chunk = fh.read()
        except FileNotFoundError:
            # LOOKED, found no source. Touch an existing cursor only — never mint one
            # (store.touch_cursor); a vanished source must not look like a live one.
            self._store.touch_cursor(source_path)
            return 0
        if not chunk:
            self._stamp_looked(source_path, start)
            return 0

        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            self._stamp_looked(source_path, start)
            return 0
        consumable = chunk[:last_nl + 1]

        # sid is stream-scoped: recover the last-seen sid so a mid-stream resume
        # keeps attributing events to the session_meta id.
        last_ev = self._store.last_event()
        sid_box = [last_ev["sid"] if last_ev and last_ev["sid"] != "?" else None]

        # Condition B (see adapter_claude): the byte offset advances BEFORE the
        # line is emitted, so we track committed_offset (advanced only after
        # _emit_line returns) and wrap each line's appends in ONE store txn. The
        # finally persists committed_offset even when the lane contains a raise:
        # committed lines are not re-read (no double-capture) and the failed line
        # IS re-read next tick (no gap).
        appended = 0
        offset = start
        committed_offset = start
        try:
            for raw in consumable.splitlines(keepends=True):
                line_start = offset
                offset += len(raw)
                text = raw.rstrip(b"\n").rstrip(b"\r")
                if text.strip():
                    with self._store.transaction():
                        appended += self._emit_line(text, source_path,
                                                    line_start, sid_box)
                committed_offset = offset  # this line fully consumed + committed
        finally:
            self._store.set_cursor(
                source_path, self._lineage_root, last_off=committed_offset,
                last_seq=self._store.max_seq())
        return appended

    def _emit_line(self, text_bytes, source_path, line_start, sid_box):
        self._line_ts = 0.0
        try:
            obj = json.loads(text_bytes)
        except ValueError:
            self._append("marker", "unparseable-line", source_path, line_start,
                         text_bytes, sid="?", ts=0.0)
            return 1

        # Every codex line carries its own top-level timestamp; the emitters below pass ts=0.0
        # and _append dates the row from this line (gm (4)G: codex WALs were undated).
        self._line_ts = _parse_ts(obj.get("timestamp")) if isinstance(obj, dict) else 0.0
        integrity = hashlib.sha256(text_bytes).hexdigest()
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

        if typ == "session_meta":
            sid = payload.get("id") or "?"
            if sid != "?":
                sid_box[0] = sid
            self._append("marker", "session_meta", source_path, line_start,
                         text_bytes, sid=sid, ts=_parse_ts(payload.get("timestamp")),
                         integrity=integrity)
            return 1

        sid = sid_box[0] or "?"

        if typ == "response_item":
            return self._emit_response_item(payload, source_path, line_start,
                                            integrity, sid)
        if typ == "event_msg":
            return self._emit_event_msg(payload, source_path, line_start,
                                        integrity, sid)

        self._append("marker", f"type:{typ}", source_path, line_start,
                     text_bytes, sid=sid, ts=0.0, integrity=integrity)
        return 1

    def _emit_response_item(self, payload, source_path, line_start, integrity, sid):
        itype = payload.get("type")
        body_ref = f"{source_path}:{line_start}"
        if itype == "message":
            role = payload.get("role")
            size = _text_len(payload.get("content"))
            kind = "response" if role == "assistant" else "prompt"
            self._append(kind, f"text ({size} chars)", source_path, line_start,
                         None, sid=sid, ts=0.0, integrity=integrity,
                         body_ref=body_ref)
            return 1
        if itype == "reasoning":
            # model voice -> STRUCTURAL only (never the reasoning content)
            content = payload.get("content")
            size = len(content) if isinstance(content, str) else _text_len(content)
            self._append("response", f"thinking ({size} chars)", source_path,
                         line_start, None, sid=sid, ts=0.0, integrity=integrity,
                         body_ref=body_ref)
            return 1
        if itype == "function_call":
            self._append("tool_call", f"{payload.get('name', '?')}", source_path,
                         line_start, None, sid=sid, ts=0.0, integrity=integrity,
                         body_ref=body_ref)
            return 1
        if itype == "function_call_output":
            out = payload.get("output")
            size = len(out) if isinstance(out, str) else 0
            self._append("tool_result", f"result ({size} chars)", source_path,
                         line_start, None, sid=sid, ts=0.0, integrity=integrity,
                         body_ref=body_ref)
            return 1
        self._append("marker", f"response_item:{itype}", source_path, line_start,
                     None, sid=sid, ts=0.0, integrity=integrity, body_ref=body_ref)
        return 1

    def _emit_event_msg(self, payload, source_path, line_start, integrity, sid):
        if payload.get("type") == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            self._append("ctx", f"total_tokens={int(info.get('total_tokens', 0) or 0)}",
                         source_path, line_start, None, sid=sid, ts=0.0,
                         integrity=integrity, body_ref=f"{source_path}:{line_start}")
            return 1
        self._append("marker", f"event:{payload.get('type', '?')}", source_path,
                     line_start, None, sid=sid, ts=0.0, integrity=integrity,
                     body_ref=f"{source_path}:{line_start}")
        return 1

    def _append(self, kind, summary, source_path, source_off, raw_bytes, *,
                sid, ts, integrity=None, body_ref=None):
        if integrity is None and raw_bytes is not None:
            integrity = hashlib.sha256(raw_bytes).hexdigest()
        self._store.append(
            ts=self._dated(ts or self._line_ts, source_path), lineage_root=self._lineage_root,
            generation=self._generation,
            sid=sid, runtime=self.runtime, kind=kind, summary=summary,
            body_ref=body_ref or f"{source_path}:{source_off}",
            source_path=source_path, source_off=source_off, integrity=integrity)
