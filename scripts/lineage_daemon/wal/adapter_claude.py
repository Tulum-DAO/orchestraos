"""claude WAL adapter — tail the harness-written sid.jsonl and NORMALIZE into
canonical wal_events (spec §2.3). CAPTURE-ONLY: the LLM authors none of this
stream; we only read new bytes and index them.

Zero-in-band-exit is a property of HOW we tail, not of the dying agent:
  - we consume only bytes up to the LAST NEWLINE, so a line half-written when
    Blue is kill -9'd is never half-captured (it is simply not yet a complete
    event); everything the harness flushed is captured, in order.
  - the resume cursor is a byte offset persisted in wal_cursors, so a tailer
    crash re-reads nothing already consumed.

Model-voice discipline (risk-0/DP-1): summaries for assistant text/thinking and
tool_call are STRUCTURAL (kind + size + tool name) — never a content snippet.
Full bodies stay in the source jsonl and are resolved later via body_ref. This
keeps raw model-voice (the court-contagion vector) out of the WAL index.
"""
import hashlib
from datetime import datetime


def _parse_ts(raw):
    if not raw:
        return 0.0
    try:
        s = raw.replace("Z", "+00:00") if isinstance(raw, str) else raw
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _jsonl_tokens(usage):
    return (int(usage.get("input_tokens", 0))
            + int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0)))


