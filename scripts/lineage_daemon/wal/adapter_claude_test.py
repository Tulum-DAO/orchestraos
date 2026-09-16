"""RED-first tests for the claude WAL adapter (spec §2.3 adapter_claude).

Grounded on the REAL event shapes observed live on ios-watch-dev's
757ef800-*.jsonl (2026-09-02): line-delimited JSON, types assistant
(content[].thinking/text/tool_use + message.usage), user
(content[].tool_result/text), system, file-history-snapshot, queue-operation;
every line carries `timestamp` + `sessionId`.

Core invariants:
  - offset-cursor incremental: a second tail re-reads NO already-consumed bytes.
  - a partial trailing line (the kill-9 case) is NOT consumed until newline-complete.
  - integrity = sha256 of the exact source line (gap/tamper detection).
  - model-voice (text/thinking/tool_call) summaries stay STRUCTURAL (no content
    snippet) — bodies remain in source via body_ref.
"""
import hashlib
import json

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.adapter_claude import ClaudeWalAdapter


def _line(**o):
    o.setdefault("sessionId", "757ef800")
    o.setdefault("timestamp", "2026-09-02T03:00:00.000Z")
    return json.dumps(o) + "\n"


def _assistant(content, usage=None):
    msg = {"role": "assistant", "content": content}
    if usage is not None:
        msg["usage"] = usage
    return _line(type="assistant", message=msg)


def _user(content):
    return _line(type="user", message={"role": "user", "content": content})


def _write(path, *lines):
    with open(path, "w") as fh:
        fh.write("".join(lines))


def _adapter(tmp_path):
    store = WalStore(str(tmp_path / "ios-watch-dev.db"))
    return store, ClaudeWalAdapter(store, lineage_root="ios-watch-dev", generation=6)


def test_maps_assistant_tool_use_to_tool_call(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src, _assistant([
        {"type": "tool_use", "id": "tu1", "name": "Bash",
         "input": {"command": "ls -la"}}]))
    ad.tail(src)
    evs = [r for r in store.events() if r["kind"] == "tool_call"]
    assert len(evs) == 1
    assert "Bash" in evs[0]["summary"]
    # structural: the raw command is NOT copied into the summary
    assert "ls -la" not in evs[0]["summary"]


def test_maps_user_tool_result_to_tool_result(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src, _user([
        {"type": "tool_result", "tool_use_id": "tu1", "content": "total 8"}]))
    ad.tail(src)
    kinds = [r["kind"] for r in store.events()]
    assert kinds == ["tool_result"]


def test_maps_user_text_to_prompt(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src, _user([{"type": "text", "text": "please build the thing"}]))
    ad.tail(src)
    kinds = [r["kind"] for r in store.events()]
    assert kinds == ["prompt"]


def test_maps_usage_to_ctx_with_jsonl_tokens(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src, _assistant(
        [{"type": "text", "text": "ok"}],
        usage={"input_tokens": 100, "output_tokens": 20,
               "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 900}))
    ad.tail(src)
    ctx = [r for r in store.events() if r["kind"] == "ctx"]
    assert len(ctx) == 1
    # jsonl_tokens = input + cache_read + cache_creation = 100 + 4000 + 900
    assert "5000" in ctx[0]["summary"]


def test_system_and_snapshot_to_marker(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src,
           _line(type="system", subtype="hook", sessionId="757ef800"),
           _line(type="file-history-snapshot", messageId="m1", sessionId="757ef800"))
    ad.tail(src)
    kinds = [r["kind"] for r in store.events()]
    assert kinds == ["marker", "marker"]


def test_offset_cursor_resume_no_reread(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src,
           _user([{"type": "text", "text": "one"}]),
           _assistant([{"type": "text", "text": "two"}]))
    n1 = ad.tail(src)
    assert n1 == 2
    # append a third complete line and tail again — only the new line is consumed
    with open(src, "a") as fh:
        fh.write(_assistant([{"type": "tool_use", "id": "t", "name": "Read",
                              "input": {}}]))
    n2 = ad.tail(src)
    assert n2 == 1
    seqs = [r["seq"] for r in store.events()]
    assert seqs == [1, 2, 3]  # total order continued, no duplication


def test_partial_trailing_line_not_consumed(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    complete = _user([{"type": "text", "text": "done"}])
    partial = '{"type": "assistant", "message": {"role":"assis'  # killed mid-write
    with open(src, "w") as fh:
        fh.write(complete + partial)
    n1 = ad.tail(src)
    assert n1 == 1  # only the complete line
    # now the writer "finishes" the line + newline; tail picks it up cleanly
    with open(src, "w") as fh:
        fh.write(complete + _assistant([{"type": "text", "text": "resumed"}]))
    n2 = ad.tail(src)
    assert n2 == 1
    assert store.max_seq() == 2


def test_integrity_is_sha256_of_source_line(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    line = _user([{"type": "text", "text": "x"}])
    _write(src, line)
    ad.tail(src)
    ev = store.events()[0]
    assert ev["integrity"] == hashlib.sha256(line.rstrip("\n").encode()).hexdigest()


def test_sid_change_emits_marker_rotate(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src,
           _line(type="user", message={"role": "user", "content": "a"},
                 sessionId="sidA"),
           _line(type="user", message={"role": "user", "content": "b"},
                 sessionId="sidB"))
    ad.tail(src)
    rotates = [r for r in store.events()
               if r["kind"] == "marker" and "rotate" in (r["summary"] or "")]
    assert len(rotates) == 1


def test_multi_item_line_shares_source_off_preserves_order(tmp_path):
    store, ad = _adapter(tmp_path)
    src = str(tmp_path / "s.jsonl")
    _write(src, _assistant(
        [{"type": "thinking", "thinking": "hmm"},
         {"type": "text", "text": "answer"},
         {"type": "tool_use", "id": "t", "name": "Grep", "input": {}}],
        usage={"input_tokens": 10, "output_tokens": 5,
               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}))
    ad.tail(src)
    evs = store.events()
    kinds = [r["kind"] for r in evs]
    # order preserved: thinking+text -> response, tool_use -> tool_call, usage -> ctx
    assert kinds == ["response", "response", "tool_call", "ctx"]
    assert len({r["source_off"] for r in evs}) == 1  # all from one source line
