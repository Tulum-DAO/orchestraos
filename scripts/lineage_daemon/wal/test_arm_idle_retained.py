"""ITEM B / v2.4 (DEC-1789456544274196, CONSENSUS 3/3 over 5a7afa37): the ARM-TIME conformance gate
accepts a RETAINED last-valid in-range ctx for an IDLE seat (mirrors consented finding #2 idle-ceiling),
bounded MAX_ARM_IDLE_CTX_AGE_S=6h, LOUD ctx_source=retained-idle. Busy/none/>6h/out-of-range/unknown
still REFUSE. RED-first. DI (read_ctx_fn, retained_ctx_fn, idle, now, max_idle_age_s) keeps it hermetic."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import conformance as cf  # noqa: E402

FRESH = lambda rt, s, **k: (0.42, True)      # noqa: E731  fresh in-range
STALE = lambda rt, s, **k: (None, False)     # noqa: E731  not fresh


def test_fresh_in_range_conformant_unchanged():
    ok, reasons = cf.is_arm_conformant("claude", "s", read_ctx_fn=FRESH)
    assert ok and "read_ctx_fresh_in_range" in reasons


def test_idle_retained_in_range_within_bound_conformant():
    ok, reasons = cf.is_arm_conformant(
        "claude", "s", read_ctx_fn=STALE, idle=True,
        retained_ctx_fn=lambda: (0.23, 1000.0), arm_now=1000.0 + 3600, max_idle_age_s=21600)
    assert ok
    assert any("retained-idle" in r for r in reasons)


def test_idle_retained_too_old_refuses():
    ok, reasons = cf.is_arm_conformant(
        "claude", "s", read_ctx_fn=STALE, idle=True,
        retained_ctx_fn=lambda: (0.23, 1000.0), arm_now=1000.0 + 21601, max_idle_age_s=21600)
    assert not ok and any("too-old" in r for r in reasons)


def test_idle_no_retained_value_refuses():
    ok, reasons = cf.is_arm_conformant(
        "claude", "s", read_ctx_fn=STALE, idle=True,
        retained_ctx_fn=lambda: (None, None), arm_now=1000.0, max_idle_age_s=21600)
    assert not ok and any("no-retained" in r for r in reasons)


def test_busy_not_fresh_refuses():
    """A BUSY seat (idle=False) with a stale read is REFUSED — its ctx is moving, must be fresh."""
    ok, reasons = cf.is_arm_conformant(
        "claude", "s", read_ctx_fn=STALE, idle=False,
        retained_ctx_fn=lambda: (0.23, 1000.0), arm_now=1000.0, max_idle_age_s=21600)
    assert not ok and any("not-fresh" in r for r in reasons)


def test_retained_out_of_range_refuses():
    ok, reasons = cf.is_arm_conformant(
        "claude", "s", read_ctx_fn=STALE, idle=True,
        retained_ctx_fn=lambda: (1.4, 1000.0), arm_now=1000.0, max_idle_age_s=21600)
    assert not ok and any("out-of-range" in r for r in reasons)


def test_unknown_runtime_refuses_unchanged():
    ok, reasons = cf.is_arm_conformant("banana", "s", read_ctx_fn=FRESH, idle=True,
                                       retained_ctx_fn=lambda: (0.2, 1.0))
    assert not ok and any("unknown-runtime" in r for r in reasons)


def test_core_constant_and_env_override():
    assert cf.MAX_ARM_IDLE_CTX_AGE_S == 21600
    assert cf._effective_max_arm_idle_age() == 21600
    import os
    os.environ["BG_ARM_IDLE_CTX_MAX_AGE_S"] = "60"
    try:
        assert cf._effective_max_arm_idle_age() == 60
        # with the override, a retained read 120s old is now too old
        ok, _ = cf.is_arm_conformant("claude", "s", read_ctx_fn=STALE, idle=True,
                                     retained_ctx_fn=lambda: (0.2, 1000.0), arm_now=1120.0)
        assert not ok
    finally:
        del os.environ["BG_ARM_IDLE_CTX_MAX_AGE_S"]
    assert cf.MAX_ARM_IDLE_CTX_AGE_S == 21600   # core constant untouched by the override
