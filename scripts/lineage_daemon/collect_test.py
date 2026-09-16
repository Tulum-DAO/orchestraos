from scripts.lineage_daemon.collect import (
    parse_pct, build_agent, collect_fleet,
    pane_pct, pane_exhausted, jsonl_fallback_pct, resolve_ctx_pct,
    beat_skip_reason,
)
from scripts.lineage_daemon.decide import decide

# Real captured status-bar lines (live fleet, 2026-08-12) that the shared
# agent-status parser returned context_pct='' for — the WS1 observe blocker.
_OB_BAR = "  ⬆ /gsd:update │ claude-opus-4-8[1m] │ agent-orchestra ████████░░ 86%           ✗ Auto-update failed"
_JARVIS_GM_BAR = "  ⬆ /gsd:update │ claude-opus-4-8 │ agent-orchestra 💀 ██████████ Clau                0% until auto-compact"
_OBV2_BAR = "  ⬆ /gsd:update │ claude-opus-4-8[1m] │ agent-orchestra ████░░░░░░ 43%           ✗ Auto-update failed"


# --- parse_pct: extract the integer immediately preceding a '%' ---

def test_parse_pct_plain():
    assert parse_pct("29%") == 29


def test_parse_pct_single_digit():
    assert parse_pct("8%") == 8


def test_parse_pct_empty_is_none():
    assert parse_pct("") is None


def test_parse_pct_none_is_none():
    assert parse_pct(None) is None


def test_parse_pct_prefixed():
    assert parse_pct("ctx:36%") == 36


# --- build_agent: shape a decide() input from a live status + reg entry ---

_GM_SAMPLE = {
    "session": "gm", "state": "idle",
    "activity": "Completed (ran for 53s)", "elapsed": "53s",
    "tokens": "", "tool": "", "model": "", "project": "",
    "context_pct": "29%",
    "process": {"pid": 3260758, "cpu": 2.4, "rss_mb": 503, "uptime": "04:44:06"},
    "is_noise": [], "confidence": "screen", "state_age_s": 46,
    "hook": {"event": "Stop", "tool": "", "age_s": 47},
}


def test_build_agent_from_gm_sample():
    reg_entry = {"tier": "T0", "model": "claude-opus-4-8[1m]"}
    a = build_agent(_GM_SAMPLE, reg_entry)
    assert a["agent_id"] == "gm"
    assert a["tier_class"] == "T0"
    assert a["ctx"]["status_bar_pct"] == 29
    assert a["ctx"]["jsonl_tokens"] is None
    assert a["ctx"]["model"] == "claude-opus-4-8[1m]"
    assert a["death"]["court"] is False
    assert a["death"]["session_alive"] is True
    assert a["death"]["jsonl_growing"] is True
    assert a["death"]["state"] == "idle"
    assert a["death"]["state_age_s"] == 46


def test_build_agent_no_reg_entry_defaults_t2():
    a = build_agent(_GM_SAMPLE, None)
    assert a["tier_class"] == "T2"
    # model falls back to status.model ("") when no reg entry
    assert a["ctx"]["model"] == ""


def test_build_agent_state_age_defaults_zero():
    status = dict(_GM_SAMPLE)
    status["state_age_s"] = None
    a = build_agent(status, None)
    assert a["death"]["state_age_s"] == 0


# --- collect_fleet: map the whole status list, attaching correct tiers ---

def test_collect_fleet_maps_and_attaches_tiers():
    status_list = [
        {"session": "gm", "state": "idle", "context_pct": "29%",
         "state_age_s": 46, "model": ""},
        {"session": "worker", "state": "working", "context_pct": "80%",
         "state_age_s": 5, "model": ""},
    ]
    registry = {"agents": {
        "gm": {"tier": "T0", "model": "claude-opus-4-8[1m]"},
        "worker": {"tier": "T2", "model": "claude-sonnet"},
    }}
    fleet = collect_fleet(status_list, registry)
    assert len(fleet) == 2
    assert fleet[0]["agent_id"] == "gm"
    assert fleet[0]["tier_class"] == "T0"
    assert fleet[0]["ctx"]["status_bar_pct"] == 29
    assert fleet[1]["agent_id"] == "worker"
    assert fleet[1]["tier_class"] == "T2"
    assert fleet[1]["ctx"]["status_bar_pct"] == 80


