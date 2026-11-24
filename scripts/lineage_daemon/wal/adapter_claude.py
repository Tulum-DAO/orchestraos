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
import glob
import hashlib
import json
import os
import sys
from datetime import datetime

# Kill switch for the subagent pass only (the main tail never reads it): touch
# <wal dir>/SUBAGENT_CAPTURE_DISABLED.
SUBAGENT_DISABLE_FILE = "SUBAGENT_CAPTURE_DISABLED"
SUBAGENT_READ_MAX = 8 << 20      # bytes per file per pass: a backlog drains over passes, bounded memory
SUBAGENT_META_WAIT_S = 120       # a new transcript waits this long for its .meta.json (parent/depth)


def _warn(msg):
    try:
        print(msg, file=sys.stderr)
    except Exception:  # noqa: BLE001 -- a broken stderr must not turn a warning into a raise
        pass


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
        """Tail the seat's own transcript (the return value counts ONLY its events, as before),
        then its subagents' transcripts into the sidecar (gm msg_d0b7b58b item 2). The subagent
        pass runs even when the main file has nothing new: a parent blocked inside an Agent call
        is silent while its helpers write. It can never fail the main tail."""
        n = self._tail_main(source_path)
        try:
            self._tail_subagents(source_path)
        except Exception as e:  # noqa: BLE001 -- telemetry must never break capture
            _warn(f"[wal-subagents] {self._lineage_root}: subagent pass failed: {e!r}")
        return n

    def _tail_subagents(self, source_path):
        """<sid>.jsonl -> <sid>/subagents/agent-<id>.jsonl, every depth (Claude Code 2.1.x)."""
        if not (isinstance(source_path, str) and source_path.endswith(".jsonl")):
            return 0
        if not hasattr(self._store, "append_subagent"):
            return 0                       # a store without the sidecar (older fakes)
        wal_dir = os.path.dirname(getattr(self._store, "path", "") or "")
        if wal_dir and os.path.exists(os.path.join(wal_dir, SUBAGENT_DISABLE_FILE)):
            return 0
        seen = self.__dict__.setdefault("_sub_seen", {})   # path -> (dev, ino, size) fully consumed
        n = 0
        for path in sorted(glob.glob(os.path.join(source_path[:-len(".jsonl")], "subagents",
                                                  "agent-*.jsonl"))):
            try:
                st = os.stat(path)
            except OSError:
                continue
            ident = (st.st_dev, st.st_ino, st.st_size)
            if seen.get(path) == ident:
                continue                   # unchanged since this adapter's last pass: no SQL
            try:
                got, done = _SubagentTail(self, path).run(st)
            except Exception as e:  # noqa: BLE001 -- one bad file never blocks the next (codex r1)
                _warn(f"[wal-subagents] {self._lineage_root}: {os.path.basename(path)}: {e!r}")
                continue
            n += got
            if done:
                seen[path] = ident
            else:
                seen.pop(path, None)
        return n

    def _tail_main(self, source_path):
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
            # LOOKED, found no source. Touch an existing cursor only — never mint one
            # (store.touch_cursor); a vanished source must not look like a live one.
            self._store.touch_cursor(source_path)
            return 0
        if not chunk:
            self._stamp_looked(source_path, start)
            return 0

        # Consume only up to the last newline; keep the partial remainder.
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            self._stamp_looked(source_path, start)
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
            ts=self._dated(ts, source_path), lineage_root=self._lineage_root,
            generation=self._generation,
            sid=sid, runtime=self.runtime, kind=kind, summary=summary,
            body_ref=body_ref or f"{source_path}:{source_off}",
            source_path=source_path, source_off=source_off, integrity=integrity)