class ClaudeWalAdapter:
    runtime = "claude"

    def __init__(self, store, lineage_root, generation):
        self._store = store
        self._lineage_root = lineage_root
        self._generation = generation

    def tail(self, source_path):
        """Consume new complete lines from source_path; return #events appended.

        Only whole newline-terminated lines are consumed; a partial trailing
        line is left for a later pass (or never, if the writer died — it was
        never a complete event). The byte cursor advances only past consumed
        lines.
        """
        cur = self._store.get_cursor(source_path)
        start = cur["last_off"] if cur else 0
        try:
            with open(source_path, "rb") as fh:
                fh.seek(start)
                chunk = fh.read()
        except FileNotFoundError:
            return 0
        if not chunk:
            return 0

        # Consume only up to the last newline; keep the partial remainder.
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return 0  # no complete line yet
        consumable = chunk[:last_nl + 1]

        last_ev = self._store.last_event()
        prev_sid = [last_ev["sid"] if last_ev else None]  # mutable box across lines

        # Condition B (claude/codex are NOT a naive finally-lift): the offset is
        # advanced BEFORE the line is emitted, so a finally on `offset` would
        # persist a cursor PAST a line that failed to append = silent gap. We
        # track committed_offset, advanced to a line's end ONLY after its
        # _emit_line returns, and wrap each line's (possibly multi-) appends in
        # ONE store txn so a mid-line raise leaves no half-committed residual.
        # The finally persists committed_offset even when the lane contains a
        # raise: committed rows are not re-read (no double-capture) and the
        # failed line IS re-read next tick (no gap).
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
                                                    line_start, prev_sid)
                committed_offset = offset  # this line fully consumed + committed
        finally:
            self._store.set_cursor(
                source_path, self._lineage_root, last_off=committed_offset,
                last_seq=self._store.max_seq())
        return appended

    def _emit_line(self, text_bytes, source_path, line_start, prev_sid):
        import json
        try:
            obj = json.loads(text_bytes)
        except ValueError:
            # unparseable complete line: record a marker so the gap is visible,
            # never silently drop (integrity is still the line hash).
            self._append("marker", "unparseable-line", source_path, line_start,
                         text_bytes, sid="?", ts=0.0)
            return 1

        integrity = hashlib.sha256(text_bytes).hexdigest()
        sid = obj.get("sessionId") or "?"
        ts = _parse_ts(obj.get("timestamp"))
        n = 0

        # sid change (resume/compact) -> marker:rotate BEFORE the line's events
        if prev_sid[0] is not None and sid != "?" and sid != prev_sid[0]:
            self._append("marker", f"rotate sid {prev_sid[0]}->{sid}",
                         source_path, line_start, text_bytes, sid=sid, ts=ts)
            n += 1
        if sid != "?":
            prev_sid[0] = sid

        typ = obj.get("type")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else None

        if typ == "assistant" and msg is not None:
            n += self._emit_content(msg.get("content"), source_path, line_start,
                                    integrity, sid, ts, is_assistant=True)
            usage = msg.get("usage")
            if isinstance(usage, dict):
                self._append("ctx", f"jsonl_tokens={_jsonl_tokens(usage)} "
                             f"out={int(usage.get('output_tokens', 0))}",
                             source_path, line_start, text_bytes, sid=sid, ts=ts,
                             integrity=integrity)
                n += 1
        elif typ == "user" and msg is not None:
            n += self._emit_content(msg.get("content"), source_path, line_start,
                                    integrity, sid, ts, is_assistant=False)
        elif typ == "system":
            self._append("marker", f"system:{obj.get('subtype', '?')}",
                         source_path, line_start, text_bytes, sid=sid, ts=ts,
                         integrity=integrity)
            n += 1
        elif typ == "file-history-snapshot":
            self._append("marker", "file-history-snapshot", source_path,
                         line_start, text_bytes, sid=sid, ts=ts,
                         integrity=integrity)
            n += 1
        elif typ == "queue-operation":
            self._append("marker", f"queue:{obj.get('operation', '?')}",
                         source_path, line_start, text_bytes, sid=sid, ts=ts,
                         integrity=integrity)
            n += 1
        else:
            self._append("marker", f"type:{typ}", source_path, line_start,
                         text_bytes, sid=sid, ts=ts, integrity=integrity)
            n += 1
        return n

    def _emit_content(self, content, source_path, line_start, integrity, sid,
                      ts, is_assistant):
        n = 0
        if content is None:
            return 0
        if isinstance(content, str):
            kind = "response" if is_assistant else "prompt"
            self._append(kind, f"text ({len(content)} chars)", source_path,
                         line_start, None, sid=sid, ts=ts, integrity=integrity,
                         body_ref=f"{source_path}:{line_start}")
            return 1
        if not isinstance(content, list):
            return 0
        for idx, item in enumerate(content):
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            body_ref = f"{source_path}:{line_start}#c{idx}"
            if itype == "tool_use":
                self._append("tool_call", f"{item.get('name', '?')}",
                             source_path, line_start, None, sid=sid, ts=ts,
                             integrity=integrity, body_ref=body_ref)
                n += 1
            elif itype == "tool_result":
                out = item.get("content")
                size = len(out) if isinstance(out, str) else 0
                self._append("tool_result", f"result ({size} chars)",
                             source_path, line_start, None, sid=sid, ts=ts,
                             integrity=integrity, body_ref=body_ref)
                n += 1
            elif itype == "thinking":
                self._append("response", "thinking", source_path, line_start,
                             None, sid=sid, ts=ts, integrity=integrity,
                             body_ref=body_ref)
                n += 1
            elif itype == "text":
                txt = item.get("text", "")
                kind = "response" if is_assistant else "prompt"
                self._append(kind, f"text ({len(txt)} chars)", source_path,
                             line_start, None, sid=sid, ts=ts,
                             integrity=integrity, body_ref=body_ref)
                n += 1
        return n

    def _append(self, kind, summary, source_path, source_off, raw_bytes, *,
                sid, ts, integrity=None, body_ref=None):
        if integrity is None and raw_bytes is not None:
            integrity = hashlib.sha256(raw_bytes).hexdigest()
        self._store.append(
            ts=ts, lineage_root=self._lineage_root, generation=self._generation,
            sid=sid, runtime=self.runtime, kind=kind, summary=summary,
            body_ref=body_ref or f"{source_path}:{source_off}",
            source_path=source_path, source_off=source_off, integrity=integrity)
