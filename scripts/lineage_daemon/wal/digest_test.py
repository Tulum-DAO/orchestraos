"""RED-first tests for the deterministic `wal digest` renderer (spec §3.3, DP-1).

NO LLM anywhere. The load-bearing safety property is the CLASS defense:
model-voice (assistant response/thinking) NEVER appears verbatim in ANY digest
layer; tool_calls render as STRUCTURED rows; verbatim body drill-down is allowed
ONLY for world-output (tool_result/file_mod) and passes the scrub boundary.

These tests use SYNTHETIC markers I control (not a real court sample) to prove
the structural rules. The REAL court-fixture zero-signature proof is a separate
test that lands when gm provides the captured sample.
"""
from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.digest import render_digest


def _store(tmp_path):
    return WalStore(str(tmp_path / "l.db"))


def _ev(store, kind, summary, body_ref, sid="sid", gen=6):
    return store.append(ts=0.0, lineage_root="ios-watch-dev", generation=gen,
                        sid=sid, runtime="claude", kind=kind, summary=summary,
                        body_ref=body_ref, source_path="p.jsonl",
                        source_off=0, integrity="h")


def test_model_voice_response_never_verbatim(tmp_path):
    store = _store(tmp_path)
    _ev(store, "response", "text (18 chars)", "p.jsonl:0#c0")
    bodies = {"p.jsonl:0#c0": "SECRET_MODEL_VOICE"}
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: bodies.get(ref, ""))
    # even though the body is resolvable, model-voice is NEVER put in the digest
    assert "SECRET_MODEL_VOICE" not in d["text"]
    # it still appears STRUCTURALLY (seq/kind), so Green knows it exists
    assert "response" in d["text"]


def test_thinking_never_verbatim(tmp_path):
    store = _store(tmp_path)
    _ev(store, "response", "thinking", "p.jsonl:0#c0")
    bodies = {"p.jsonl:0#c0": "PRIVATE_CHAIN_OF_THOUGHT"}
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: bodies.get(ref, ""))
    assert "PRIVATE_CHAIN_OF_THOUGHT" not in d["text"]


def test_tool_call_is_structured_row_not_raw_args(tmp_path):
    store = _store(tmp_path)
    _ev(store, "tool_call", "Bash", "p.jsonl:1#c0")
    bodies = {"p.jsonl:1#c0": '{"command":"RAW_ARGS_BLOB_XYZ"}'}
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: bodies.get(ref, ""))
    assert "Bash" in d["text"]                    # name surfaced (structured)
    assert "RAW_ARGS_BLOB_XYZ" not in d["text"]   # raw args NOT verbatim


def test_world_output_surfaces_when_clean(tmp_path):
    store = _store(tmp_path)
    _ev(store, "tool_result", "result (12 chars)", "p.jsonl:2#c0")
    bodies = {"p.jsonl:2#c0": "BUILD SUCCEEDED 42 tests"}
    # world-output (tool_result) with NO signature -> surfaced verbatim
    def scrub(text):
        return text, ("SIGNATURE" in text)  # trips only on the signature marker
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: bodies.get(ref, ""), scrub=scrub)
    assert "BUILD SUCCEEDED 42 tests" in d["text"]
    assert d["contaminated"] is False


def test_contaminated_world_output_span_hard_excluded(tmp_path):
    store = _store(tmp_path)
    _ev(store, "tool_result", "result", "p.jsonl:2#c0")  # seq 1
    bodies = {"p.jsonl:2#c0": "leading SIGNATURE trailing"}
    def scrub(text):
        return text, ("SIGNATURE" in text)  # detected -> hard-exclude the span
    d = render_digest(store, "ios-watch-dev",
                      resolve_body=lambda ref: bodies.get(ref, ""), scrub=scrub)
    assert "SIGNATURE" not in d["text"]        # zero signature occurrences
    assert "leading" not in d["text"]          # whole span excluded, not cleaned
    assert d["contaminated"] is True
    assert 1 in d["dropped_spans"]


def test_working_set_from_file_mod_and_git(tmp_path):
    store = _store(tmp_path)
    _ev(store, "file_mod", "M src/App.swift", "git:/cwd#src/App.swift")
    _ev(store, "git", "HEAD abc123def456 main", "git:/cwd@abc123")
    d = render_digest(store, "ios-watch-dev")
    assert "src/App.swift" in d["text"]
    assert "abc123def456" in d["text"]


def test_deterministic_same_input_same_bytes(tmp_path):
    store = _store(tmp_path)
    _ev(store, "prompt", "text (5 chars)", "p.jsonl:0#c0")
    _ev(store, "tool_call", "Read", "p.jsonl:1#c0")
    a = render_digest(store, "ios-watch-dev")["text"]
    b = render_digest(store, "ios-watch-dev")["text"]
    assert a == b  # no LLM, no randomness


def test_since_seq_incremental(tmp_path):
    store = _store(tmp_path)
    _ev(store, "tool_call", "Read", "p.jsonl:0#c0")     # seq 1
    _ev(store, "tool_call", "Grep", "p.jsonl:1#c0")     # seq 2
    _ev(store, "tool_call", "Edit", "p.jsonl:2#c0")     # seq 3
    d = render_digest(store, "ios-watch-dev", since_seq=2)
    assert "Edit" in d["text"]
    assert "Read" not in d["text"] and "Grep" not in d["text"]
