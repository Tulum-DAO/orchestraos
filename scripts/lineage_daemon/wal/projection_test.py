"""Build C — wal -> TranscriptEnvelope projection unit + court-boundary REDs.

RED-first. The projection is the thin, DETERMINISTIC, READ-ONLY map from
seq-ordered WAL rows to the grammar-v2 TranscriptEnvelope iOS/web renders. The
SAFETY centerpiece: model voice (response/thinking, tool_call args) is rendered
THROUGH the stage-2 sanitizer (normalize.render_body) — a flagged lineage
block-renders (structural summary, ZERO verbatim model voice). The A0 court REDs
are exercised at the projection boundary here.
"""
import json
import os
import tempfile

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal.projection import project_envelope, GRAMMAR_VERSION

HERE = os.path.dirname(__file__)
FIXDIR = os.path.join(HERE, "..", "..", "..", "contract", "transcript", "fixtures")
SENTINEL = "SYNTHETIC-COURT-SENTINEL-DO-NOT-INGEST"


def _store(tmp):
    return WalStore(os.path.join(tmp, "l.db"))


def _append(store, kind, summary, body_ref, *, runtime="claude", lineage="L",
            sid="s1", gen=1, ts=0.0):
    return store.append(ts=ts, lineage_root=lineage, generation=gen, sid=sid,
                        runtime=runtime, kind=kind, summary=summary,
                        body_ref=body_ref, source_path="src.jsonl", source_off=0)


def _project(store, bodies, **kw):
    return project_envelope(
        store, "L", agent_id="a1", session_id="sess1",
        resolve_body=lambda ref: bodies.get(ref), **kw)


# ---- envelope frame -------------------------------------------------------

def test_envelope_frame_is_grammar_v2():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "prompt", "text (5 chars)", "b_p")
        env = _project(st, {"b_p": "hi you"})
        assert env["grammar_version"] == GRAMMAR_VERSION == 2
        assert env["agent_id"] == "a1"
        assert env["session_id"] == "sess1"
        assert isinstance(env["items"], list)
        assert isinstance(env["render_items"], list)


# ---- per-kind mapping -----------------------------------------------------

def test_prompt_maps_to_user_text_bubble():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "prompt", "text (9 chars)", "b_p")
        env = _project(st, {"b_p": "ship it!"})
        item = env["items"][0]
        assert item["kind"] == "text" and item["role"] == "user"
        assert item["text"] == "ship it!"
        r = env["render_items"][0]
        assert r["kind"] == "user" and r["text"] == "ship it!"
        assert "key" in r


def test_response_text_maps_to_assistant():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text (12 chars)", "b_r")
        env = _project(st, {"b_r": "On it, the operator."})
        assert env["items"][0] == {
            "kind": "text", "role": "assistant", "text": "On it, the operator."}
        assert env["render_items"][0]["kind"] == "assistant"
        assert env["render_items"][0]["text"] == "On it, the operator."


def test_response_thinking_maps_to_thinking():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "thinking", "b_t")
        env = _project(st, {"b_t": "I should list the dir first."})
        assert env["items"][0]["kind"] == "thinking"
        assert env["items"][0]["role"] == "assistant"
        assert env["items"][0]["text"] == "I should list the dir first."
        assert env["render_items"][0]["kind"] == "thinking"


def test_codex_thinking_summary_variant_maps_to_thinking():
    # codex adapter emits summary "thinking (N chars)" (not bare "thinking")
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "thinking (40 chars)", "b_t", runtime="codex")
        env = _project(st, {"b_t": "reasoning trace"})
        assert env["items"][0]["kind"] == "thinking"


def test_tool_call_and_result_pair_in_render_items():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "tool_call", "Bash", "b_tc")
        _append(st, "tool_result", "result (7 chars)", "b_tr")
        env = _project(st, {
            "b_tc": json.dumps({"type": "tool_use", "name": "Bash",
                                "input": {"command": "ls -la"}}),
            "b_tr": "total 0",
        })
        # flat items: tool_use + tool_result separate
        kinds = [i["kind"] for i in env["items"]]
        assert kinds == ["tool_use", "tool_result"]
        tu = env["items"][0]
        assert tu["tool"] == "Bash"
        assert tu["input"]["command"] == "ls -la"
        assert isinstance(tu["summary"], str) and len(tu["summary"]) <= 120
        tr = env["items"][1]
        assert tr["kind"] == "tool_result" and tr["role"] == "user"
        assert tr["text"] == "total 0"
        # render_items: result paired UNDER the tool node
        rt = [r for r in env["render_items"] if r["kind"] == "tool"]
        assert len(rt) == 1
        assert rt[0]["tool"] == "Bash"
        assert rt[0]["result"] == "total 0"


