import json
import os
import tempfile

from scripts.lineage_daemon.enrich import (
    live_sid, resolve_model, jsonl_context_tokens, enrich_statuses, _project_dir,
)


# --- live_sid: resume_command sid beats the clobber-prone session_id ---

def test_live_sid_prefers_resume_command():
    entry = {"session_id": "6dcadbe3-3739-4277-9abf-d0dfa457048f",
             "resume_command": "/usr/bin/claude --resume "
                               "24531952-6254-416a-a609-7eb9cbd2cc60 --model 'x'"}
    assert live_sid(entry) == "24531952-6254-416a-a609-7eb9cbd2cc60"

def test_live_sid_falls_back_to_session_id():
    entry = {"session_id": "c19f0009-e2ab-45e9-9e77-ee30d4c4274a",
             "resume_command": ""}
    assert live_sid(entry) == "c19f0009-e2ab-45e9-9e77-ee30d4c4274a"

def test_live_sid_empty_when_neither():
    assert live_sid({}) == ""


# --- resolve_model: resume --model beats registry beats meta ---

def test_resolve_model_from_resume_command():
    entry = {"resume_command": "claude --resume abc --model 'claude-opus-4-8[1m]' --x"}
    assert resolve_model(entry, {"model": "other"}) == "claude-opus-4-8[1m]"

def test_resolve_model_from_registry_when_no_resume():
    assert resolve_model({}, {"model": "claude-opus-4-8[1m]"}) == "claude-opus-4-8[1m]"

def test_resolve_model_from_meta_last():
    assert resolve_model({"model": "claude-sonnet"}, {}) == "claude-sonnet"


# --- _project_dir encoding ---

def test_project_dir_encoding():
    assert _project_dir("/home/testuser/agent-orchestra") == \
        "-home-testuser-agent-orchestra"


# --- jsonl_context_tokens: last usage record, injected root ---

def _write_jsonl(root, projdir, sid, usages):
    d = os.path.join(root, projdir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, sid + ".jsonl"), "w") as fh:
        for u in usages:
            fh.write(json.dumps({"message": {"usage": u}}) + "\n")


def test_jsonl_context_tokens_sums_last_usage():
    with tempfile.TemporaryDirectory() as root:
        cwd = "/home/testuser/agent-orchestra"
        _write_jsonl(root, _project_dir(cwd), "sid1", [
            {"input_tokens": 1, "cache_read_input_tokens": 100},
            {"input_tokens": 2, "cache_read_input_tokens": 688664,
             "cache_creation_input_tokens": 1668},   # LAST record wins
        ])
        assert jsonl_context_tokens("sid1", cwd, project_root=root) == 690334

def test_jsonl_context_tokens_missing_file_is_none():
    with tempfile.TemporaryDirectory() as root:
        assert jsonl_context_tokens("nope", "/x", project_root=root) is None

def test_jsonl_context_tokens_no_sid_is_none():
    assert jsonl_context_tokens("", "/x") is None

