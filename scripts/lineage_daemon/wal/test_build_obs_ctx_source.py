"""RED (BG leg-(ii) P0.6 — the ctx-source: blind-telemetry fix, r-a-b #1 blocker).

Today build_obs (bg_beat.py) reads ONLY agent.ctx.status_bar_pct and coerces a missing
value None->0.0 — so a Blue whose status bar is blind looks like an EMPTY-ctx noop and the
autonomous beat never fires (blind telemetry). P0.6:
  * read the live detector file /tmp/claude-ctx-{blue_sid}.json used_pct FIRST (BLUE's sid
    comes from the canonical doc via read_canonical_blue);
  * fall back to status_bar_pct;
  * an UNKNOWN reading NEVER coerces to 0.0 — retain the last-valid value + its age and
    flag ctx_unknown (alarm), so decide_bg alarms instead of silently reading empty-noop.

Real-object (leg-(i) lesson): every case drives the REAL build_obs against a REAL detector
file + a REAL BgStateStore (detector_dir + wal_dir injected for hermeticity; the production
default is /tmp). RED until build_obs sources the detector + retains last-valid.
"""
import json
import os

from scripts.lineage_daemon.wal import bg_beat
from scripts.lineage_daemon.wal.bg_state import BgStateStore

ROOT = "second-brain-dev"
BLUE_SID = "blue-sid-abc"


def _blue(gen=4, sid=BLUE_SID):
    b = {"generation": gen, "model": "claude-opus-4-8[1m]", "blue_generation_id": 41}
    if sid is not None:
        b["session_id"] = sid
    return b


def _agent(status_bar=78):
    ctx = {} if status_bar is None else {"status_bar_pct": status_bar}
    return {"agent_id": ROOT, "runtime": "claude", "ctx": ctx, "death": {}}


def _write_detector(detector_dir, sid, used_pct, ts):
    os.makedirs(detector_dir, exist_ok=True)
    with open(os.path.join(detector_dir, f"claude-ctx-{sid}.json"), "w") as fh:
        json.dump({"session_id": sid, "used_pct": used_pct, "timestamp": ts}, fh)


# ── detector file is the PRIMARY source (beats status_bar) ─────────────────────

def test_detector_used_pct_is_primary_over_status_bar(tmp_path):
    det = str(tmp_path / "det")
    _write_detector(det, BLUE_SID, used_pct=83, ts=1000.0)
    obs = bg_beat.build_obs(_agent(status_bar=50), _blue(), wal_dir=str(tmp_path / "wal"),
                            now=1000.0, ctx_ttl_s=120.0, detector_dir=det)
    assert obs["ctx_pct"] == 0.83, "detector used_pct (83%) must win over status_bar (50%)"
    assert obs["ctx_source"] == "adapter"
    assert obs["ctx_unknown"] is False


def test_detector_valid_read_persists_last_valid(tmp_path):
    det = str(tmp_path / "det")
    wal = str(tmp_path / "wal")
    _write_detector(det, BLUE_SID, used_pct=75, ts=2000.0)
    bg_beat.build_obs(_agent(status_bar=None), _blue(), wal_dir=wal,
                      now=2000.0, ctx_ttl_s=120.0, detector_dir=det)
    st = BgStateStore(wal, ROOT)
    assert st.read_meta("last_valid_ctx_pct") == 0.75, "a valid read must be retained"
    assert st.read_meta("last_valid_ctx_ts") == 2000.0


# ── stale detector file (older than TTL) is ignored ────────────────────────────

def test_stale_detector_falls_back_to_status_bar(tmp_path):
    det = str(tmp_path / "det")
    _write_detector(det, BLUE_SID, used_pct=90, ts=1.0)   # ancient
    obs = bg_beat.build_obs(_agent(status_bar=60), _blue(), wal_dir=str(tmp_path / "wal"),
                            now=1000.0, ctx_ttl_s=120.0, detector_dir=det)
    assert obs["ctx_pct"] == 0.60, "a stale detector (age>ttl) must be ignored -> status_bar"
    assert obs["ctx_source"] == "status_bar"


# ── status_bar fallback when no detector ───────────────────────────────────────

def test_no_detector_uses_status_bar(tmp_path):
    obs = bg_beat.build_obs(_agent(status_bar=72), _blue(sid=None),
                            wal_dir=str(tmp_path / "wal"), now=1000.0,
                            detector_dir=str(tmp_path / "det"))
    assert obs["ctx_pct"] == 0.72
    assert obs["ctx_source"] == "status_bar"
    assert obs["ctx_unknown"] is False


# ── UNKNOWN NEVER coerces to 0.0 — retain last-valid + age, alarm ──────────────

def test_unknown_retains_last_valid_with_age_and_flags_unknown(tmp_path):
    wal = str(tmp_path / "wal")
    st = BgStateStore(wal, ROOT)
    st.write_meta("last_valid_ctx_pct", 0.77)
    st.write_meta("last_valid_ctx_ts", 900.0)
    # no detector file, status_bar None => UNKNOWN
    obs = bg_beat.build_obs(_agent(status_bar=None), _blue(sid=None), wal_dir=wal,
                            now=1000.0, detector_dir=str(tmp_path / "det"))
    assert obs["ctx_pct"] == 0.77, "UNKNOWN must retain the last-valid value, NOT coerce to 0"
    assert obs["ctx_unknown"] is True
    assert obs["ctx_source"] == "stale"
    assert obs["ctx_age_s"] == 100.0, "carry the age of the last-valid reading"


def test_detector_out_of_range_used_pct_is_clamped(tmp_path):
    """Defense-in-depth (reviewer nit): a malformed >100 (or <0) detector value must never
    yield ctx_pct outside 0..1 (which could spuriously trip ctx:swap once armed)."""
    det = str(tmp_path / "det")
    _write_detector(det, BLUE_SID, used_pct=140, ts=1000.0)   # malformed
    obs = bg_beat.build_obs(_agent(status_bar=None), _blue(), wal_dir=str(tmp_path / "wal"),
                            now=1000.0, ctx_ttl_s=120.0, detector_dir=det)
    assert obs["ctx_pct"] == 1.0, "an out-of-range detector value must clamp to <=1.0"
    assert obs["ctx_source"] == "adapter"


def test_unknown_with_no_history_is_none_never_zero(tmp_path):
    obs = bg_beat.build_obs(_agent(status_bar=None), _blue(sid=None),
                            wal_dir=str(tmp_path / "wal"), now=1000.0,
                            detector_dir=str(tmp_path / "det"))
    assert obs["ctx_pct"] is None, "no live + no history => None (UNKNOWN), NEVER 0.0"
    assert obs["ctx_unknown"] is True
    assert obs["ctx_source"] == "unknown"
