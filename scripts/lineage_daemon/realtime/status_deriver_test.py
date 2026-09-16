"""RED tests for the B1(a) status deriver — the 2x2 (bytes x /proc-CPU)
discriminator that DELETES the 300/900/600 TTL crutches and SUBSUMES the
provider-idle-detection commission.

Driven directly by the A0 golden_cases in each provider fixture (the RED tests
A0 was built to become). Also proves the load-bearing per-provider claims:
bytes-axis PRIMARY, IO-delta tiebreak (codex), hooks-absent graceful degrade.
"""
import json
import os

from lineage_daemon.realtime.provider_profiles import profile_for
from lineage_daemon.realtime.status_deriver import derive_status

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "contract", "transcript", "fixtures")


def _golden(provider):
    with open(os.path.join(_FIX, provider, "a0-status-discriminator.json")) as fh:
        return json.load(fh)["golden_cases"]


def _run_all_golden(provider):
    prof = profile_for(provider)
    out = []
    for case in _golden(provider):
        got = derive_status(case["input"], profile=prof)
        out.append((case["name"], case["expected_status"], got))
    return out


def test_claude_golden_cases_all_pass():
    for name, expected, got in _run_all_golden("claude"):
        assert got == expected, f"claude/{name}: expected {expected}, got {got}"


def test_codex_golden_cases_all_pass():
    for name, expected, got in _run_all_golden("codex"):
        assert got == expected, f"codex/{name}: expected {expected}, got {got}"


def test_gemini_golden_cases_all_pass():
    for name, expected, got in _run_all_golden("gemini"):
        assert got == expected, f"gemini/{name}: expected {expected}, got {got}"


# --- bytes-axis PRIMARY -----------------------------------------------------

def test_bytes_flowing_is_streaming_even_at_zero_cpu():
    # claude streaming golden: bytes flow but cpu ~0 -> STILL streaming (the
    # bytes axis overrides the cpu axis).
    s = {"bytes_flowing": True, "chars_per_s": 4383, "cpu_core": 0.0,
         "io_delta": 217, "live_pids": 5, "chrome": {"working": True}}
    assert derive_status(s, profile=profile_for("claude")) == "streaming"


# --- IO-delta tiebreak is load-bearing for codex ----------------------------

def test_codex_computing_decided_by_io_not_cpu():
    # cpu 1.46 is BELOW codex idle-baseline (~4) — CPU alone would call this
    # idle. Only the 61KB IO-delta makes it COMPUTING. This is the whole reason
    # the codex-idle-baseline had to be measured.
    prof = profile_for("codex")
    s = {"bytes_flowing": False, "cpu_core": 1.46, "io_delta": 61456,
         "live_pids": 3, "chrome": {"idle_placeholder": True, "working": False}}
    assert s["cpu_core"] < prof.idle_baseline_core_pct  # CPU would say idle
    assert derive_status(s, profile=prof) == "computing"  # IO says computing


def test_gemini_idle_top_of_band_is_not_computing():
    # gemini idle at 1.95% (top of render-loop band) must NOT read computing —
    # a claude-tuned global eps (~0.5) would misfire here.
    prof = profile_for("gemini")
    s = {"bytes_flowing": False, "cpu_core": 1.95, "io_delta": 1600,
         "live_pids": 2, "chrome": {"idle_footer": True}}
    assert derive_status(s, profile=prof) == "idle"


def test_a_single_global_epsilon_would_misclassify_one_side():
    # Proof per-provider config is necessary: apply GEMINI's profile (baseline
    # 1.4, eps 0.6, cpu_axis_reliable=True — a "global epsilon" lens) to a codex
    # idle-high render-loop sample -> it wrongly reads computing; the correct
    # codex profile (cpu_axis_reliable=False, render loop engulfs compute) reads
    # idle. (Claude joined the cpu_axis_reliable=False set on 2.1.260, so it can
    # no longer serve as the wrong-lens example here.)
    codex_idle_high = {"bytes_flowing": False, "cpu_core": 6.0,
                       "io_delta": 1600, "live_pids": 3}
    wrong = derive_status(codex_idle_high, profile=profile_for("gemini"))
    right = derive_status(codex_idle_high, profile=profile_for("codex"))
    assert wrong == "computing" and right == "idle"