def test_jsonl_context_tokens_skips_nonusage_lines():
    with tempfile.TemporaryDirectory() as root:
        cwd = "/c"
        d = os.path.join(root, _project_dir(cwd))
        os.makedirs(d)
        with open(os.path.join(d, "s.jsonl"), "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"type": "user"}) + "\n")   # no usage
            fh.write(json.dumps({"message": {"usage": {"input_tokens": 5}}}) + "\n")
        assert jsonl_context_tokens("s", cwd, project_root=root) == 5


# --- enrich_statuses: read-only, only_when_empty, injected seams ---

def _fake_capture(mapping):
    return lambda session: mapping.get(session, "")

def test_enrich_only_when_empty_skips_populated():
    statuses = [{"session": "gm", "context_pct": "29%"}]
    out = enrich_statuses(statuses, {}, {}, capture_fn=_fake_capture({}))
    # already had a pct -> not enriched (no extra keys added)
    assert "pane_status_line" not in out[0]

def test_enrich_populates_pane_and_jsonl_for_empty():
    with tempfile.TemporaryDirectory() as root:
        cwd = "/home/testuser/agent-orchestra"
        sid = "24531952-6254-416a-a609-7eb9cbd2cc60"
        _write_jsonl(root, _project_dir(cwd), sid,
                     [{"input_tokens": 2, "cache_read_input_tokens": 690332}])
        meta = {"ob": {"cwd": cwd,
                       "resume_command": f"claude --resume {sid} --model "
                                         "'claude-opus-4-8[1m]'"}}
        statuses = [{"session": "ob", "context_pct": ""}]
        bar = "agent-orchestra ████████░░ 86%"
        out = enrich_statuses(statuses, meta, {}, capture_fn=_fake_capture({"ob": bar}),
                              project_root=root)
        assert out[0]["pane_status_line"] == bar
        assert out[0]["jsonl_tokens"] == 690334
        assert out[0]["resolved_model"] == "claude-opus-4-8[1m]"

def test_enrich_does_not_mutate_input():
    statuses = [{"session": "ob", "context_pct": ""}]
    enrich_statuses(statuses, {}, {}, capture_fn=_fake_capture({"ob": "x █ 5%"}))
    assert "pane_status_line" not in statuses[0]   # original dict untouched


# --- Gemini / Antigravity tests ---

def test_live_sid_prefers_conversation_command():
    entry = {"session_id": "stale-session-id",
             "resume_command": "agy --conversation 139e5f3e-b919-441a-976f-7a470428735c --dangerously-skip-permissions"}
    assert live_sid(entry) == "139e5f3e-b919-441a-976f-7a470428735c"


def test_resolve_model_defaults_gemini():
    assert resolve_model({"runtime": "gemini"}, {}) == "gemini-3.7-flash"
    assert resolve_model({}, {"runtime": "gemini"}) == "gemini-3.7-flash"


def test_jsonl_context_tokens_reads_gemini_brain_transcript():
    with tempfile.TemporaryDirectory() as b_root:
        sid = "139e5f3e-b919-441a-976f-7a470428735c"
        log_dir = os.path.join(b_root, sid, ".system_generated", "logs")
        os.makedirs(log_dir, exist_ok=True)
        trans_file = os.path.join(log_dir, "transcript.jsonl")
        # Write 4 KB of transcript content
        with open(trans_file, "w") as fh:
            fh.write("x" * 4096)
        # 4096 bytes = 4.0 KB -> ~1000 tokens (4.0 * 250)
        tokens = jsonl_context_tokens(sid, brain_root=b_root)
        assert tokens == 1000


def test_enrich_populates_gemini_brain_tokens():
    with tempfile.TemporaryDirectory() as b_root:
        sid = "139e5f3e-b919-441a-976f-7a470428735c"
        log_dir = os.path.join(b_root, sid, ".system_generated", "logs")
        os.makedirs(log_dir, exist_ok=True)
        trans_file = os.path.join(log_dir, "transcript.jsonl")
        with open(trans_file, "w") as fh:
            fh.write("y" * 8192)
        meta = {"gemini-dev": {
            "resume_command": f"agy --conversation {sid} --dangerously-skip-permissions",
            "runtime": "gemini"
        }}
        statuses = [{"session": "gemini-dev", "context_pct": ""}]
        out = enrich_statuses(statuses, meta, {}, capture_fn=_fake_capture({"gemini-dev": "fake █ 86%"}),
                              brain_root=b_root)
        assert out[0]["jsonl_tokens"] == 2000
        assert out[0]["resolved_model"] == "gemini-3.7-flash"
        # Gemini does not use Claude █ status bar; pane_status_line stays empty
        assert out[0]["pane_status_line"] == ""


# --- Codex runtime tests ---

def test_enrich_codex_ignores_pane_status_bar_and_queries_ctx():
    fake_bar = "git diff ████████░░ 86%"
    meta = {"codex-dev-1": {
        "runtime": "codex",
        "model": "gpt-5.6-terra"
    }}
    statuses = [{"session": "codex-dev-1", "context_pct": ""}]
    out = enrich_statuses(
        statuses, meta, {},
        capture_fn=_fake_capture({"codex-dev-1": fake_bar}),
        codex_ctx_fn=lambda s: (27.1, 70000, 258000)
    )
    # pane_status_line is suppressed so git diff 86% is not treated as context bar
    assert out[0]["pane_status_line"] == ""
    assert out[0]["context_pct"] == "27%"
    assert out[0]["jsonl_tokens"] == 70000
    assert out[0]["resolved_model"] == "gpt-5.6-terra"


def test_jsonl_context_tokens_reads_codex_rollout_jsonl():
    with tempfile.TemporaryDirectory() as c_root:
        sid = "rollout-20260828-test1234"
        sess_dir = os.path.join(c_root, "2026", "08", "28")
        os.makedirs(sess_dir, exist_ok=True)
        rollout_file = os.path.join(sess_dir, f"{sid}.jsonl")
        with open(rollout_file, "w") as fh:
            fh.write(json.dumps({"type": "event_msg", "payload": {
                "type": "token_count",
                "info": {"last_token_usage": {"total_tokens": 42000}}
            }}) + "\n")
        tokens = jsonl_context_tokens(sid, codex_root=c_root)
        assert tokens == 42000

