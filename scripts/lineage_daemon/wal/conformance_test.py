"""Conformance-gated arm (DEC v2 §4, fail-closed) — an un-conformed provider's seat is
REFUSED at arm; a conformant one is armed. This is the gemini-gap safety property
(demo-gemini-pred not surfaced by its reader => cannot arm)."""
import json
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import conformance as conf          # noqa: E402
from lineage_daemon.wal import bg_state                      # noqa: E402


def _detector(tmp_path, sid, used_pct=82, ts=1_000_000.0):
    (tmp_path / f"claude-ctx-{sid}.json").write_text(
        json.dumps({"used_pct": used_pct, "timestamp": ts}))


# ---- arm-time gate ----

def test_unknown_runtime_refused():
    ok, reasons = conf.is_arm_conformant("some-future-provider", "seat")
    assert ok is False and "no-adapter" in reasons[0]


def test_seat_with_no_fresh_ctx_refused(tmp_path):
    ok, reasons = conf.is_arm_conformant("claude", "seat", detector_dir=str(tmp_path),
                                         sid="absent", now=1.0, ttl_s=120.0)
    assert ok is False and any("read_ctx-not-fresh" in r for r in reasons)


def test_conformant_claude_seat_passes(tmp_path):
    _detector(tmp_path, "sid1")
    ok, reasons = conf.is_arm_conformant("claude", "seat", detector_dir=str(tmp_path),
                                         sid="sid1", now=1_000_000.0, ttl_s=120.0)
    assert ok is True and "read_ctx_fresh_in_range" in reasons


def test_normalization_pin_codex():
    ok, why = conf.normalization_matches_pin("codex")
    assert ok is True, why


# ---- the fail-closed arm itself: flag NOT written on refusal ----

def test_arm_lineage_refuses_and_writes_nothing(tmp_path):
    wal = str(tmp_path)
    with pytest.raises(bg_state.ArmNotConformant):
        # HERMETIC: a gemini seat NOT surfaced by any live reader (no 'You are <seat>'
        # brain) => read_ctx None => conformance refuses. (demo-gemini-pred is now a LIVE
        # readable seat, so use a guaranteed-absent name to keep this test hermetic.)
        bg_state.arm_lineage(wal, "victim", "gemini", seat="no-such-gemini-seat-zzz9")
    assert bg_state.is_armed(wal, "victim") is False   # fail-closed: never armed


def test_arm_lineage_arms_a_conformant_seat(tmp_path):
    wal = str(tmp_path)
    _detector(tmp_path, "sidA")
    bg_state.arm_lineage(wal, "root", "claude", seat="root",
                         detector_dir=wal, sid="sidA", now=1_000_000.0, ttl_s=120.0)
    assert bg_state.is_armed(wal, "root") is True