# --- WS1 observe fix: pane-bar % parsing (the '████ 86%' format) ---

def test_pane_pct_parses_ob_bar():
    assert pane_pct(_OB_BAR) == 86

def test_pane_pct_parses_obv2_bar():
    assert pane_pct(_OBV2_BAR) == 43

def test_pane_pct_empty_and_none():
    assert pane_pct("") is None
    assert pane_pct(None) is None

def test_pane_pct_no_percent_is_none():
    assert pane_pct("  ⬆ /gsd:update │ claude-opus-4-8 │ no meter here") is None


# --- pane exhaustion (skull / '0% until auto-compact') ---

def test_pane_exhausted_skull():
    assert pane_exhausted(_JARVIS_GM_BAR) is True

def test_pane_exhausted_auto_compact_zero_without_skull():
    assert pane_exhausted("agent-orchestra ██████████ 0% until auto-compact") is True

def test_pane_not_exhausted_normal_bar():
    assert pane_exhausted(_OB_BAR) is False
    assert pane_exhausted("") is False


# --- jsonl-token fallback pct (effective, auto-compact-aware ceiling) ---

def test_jsonl_fallback_pct_1m_matches_status_bar():
    # ob's real numbers: 690,334 tok on [1m] -> 86% (agrees with the bar)
    assert jsonl_fallback_pct(690334, "claude-opus-4-8[1m]") == 86

def test_jsonl_fallback_pct_none_tokens():
    assert jsonl_fallback_pct(None, "claude-opus-4-8[1m]") is None


# --- resolve_ctx_pct priority: agent-status -> pane -> jsonl ---

def test_resolve_ctx_prefers_agent_status():
    s = {"context_pct": "29%", "pane_status_line": _OB_BAR, "jsonl_tokens": 690334,
         "resolved_model": "claude-opus-4-8[1m]"}
    assert resolve_ctx_pct(s) == 29

def test_resolve_ctx_falls_back_to_pane_when_status_empty():
    s = {"context_pct": "", "pane_status_line": _OB_BAR}
    assert resolve_ctx_pct(s) == 86

def test_resolve_ctx_falls_back_to_jsonl_when_no_bar():
    s = {"context_pct": "", "pane_status_line": "", "jsonl_tokens": 690334,
         "resolved_model": "claude-opus-4-8[1m]"}
    assert resolve_ctx_pct(s) == 86


# --- build_agent + decide on the REAL blocked agents (end-to-end) ---

def test_ob_empty_status_bar_falls_back_to_soft_handoff():
    """orchestra-builder: agent-status context_pct='', pane bar 86% -> HARD (>=85%)."""
    status = {"session": "orchestra-builder", "state": "idle", "context_pct": "",
              "state_age_s": 2117, "pane_status_line": _OB_BAR,
              "jsonl_tokens": 690334, "resolved_model": "claude-opus-4-8[1m]"}
    a = build_agent(status, {"tier": "T1"})
    assert a["ctx"]["status_bar_pct"] == 86
    assert a["death"]["exhausted"] is False
    d = decide(a)
    assert d["action"] == "hard_rotate" and d["reason"] == "ctx:HARD"

def test_jarvis_gm_skull_falls_back_to_hard_rotate_death():
    """jarvis-gm: skull '0% until auto-compact' -> death:exhausted -> hard_rotate."""
    status = {"session": "jarvis-gm", "state": "idle", "context_pct": "",
              "state_age_s": 6011, "pane_status_line": _JARVIS_GM_BAR,
              "jsonl_tokens": 665989, "resolved_model": "claude-opus-4-8"}
    a = build_agent(status, {"tier": "T1"})
    assert a["death"]["exhausted"] is True
    d = decide(a)
    assert d["action"] == "hard_rotate"
    assert d["reason"] == "death:exhausted"
    assert d["needs_approval"] is True   # T1 kill is gated

def test_build_agent_exhausted_defaults_false_without_pane():
    a = build_agent(_GM_SAMPLE, {"tier": "T0"})
    assert a["death"]["exhausted"] is False


