"""RED-first tests for v2-2 (every seam time-bounded) + v2-3 (verify stall bound).

v2-2: bg_beat._Seams wraps every effect seam in bounded_call, so a HANGING seam
converts to SeamTimeout (a raise the beat firewall catches) rather than wedging the
whole fleet beat. The bg_arm firewall catches raises, NOT hangs — this closes that
gap for spawn/verify/hydrate/reap.

v2-3: a Green that boots but never makes WAL progress (wedged) makes verify return
False forever, which scores acted++ each beat (silent indefinite PREWARMING). The
stall bound raises VerifyStalled after M no-progress beats (persisted counter) so the
firewall alarms + disarms — consistent with inv 7 (anti-orphan).
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal import verify_stall  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


# ---- v2-2: every seam time-bounded (hang -> SeamTimeout) ---------------------

def _hang(*a, **k):
    import time
    time.sleep(30)


def _seams_with(spawn=None, verify=None, hydrate=None, reap=None, timeout_s=0.5):
    noop = lambda *a, **k: None  # noqa: E731
    return bg_beat._Seams(
        register_provisional=noop, project_now=noop, swap=noop,
        spawn_fn=spawn or noop, verify_fn=verify or (lambda *a: True),
        hydrate_fn=hydrate or noop, reap_fn=reap or noop, timeout_s=timeout_s)


def test_hanging_spawn_raises_seamtimeout():
    seams = _seams_with(spawn=_hang)
    with pytest.raises(bg_beat.SeamTimeout):
        seams.spawn("root", "green")


def test_hanging_verify_raises_seamtimeout():
    seams = _seams_with(verify=_hang)
    with pytest.raises(bg_beat.SeamTimeout):
        seams.verify("root", "green")


def test_hanging_hydrate_raises_seamtimeout():
    seams = _seams_with(hydrate=_hang)
    with pytest.raises(bg_beat.SeamTimeout):
        seams.hydrate("root", "green", 0)


def test_hanging_reap_raises_seamtimeout():
    seams = _seams_with(reap=_hang)
    with pytest.raises(bg_beat.SeamTimeout):
        seams.reap("root", 6)


def test_bounded_seam_returns_value_when_fast():
    seams = _seams_with(verify=lambda r, g: True, timeout_s=5)
    assert seams.verify("root", "green") is True


def test_a_raising_seam_propagates_the_original_error():
    def boom(*a, **k):
        raise ValueError("real failure")
    seams = _seams_with(spawn=boom, timeout_s=5)
    with pytest.raises(ValueError, match="real failure"):
        seams.spawn("root", "green")


# ---- v2-3: verify stall bound (M no-progress beats -> raise) -----------------

def test_verify_stall_raises_after_m_no_progress_beats(tmp_path):
    """Green wedged: WAL progress flat, verify False. After M beats -> VerifyStalled."""
    root = "bg-drill-victim"
    # progress_fn returns a constant seq (no advance = wedged green).
    wrapped = verify_stall.verify_with_stall_bound(
        verify_fn=lambda r, g: False,
        progress_fn=lambda r, g: 5,           # flat, never advances
        wal_dir=str(tmp_path), max_stall_beats=3)
    # beats 1,2: False, no raise
    assert wrapped(root, "green") is False
    assert wrapped(root, "green") is False
    # beat 3: M reached -> raise
    with pytest.raises(verify_stall.VerifyStalled):
        wrapped(root, "green")


def test_verify_stall_resets_on_wal_progress(tmp_path):
    """A warming Green whose WAL IS advancing is NOT stalled — the counter resets
    on progress, so a slow-but-live warmup never false-trips the bound."""
    root = "bg-drill-victim"
    seqs = iter([5, 5, 6, 6, 6])   # progress at beat 3, then flat again
    wrapped = verify_stall.verify_with_stall_bound(
        verify_fn=lambda r, g: False,
        progress_fn=lambda r, g: next(seqs),
        wal_dir=str(tmp_path), max_stall_beats=3)
    wrapped(root, "green")   # 5  stall=1
    wrapped(root, "green")   # 5  stall=2
    wrapped(root, "green")   # 6  progress -> stall reset to 0
    wrapped(root, "green")   # 6  stall=1
    # still under the bound after the reset (would have raised at beat 3 without it)
    assert wrapped(root, "green") is False   # 6 stall=2, no raise


def test_verify_stall_true_short_circuits_no_stall(tmp_path):
    """A verify that returns True (Green ready) never counts toward the stall bound."""
    root = "bg-drill-victim"
    wrapped = verify_stall.verify_with_stall_bound(
        verify_fn=lambda r, g: True,
        progress_fn=lambda r, g: 5,
        wal_dir=str(tmp_path), max_stall_beats=2)
    for _ in range(5):
        assert wrapped(root, "green") is True
    # ready seat never accrues stall; counter stays 0
    assert BgStateStore(str(tmp_path), root).read_meta("verify_stall_beats", 0) == 0
