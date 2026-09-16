#!/usr/bin/env python3
"""Build C conformance — the wal->TranscriptEnvelope projection output VALIDATES
against contract/transcript/transcript.v2.schema.json (grammar v2), across the
CROSS-RUNTIME matrix (claude + codex + gemini — a claude-only proof is NOT
acceptable), and the A0 court REDs block-render in each provider's serialization
with ZERO verbatim leak while STILL producing a schema-valid envelope.

This is the Python sibling of conformance.test.ts: same shared contract (the
schema is the single source of truth the iOS drift-guard is generated from), a
different producer (the durable WAL lane vs the raw-jsonl server normalizer).

Run: python3 -m pytest contract/transcript/tests/test_projection_conformance.py
"""
import json
import os
import sys
import tempfile

import jsonschema

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))

from lineage_daemon.wal.store import WalStore            # noqa: E402
from lineage_daemon.wal.projection import project_envelope, GRAMMAR_VERSION  # noqa: E402

SCHEMA = json.load(open(os.path.join(HERE, "..", "transcript.v2.schema.json")))
FIXDIR = os.path.join(HERE, "..", "fixtures")
SENTINEL = "SYNTHETIC-COURT-SENTINEL-DO-NOT-INGEST"


def _validate(env):
    jsonschema.validate(instance=env, schema=SCHEMA)


def _pb_str(s):
    """Minimal protobuf-wire encoding of {field 1: string} so normalize's gemini
    protobuf-decode path recovers `s` (mirrors the real antigravity body shape)."""
    b = s.encode("utf-8")
    assert len(b) < 128
    return bytes([0x0A, len(b)]) + b


def _store(tmp, name):
    return WalStore(os.path.join(tmp, f"{name}.db"))


def _append(st, runtime, kind, summary, body_ref, lineage):
    st.append(ts=0.0, lineage_root=lineage, generation=1, sid="s",
              runtime=runtime, kind=kind, summary=summary, body_ref=body_ref,
              source_path="src", source_off=0)


# --------------------------------------------------------------------------
# per-provider realistic lineages
# --------------------------------------------------------------------------

def _build_claude(st):
    lin = "claude-L"
    _append(st, "claude", "prompt", "text", "c_sys", lin)
    _append(st, "claude", "response", "thinking", "c_think", lin)
    _append(st, "claude", "response", "text (9 chars)", "c_txt", lin)
    _append(st, "claude", "tool_call", "Bash", "c_tc", lin)
    _append(st, "claude", "tool_result", "result (7 chars)", "c_tr", lin)
    _append(st, "claude", "ctx", "jsonl_tokens=5", "c_ctx", lin)      # omitted
    _append(st, "claude", "marker", "system:init", "c_mk", lin)       # omitted
    bodies = {
        "c_sys": "You are claude-dev, a T2 agent.",
        "c_think": "I should list the directory first.",
        "c_txt": "On it now",
        "c_tc": json.dumps({"type": "tool_use", "name": "Bash",
                            "input": {"command": "ls -la\necho done"}}),
        "c_tr": "total 0",
    }
    return lin, bodies


def _build_codex(st):
    lin = "codex-L"
    _append(st, "codex", "marker", "session_meta", "x_meta", lin)     # omitted
    _append(st, "codex", "prompt", "text (5 chars)", "x_p", lin)
    _append(st, "codex", "response", "thinking (30 chars)", "x_think", lin)
    _append(st, "codex", "tool_call", "exec_command", "x_tc", lin)
    _append(st, "codex", "tool_result", "result (4 chars)", "x_tr", lin)
    _append(st, "codex", "ctx", "total_tokens=42", "x_ctx", lin)      # omitted
    bodies = {
        "x_p": "hi",
        "x_think": "reasoning about the request",
        "x_tc": json.dumps({"type": "function_call", "name": "exec_command",
                            "arguments": "{}", "call_id": "c1"}),
        "x_tr": "done",
    }
    return lin, bodies


def _build_gemini(st):
    lin = "gemini-L"
    _append(st, "gemini", "prompt", "text (4 chars)", "g_p", lin)
    _append(st, "gemini", "response", "text (17 chars)", "g_txt", lin)
    _append(st, "gemini", "tool_result", "result (3 chars)", "g_tr", lin)
    bodies = {
        "g_p": _pb_str("hola"),
        "g_txt": _pb_str("hello from gemini"),
        "g_tr": _pb_str("ok done"),   # gemini world output is protobuf too
    }
    return lin, bodies


_BUILDERS = {"claude": _build_claude, "codex": _build_codex, "gemini": _build_gemini}


# --------------------------------------------------------------------------
# conformance
# --------------------------------------------------------------------------