# --- Piece 2 (2b/2c): the "which seats does the beat evaluate at all" fields +
# the single-source skip predicate. build_agent must thread runtime + lineage
# status + succeeded_by so the beat can skip retired/quiescent/superseded rows
# and non-claude runtimes (codex/agy — no rotation adapter). ---

def test_build_agent_threads_runtime_status_succeeded_by():
    reg = {"tier": "T2", "model": "claude-sonnet", "runtime": "claude",
           "status": "online", "succeeded_by": None}
    a = build_agent(_GM_SAMPLE, reg)
    assert a["runtime"] == "claude"
    assert a["lineage_status"] == "online"
    assert a["succeeded_by"] is None


def test_build_agent_runtime_defaults_claude_when_absent():
    # A row with no runtime + a claude model resolves to claude (historical).
    a = build_agent(_GM_SAMPLE, {"tier": "T2", "model": "claude-opus-4-8"})
    assert a["runtime"] == "claude"


def test_build_agent_codex_runtime_threaded():
    a = build_agent({"session": "codex-dev-1", "state": "idle",
                     "context_pct": "75%", "state_age_s": 5, "model": ""},
                    {"tier": "T2", "runtime": "codex"})
    assert a["runtime"] == "codex"


# beat_skip_reason: the ONE predicate — None => the beat evaluates the seat; a
# string reason => skip (never nudge/rotate it).

def _agent(runtime="claude", lineage_status="online", succeeded_by=None):
    return {"agent_id": "x", "runtime": runtime,
            "lineage_status": lineage_status, "succeeded_by": succeeded_by}


def test_skip_reason_none_for_live_claude_seat():
    assert beat_skip_reason(_agent()) is None


def test_skip_reason_none_for_live_codex_seat():
    assert beat_skip_reason(_agent(runtime="codex")) is None


def test_skip_reason_none_for_live_gemini_seat():
    assert beat_skip_reason(_agent(runtime="gemini")) is None


def test_skip_reason_unsupported_runtime_service():
    assert beat_skip_reason(_agent(runtime="service")) == "unsupported-runtime"


def test_skip_reason_unsupported_runtime_unknown():
    assert beat_skip_reason(_agent(runtime="unknown")) == "unsupported-runtime"


def test_skip_reason_retired_row():
    assert beat_skip_reason(_agent(lineage_status="retired")) == "retired-or-quiescent"


def test_skip_reason_quiescent_row():
    assert beat_skip_reason(_agent(lineage_status="quiescent")) == "retired-or-quiescent"


def test_skip_reason_succeeded_by_set():
    # a predecessor whose successor is already canonical must never be nudged.
    assert beat_skip_reason(_agent(succeeded_by="x-gen3")) == "succeeded-by-set"


def test_skip_reason_runtime_checked_before_status():
    # an unsupported runtime retired row reports the runtime reason first (both would skip;
    # deterministic ordering keeps the log stable).
    assert beat_skip_reason(
        _agent(runtime="service", lineage_status="retired")) == "unsupported-runtime"


# --- collect_fleet is REGISTRY-SCOPED: tmux is host-global (gm ruling msg_9f04c5f0) ---

def test_collect_fleet_drops_sessions_that_resolve_to_no_registry_row():
    """A second instance beside a live fleet enumerated every tmux session on the
    machine and planned a soft-handoff for a foreign seat. Only sessions that are a
    registry agent id, or a registry row's tmux_session, are fleet."""
    status_list = [
        {"session": "gm", "state": "idle", "context_pct": "29%", "state_age_s": 46, "model": ""},
        {"session": "someone-elses-shell", "state": "working", "context_pct": "80%",
         "state_age_s": 5, "model": ""},
        {"session": "ob-gen44", "state": "idle", "context_pct": "10%", "state_age_s": 5, "model": ""},
    ]
    registry = {"agents": {
        "gm": {"tier": "T0"},
        "ob": {"tier": "T1", "tmux_session": "ob-gen44"},
    }}
    fleet = collect_fleet(status_list, registry)
    assert [a["agent_id"] for a in fleet] == ["gm", "ob-gen44"]


def test_collect_fleet_with_an_empty_registry_is_empty():
    status_list = [{"session": "x", "state": "idle", "context_pct": "1%", "state_age_s": 1, "model": ""}]
    assert collect_fleet(status_list, {"agents": {}}) == []
    assert collect_fleet(status_list, {}) == []