class _SubagentTail(ClaudeWalAdapter):
    """One subagent transcript into wal_subagent_events: the parent adapter's own line parser, a
    sidecar sink. Each row carries agent_id (file name), parent_agent_id + depth + agent_type (the
    .meta.json Claude writes beside it; depth 1 names no parent: its parent is the seat's sid).
    Same whole-line + per-line-transaction discipline as the main tail.

    Accepted limits (codex r2), the same as the main tail's: Claude appends to these files and never
    rewrites them, so continuity is (dev, ino, size >= cursor) only; one line is read whole however
    long; the subagents dir is listed + stat'ed each pass (0.7 ms on 45 files). A file captured with
    no meta (older than SUBAGENT_META_WAIT_S) keeps null parent/depth in its immutable rows; its
    agent_id joins the .meta.json later if one appears."""

    def __init__(self, parent, path):
        super().__init__(parent._store, parent._lineage_root, parent._generation)
        self._path = path
        self._first_seen = parent.__dict__.setdefault("_sub_first_seen", {})
        self._agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        meta = None
        try:
            with open(path[:-len(".jsonl")] + ".meta.json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        self._meta_missing = not isinstance(meta, dict)
        meta = meta if isinstance(meta, dict) else {}
        depth = meta.get("spawnDepth") if isinstance(meta, dict) else None
        self._meta = {"parent_agent_id": meta.get("parentAgentId") if isinstance(meta, dict) else None,
                      "depth": depth if isinstance(depth, int) else None,
                      "agent_type": meta.get("agentType") if isinstance(meta, dict) else None}

    def run(self, st):
        """-> (events appended, consumed through the end of the file's complete lines)."""
        import time
        cur = self._store.get_subagent_cursor(self._path)
        ident = (st.st_dev, st.st_ino)
        start = cur["last_off"] if cur else 0
        if cur and ((cur.get("source_dev"), cur.get("source_ino")) not in ((None, None), ident)
                    or st.st_size < start):
            start = 0                      # replaced or truncated: a new stream (codex r1)
        if not cur and self._meta_missing:
            # wait for parent/depth before the first immutable row, timed from when THIS process
            # first saw this file (an active file's mtime keeps moving; codex r2), keyed by identity
            # so a reused path starts over (codex r3). min(mtime, now) on purpose: a file already
            # quiet past the wait when first seen (a restart, the first backfill) is captured now.
            first = self._first_seen.setdefault((self._path, st.st_dev, st.st_ino),
                                                min(st.st_mtime, time.time()))
            if time.time() - first < SUBAGENT_META_WAIT_S:
                return 0, False
        if st.st_size <= start:
            return 0, True
        with open(self._path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(SUBAGENT_READ_MAX)
            if b"\n" not in chunk and len(chunk) == SUBAGENT_READ_MAX:
                chunk += fh.readline()     # one line longer than the cap: finish that line
        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return 0, True                 # only a half-written line: nothing complete yet
        appended, offset = 0, start
        prev_sid = [None]                  # same sessionId as the seat: no rotate markers
        for raw in chunk[:last_nl + 1].splitlines(keepends=True):
            line_start = offset
            offset += len(raw)
            text = raw.rstrip(b"\n").rstrip(b"\r")
            with self._store.transaction():   # the line's events AND the cursor: one commit
                if text.strip():
                    appended += self._emit_line(text, self._path, line_start, prev_sid)
                self._store.set_subagent_cursor(self._path, self._lineage_root, self._agent_id,
                                                offset, st.st_dev, st.st_ino)
        return appended, start + len(chunk) >= st.st_size or offset >= st.st_size

    def _emit_line(self, text_bytes, source_path, line_start, prev_sid):
        try:
            obj = json.loads(text_bytes)
        except ValueError:
            obj = None
        if not isinstance(obj, dict):      # [] or a scalar parses but is no record (codex r1)
            self._append("marker", "unparseable-line" if obj is None else "non-object-line",
                         source_path, line_start, text_bytes, sid="?", ts=0.0)
            return 1
        return super()._emit_line(text_bytes, source_path, line_start, prev_sid)

    def _append(self, kind, summary, source_path, source_off, raw_bytes, *,
                sid, ts, integrity=None, body_ref=None):
        if integrity is None and raw_bytes is not None:
            integrity = hashlib.sha256(raw_bytes).hexdigest()
        self._store.append_subagent(
            ts=self._dated(ts, source_path), lineage_root=self._lineage_root, generation=self._generation, sid=sid,
            agent_id=self._agent_id, runtime=self.runtime, kind=kind, summary=summary,
            body_ref=body_ref or f"{source_path}:{source_off}", source_path=source_path,
            source_off=source_off, integrity=integrity, **self._meta)