def test_side_effect_kinds_are_omitted():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        for k in ("file_mod", "git", "proc", "ctx", "marker"):
            _append(st, k, f"{k} summary", f"b_{k}")
        _append(st, "response", "text (2 chars)", "b_r")
        env = _project(st, {"b_r": "ok"})
        # only the response survived; no fabricated grammar items
        assert [i["kind"] for i in env["items"]] == ["text"]
        assert [r["kind"] for r in env["render_items"]] == ["assistant"]


# ---- is_system spawn brief ------------------------------------------------

def test_is_system_flag_on_first_you_are_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "prompt", "text", "b_sys")
        _append(st, "prompt", "text", "b_p2")
        env = _project(st, {"b_sys": "You are agent-x, a T2 agent.",
                            "b_p2": "next message"})
        assert env["items"][0].get("is_system") is True
        assert env["render_items"][0].get("is_system") is True
        # only the FIRST user text is the spawn brief
        assert "is_system" not in env["items"][1] or \
            env["items"][1].get("is_system") is not True


# ---- determinism / read-only ----------------------------------------------

def test_projection_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "prompt", "text", "b_p")
        _append(st, "response", "text", "b_r")
        _append(st, "tool_call", "Read", "b_tc")
        bodies = {"b_p": "hi", "b_r": "hello",
                  "b_tc": json.dumps({"name": "Read", "input": {"file_path": "/x"}})}
        a = _project(st, bodies)
        b = _project(st, bodies)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_projection_does_not_write_the_store():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text", "b_r")
        before = st.max_seq()
        _project(st, {"b_r": "hello"})
        assert st.max_seq() == before  # read-only: no rows appended


def test_limit_slices_to_last_n():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        for i in range(5):
            _append(st, "response", "text", f"b_{i}")
        bodies = {f"b_{i}": f"msg{i}" for i in range(5)}
        env = _project(st, bodies, limit=2)
        assert [i["text"] for i in env["items"]] == ["msg3", "msg4"]


# ---- COURT BOUNDARY REDs (the safety centerpiece) -------------------------

def _a0_court_body(provider, field):
    red = json.load(open(os.path.join(FIXDIR, provider, "a0-court-red.json")))
    return red["input"][field]


def test_flagged_lineage_blocks_model_voice_zero_verbatim_leak():
    """A0 court RED at the projection boundary: a FLAGGED lineage MUST NOT emit
    verbatim model voice. Reuses the claude A0 synthetic sentinel body."""
    body = _a0_court_body("claude", "assistant_text_block")
    assert SENTINEL in body  # precondition: the fixture carries the sentinel
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text (99 chars)", "b_r")
        env = _project(st, {"b_r": body}, lineage_flagged=True)
        blob = json.dumps(env)
        assert SENTINEL not in blob, "flagged lineage leaked verbatim model voice"
        # the item still renders — as a STRUCTURAL summary, never the body
        assert env["items"][0]["kind"] == "text"
        assert body not in env["items"][0]["text"]


def test_flagged_lineage_blocks_codex_serialization():
    body = _a0_court_body("codex", "response_item_text")
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text (80 chars)", "b_r", runtime="codex")
        env = _project(st, {"b_r": body}, lineage_flagged=True)
        assert SENTINEL not in json.dumps(env)


def test_flagged_lineage_blocks_tool_call_args():
    """tool_call args are MODEL VOICE (class defense) -> block under flagged."""
    body = _a0_court_body("claude", "assistant_text_block")
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "tool_call", "Bash", "b_tc")
        env = _project(st, {"b_tc": json.dumps(
            {"name": "Bash", "input": {"command": body}})}, lineage_flagged=True)
        assert SENTINEL not in json.dumps(env)
        # no raw args survive the block
        assert env["items"][0]["input"] == {}


def test_fail_closed_on_unreadable_flag():
    body = _a0_court_body("claude", "assistant_text_block")
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text", "b_r")
        env = _project(st, {"b_r": body}, lineage_flagged=False,
                       flag_readable=False)
        assert SENTINEL not in json.dumps(env)  # fail-closed -> block


def test_clean_lineage_renders_model_voice_verbatim():
    """Control: a CLEAN, non-court lineage streams model voice verbatim (the
    boundary blocks the flagged case, never the clean fleet)."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        _append(st, "response", "text (12 chars)", "b_r")
        env = _project(st, {"b_r": "On it, the operator."}, lineage_flagged=False)
        assert env["items"][0]["text"] == "On it, the operator."
