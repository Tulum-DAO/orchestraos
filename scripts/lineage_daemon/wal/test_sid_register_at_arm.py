"""GOAL-FINDING #1 (sid-register-at-arm) + #5 part-B (WAL-capture-start), GREEN-impl.

Arming an UNSTAGED seat (the demo fixture: no sid map, no WAL) must MECHANICALLY make it
readable + hydratable — never by hand-staging. arm_lineage runs, BEFORE the conformance
gate, an injected register_sid_fn (maps the live sid so read_ctx sees the seat) and
start_capture_fn (starts WAL capture with backfill). Fail-soft; the conformance gate is
the hard check. Provider-agnostic (the hooks are the arm command's real wiring).
"""
import json
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_state  # noqa: E402

ROOT = "sid-arm-seat"
SID = "sid-live-1"


def _detector_path(det, sid):
    return det / f"claude-ctx-{sid}.json"


def test_unstaged_seat_refused_without_sid_register(tmp_path):
    """No sid-register hook => read_ctx can't see the seat => conformance REFUSES arm."""
    det = tmp_path / "det"; det.mkdir()
    with pytest.raises(bg_state.ArmNotConformant):
        bg_state.arm_lineage(str(tmp_path / "wal"), ROOT, "claude", seat=ROOT,
                             detector_dir=str(det), sid=SID, now=1000.0, ttl_s=120.0)
    assert bg_state.is_armed(str(tmp_path / "wal"), ROOT) is False


def test_arm_registers_sid_and_starts_capture_then_arms(tmp_path):
    """register_sid_fn MECHANICALLY makes the seat readable (writes its detector, keyed on
    the mapped sid) BEFORE the gate; start_capture_fn is invoked; then the gate PASSES."""
    det = tmp_path / "det"; det.mkdir()
    calls = []

    def register_sid(seat):
        calls.append(("register", seat))
        _detector_path(det, SID).write_text(json.dumps({"used_pct": 40, "timestamp": 1000.0}))

    def start_capture(seat):
        calls.append(("capture", seat))

    path = bg_state.arm_lineage(
        str(tmp_path / "wal"), ROOT, "claude", seat=ROOT,
        register_sid_fn=register_sid, start_capture_fn=start_capture,
        detector_dir=str(det), sid=SID, now=1000.0, ttl_s=120.0)
    assert bg_state.is_armed(str(tmp_path / "wal"), ROOT) is True
    assert path.endswith(f"{ROOT}.bg_enabled")
    # both mechanics ran, sid-register BEFORE capture (order), both BEFORE the gate/arm
    assert calls == [("register", ROOT), ("capture", ROOT)]


def test_capture_start_runs_even_when_already_readable(tmp_path):
    det = tmp_path / "det"; det.mkdir()
    _detector_path(det, SID).write_text(json.dumps({"used_pct": 40, "timestamp": 1000.0}))
    started = []
    bg_state.arm_lineage(
        str(tmp_path / "wal"), ROOT, "claude", seat=ROOT,
        start_capture_fn=lambda s: started.append(s),
        detector_dir=str(det), sid=SID, now=1000.0, ttl_s=120.0)
    assert started == [ROOT]


def test_failsoft_register_error_is_breadcrumbed_not_fatal(tmp_path):
    """A raising register hook must NOT crash the arm; it records a breadcrumb, and the
    conformance gate then refuses (seat still unreadable) — fail-closed overall."""
    det = tmp_path / "det"; det.mkdir()
    wal = str(tmp_path / "wal")

    def boom(seat):
        raise RuntimeError("resolver down")

    with pytest.raises(bg_state.ArmNotConformant):
        bg_state.arm_lineage(wal, ROOT, "claude", seat=ROOT, register_sid_fn=boom,
                             detector_dir=str(det), sid=SID, now=1000.0, ttl_s=120.0)
    assert bg_state.BgStateStore(wal, ROOT).read_meta("arm_sid_register_error") is not None
    assert bg_state.is_armed(wal, ROOT) is False