# --- hooks-absent graceful degrade (codex/gemini have NO hooks) --------------

def test_waiting_permission_via_store_for_gemini_no_hooks():
    prof = profile_for("gemini")
    s = {"bytes_flowing": False, "cpu_core": 1.4, "io_delta": 1600,
         "live_pids": 2, "store": {"step_type": 132, "permissions_blob_populated": True}}
    assert derive_status(s, profile=prof, hooks=None) == "waiting_permission"


def test_claude_waiting_permission_via_hook_semantic_layer():
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}}
    assert derive_status(s, profile=prof,
                         hooks={"state": "waiting_permission"}) == "waiting_permission"


def test_never_assumes_hook_presence_codex_uses_chrome_signature():
    # codex has no hooks; permission intent arrives via pty-signature match
    # (reduced to chrome.permission here). Deriver must not require hooks.
    prof = profile_for("codex")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 2,
         "chrome": {"permission": True}}
    assert derive_status(s, profile=prof, hooks=None) == "waiting_permission"


# --- offline + stall ---------------------------------------------------------

def test_offline_when_pane_gone_or_no_live_pids():
    prof = profile_for("claude")
    assert derive_status({"pane_gone": True, "live_pids": 0}, profile=prof) == "offline_crashed"
    assert derive_status({"live_pids": 0}, profile=prof) == "offline_crashed"


def test_stall_is_working_intent_with_zero_activity():
    # claude stalled golden: working chrome but zero cpu/io/bytes + a live child.
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 6,
         "chrome": {"working": True}}
    assert derive_status(s, profile=prof) == "stalled"


# --- E2: transcript turn-completion vetoes a stale-by-emission working hook ------
# Claude emits NO Stop on interrupt/kill, so a hook FILE freezes at working -> a
# genuinely-idle seat reads stalled. The transcript disambiguates: turn COMPLETE
# (no pending tool_use) -> idle; turn OPEN (pending tool_use) -> a real hang stays
# stalled. `turn_complete` rides in the sample (True/False/None).

def test_working_hook_with_completed_turn_reads_idle_not_stalled():
    # the missed-Stop residual: working hook + zero activity, but the transcript
    # shows the turn COMPLETED (turn_complete=True) -> the hook is stale-by-emission
    # -> idle, NOT stalled.
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}, "turn_complete": True}
    assert derive_status(s, profile=prof, hooks={"state": "working"}) == "idle"


def test_working_hook_with_open_turn_stays_stalled():
    # a GENUINE mid-tool hang: working hook + zero activity + the transcript shows
    # the turn is OPEN (pending tool_use, turn_complete=False) -> MUST stay stalled
    # (never mask a real hang).
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}, "turn_complete": False}
    assert derive_status(s, profile=prof, hooks={"state": "working"}) == "stalled"


def test_working_hook_with_unknown_turn_stays_stalled_back_compat():
    # no transcript signal (turn_complete absent/None) -> trust the hook exactly as
    # before (no regression for non-claude / no-transcript seats).
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}}
    assert derive_status(s, profile=prof, hooks={"state": "working"}) == "stalled"
    s2 = dict(s, turn_complete=None)
    assert derive_status(s2, profile=prof, hooks={"state": "working"}) == "stalled"


def test_completed_turn_does_not_override_genuine_activity():
    # turn_complete only vetoes the STALLED path; a seat with real IO still computes
    # and a streaming seat still streams — the veto never masks live activity.
    prof = profile_for("claude")
    streaming = {"bytes_flowing": True, "turn_complete": True, "chrome": {},
                 "live_pids": 3}
    assert derive_status(streaming, profile=prof, hooks={"state": "working"}) == "streaming"