def test_grammar_version_is_2():
    assert GRAMMAR_VERSION == 2


def test_each_provider_projects_a_schema_valid_v2_envelope():
    for provider, build in _BUILDERS.items():
        with tempfile.TemporaryDirectory() as tmp:
            st = _store(tmp, provider)
            lin, bodies = build(st)
            env = project_envelope(st, lin, agent_id=f"{provider}-a",
                                   session_id="sess",
                                   resolve_body=lambda ref, b=bodies: b.get(ref))
            _validate(env)
            assert env["grammar_version"] == 2
            # side-effect kinds omitted (no fabricated grammar items)
            for it in env["items"]:
                assert it["kind"] in ("text", "thinking", "tool_use", "tool_result")


def test_cross_runtime_matrix_claude_plus_noncalude():
    """THE GATE: prove out on >=1 claude + >=1 codex OR gemini (claude-only
    unacceptable). All three project to schema-valid envelopes."""
    covered = set()
    with tempfile.TemporaryDirectory() as tmp:
        for provider, build in _BUILDERS.items():
            st = _store(tmp, provider)
            lin, bodies = build(st)
            env = project_envelope(st, lin, agent_id="a", session_id="s",
                                   resolve_body=lambda ref, b=bodies: b.get(ref))
            _validate(env)
            assert len(env["items"]) > 0
            covered.add(provider)
    assert "claude" in covered
    assert covered - {"claude"}, "matrix requires a non-claude runtime"
    assert covered == {"claude", "codex", "gemini"}


def test_claude_envelope_pairs_tool_result_and_flags_spawn_brief():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp, "claude")
        lin, bodies = _build_claude(st)
        env = project_envelope(st, lin, agent_id="a", session_id="s",
                               resolve_body=lambda ref: bodies.get(ref))
        _validate(env)
        # spawn brief flagged on the first user text
        assert env["items"][0]["kind"] == "text" and env["items"][0]["role"] == "user"
        assert env["items"][0].get("is_system") is True
        # tool_result paired under the tool render node
        tool_nodes = [r for r in env["render_items"] if r["kind"] == "tool"]
        assert tool_nodes and tool_nodes[0]["result"] == "total 0"


# --------------------------------------------------------------------------
# A0 COURT REDs at the projection boundary (per provider, still schema-valid)
# --------------------------------------------------------------------------

_COURT_FIELD = {
    "claude": "assistant_text_block",
    "codex": "response_item_text",
    "gemini": "step_payload_decoded_text",
}


def _court_body(provider):
    red = json.load(open(os.path.join(FIXDIR, provider, "a0-court-red.json")))
    return red["input"][_COURT_FIELD[provider]]


def test_flagged_lineage_blocks_every_provider_zero_leak_still_valid():
    for provider in ("claude", "codex", "gemini"):
        body = _court_body(provider)
        assert SENTINEL in body  # the fixture actually carries the sentinel
        with tempfile.TemporaryDirectory() as tmp:
            st = _store(tmp, provider)
            lin = f"{provider}-flagged"
            _append(st, provider, "response", "text (80 chars)", "b_r", lin)
            _append(st, provider, "tool_call", "Bash", "b_tc", lin)
            env = project_envelope(
                st, lin, agent_id="a", session_id="s",
                resolve_body=lambda ref: {"b_r": body,
                                          "b_tc": json.dumps({"name": "Bash",
                                          "input": {"command": body}})}.get(ref),
                lineage_flagged=True)
            # RED: zero verbatim model voice, and the envelope is STILL valid v2
            assert SENTINEL not in json.dumps(env), f"{provider}: leaked model voice"
            _validate(env)
            assert env["items"][0]["kind"] == "text"       # blocked -> structural
            assert env["items"][1]["input"] == {}          # blocked tool args


def test_fail_closed_flag_unreadable_blocks_and_validates():
    body = _court_body("claude")
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp, "claude")
        _append(st, "claude", "response", "text", "b_r", "L")
        env = project_envelope(st, "L", agent_id="a", session_id="s",
                               resolve_body=lambda ref: body,
                               lineage_flagged=False, flag_readable=False)
        assert SENTINEL not in json.dumps(env)
        _validate(env)


def test_projection_is_deterministic_and_readonly():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp, "claude")
        lin, bodies = _build_claude(st)
        before = st.max_seq()
        a = project_envelope(st, lin, agent_id="a", session_id="s",
                             resolve_body=lambda ref: bodies.get(ref))
        b = project_envelope(st, lin, agent_id="a", session_id="s",
                             resolve_body=lambda ref: bodies.get(ref))
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
        assert st.max_seq() == before  # read-only
