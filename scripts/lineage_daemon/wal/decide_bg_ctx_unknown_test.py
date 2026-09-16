"""RED (BG leg-(ii) P0.6 — decide_bg must ALARM on UNKNOWN ctx, never silent 0-noop).

The other half of the blind-telemetry fix: build_obs no longer coerces an unknown ctx to
0.0 (it emits ctx_unknown + a retained/None value). decide_bg must react:
  * ctx_unknown AND ctx_pct is None  -> noop + ALARM (reason ctx:unknown-alarm), NOT a
    silent ctx:0.00 noop that hides the blind seat;
  * ctx_unknown AND a retained (stale) ctx_pct -> run the normal ladder on the last-known
    value BUT flag alarm=True (act on last-known, surfaced).
Back-compat: obs WITHOUT ctx_unknown behaves EXACTLY as before (no alarm).
"""
from scripts.lineage_daemon.wal.decide_bg import decide_bg


def test_unknown_none_ctx_is_noop_with_alarm_not_silent_zero():
    d = decide_bg({"root": "x", "runtime": "claude", "death": {},
                   "ceiling_calibrated": True, "ctx_pct": None, "ctx_unknown": True})
    assert d["action"] == "noop"
    assert d["reason"] == "ctx:unknown-alarm", "blind ctx must NOT read as a silent 0-noop"
    assert d["alarm"] is True


def test_unknown_stale_value_runs_ladder_but_alarms():
    # a retained last-valid of 0.82 is still actionable (swap), but flagged
    d = decide_bg({"root": "x", "runtime": "claude", "death": {},
                   "ceiling_calibrated": True, "ctx_pct": 0.82, "ctx_unknown": True})
    assert d["action"] == "swap"
    assert d["reason"] == "ctx:swap"
    assert d["alarm"] is True, "acting on a STALE last-valid must surface an alarm"


def test_death_still_dominates_even_when_ctx_unknown():
    d = decide_bg({"root": "x", "runtime": "claude", "death": "oom",
                   "ceiling_calibrated": True, "ctx_pct": None, "ctx_unknown": True})
    assert d["action"] == "swap"
    assert d["reason"] == "death:oom"
    assert d["never_gated"] is True


def test_known_ctx_is_unaffected_no_alarm():
    # explicit ctx_unknown False => byte-identical to legacy behavior
    d = decide_bg({"root": "x", "runtime": "claude", "death": {},
                   "ceiling_calibrated": True, "ctx_pct": 0.50, "ctx_unknown": False})
    assert d["action"] == "noop"
    assert d["reason"] == "ctx:0.50"
    assert d["alarm"] is False