def test_idle_footer_with_zero_activity_is_idle_not_stall():
    # the star glyph / idle footer must NOT be read as working (A0 false-positive
    # guard: 5/5 idle claude panes false-fired on the decorative star).
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.03, "io_delta": 48, "live_pids": 5,
         "chrome": {"working": False, "idle_footer": True, "star_glyph": True}}
    assert derive_status(s, profile=prof) == "idle"


# --- 2.1.260 claude idle-CPU regression (the operator-caught, gm gate) ---------------
# 2.1.260 idle claude runs a wide/variable render-loop CPU floor (0-4.6%), so the
# old baseline 0 + eps 0.5 + cpu_axis_reliable=True false-fired "computing" on
# idle. cpu_axis_reliable=False (codex precedent) makes the CPU axis inert for
# claude; compute still detected via IO + working hook.

def test_claude_2_1_260_idle_render_loop_not_computing():
    prof = profile_for("claude")
    idle = {"bytes_flowing": False, "cpu_core": 3.8, "io_delta": 0, "live_pids": 3}
    assert derive_status(idle, profile=prof) == "idle"       # 3.8% render loop != compute


def test_claude_compute_still_detected_via_io_axis():
    prof = profile_for("claude")
    compute = {"bytes_flowing": False, "cpu_core": 3.8, "io_delta": 60000, "live_pids": 3}
    assert derive_status(compute, profile=prof) == "computing"   # IO axis still fires


def test_claude_compute_still_detected_via_working_hook():
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 3.8, "io_delta": 0, "live_pids": 3}
    assert derive_status(s, profile=prof, hooks={"state": "working"}) == "stalled"  # intent, no activity


# --- E1: hook-event RECENCY vetoes the false stall (the operator flap: gm/orchestra-builder) ---
# A claude reasoning agent BETWEEN tool calls reads zero local bytes/cpu/io during a
# network-bound model wait, but its working hook + open turn otherwise => stalled
# (the flap). A lifecycle hook seen within profile.hook_recency_s is proof-of-life.

def test_recent_working_hook_reads_computing_not_stalled():
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}, "turn_complete": False}
    assert derive_status(s, profile=prof,
                         hooks={"state": "working", "age_s": 2.0}) == "computing"


def test_stale_working_hook_still_reads_stalled():
    # same window, but the last hook fired 300s ago -> a genuine hang -> stalled
    # (recency vetoes only FRESH proof-of-life; a real stall still surfaces).
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}, "turn_complete": False}
    assert derive_status(s, profile=prof,
                         hooks={"state": "working", "age_s": 300.0}) == "stalled"


def test_working_hook_without_age_preserves_stalled_backcompat():
    # no recency signal (telemetryd not yet emitting age) -> classic behavior, stalled.
    prof = profile_for("claude")
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 5,
         "chrome": {}}
    assert derive_status(s, profile=prof, hooks={"state": "working"}) == "stalled"


def test_recency_disabled_profile_ignores_age_backcompat():
    # a profile with hook_recency_s==0 (codex/gemini default) never vetoes, even with
    # a fresh age -> chrome-working stalled path preserved for hooks-absent runtimes.
    prof = profile_for("codex")
    assert getattr(prof, "hook_recency_s", 0.0) == 0.0
    s = {"bytes_flowing": False, "cpu_core": 0.0, "io_delta": 0, "live_pids": 2,
         "chrome": {"working": True}, "hook_event_age_s": 1.0}
    assert derive_status(s, profile=prof, hooks=None) == "stalled"


def test_recency_veto_never_masks_real_activity():
    # the veto sits ONLY on the stalled path: a streaming/computing window is untouched.
    prof = profile_for("claude")
    streaming = {"bytes_flowing": True, "chrome": {}, "live_pids": 3}
    assert derive_status(streaming, profile=prof,
                         hooks={"state": "working", "age_s": 1.0}) == "streaming"
