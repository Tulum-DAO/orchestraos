"""RED tests — the per-provider end-to-end latency harness (leg b).

Measures pty-byte -> deriver -> status.json (streaming) latency for EACH provider
(claude + codex + gemini, incl. the codex bytes-primary path). The unit test
asserts the DERIVER processing latency is <<1s for every provider (the fixed poll
interval FAST_POLL_S<=1s is the only other term, so end-to-end stays <=1s). The
live poll-bounded e2e run (harness `main`) produces the real evidence table.
"""
from .latency_harness import measure_processing_latency, PROVIDERS


def test_measures_all_three_providers(tmp_path):
    res = {p: measure_processing_latency(p, base=str(tmp_path / p)) for p in PROVIDERS}
    assert set(res) == {"claude", "codex", "gemini"}


def test_processing_latency_under_1s_and_flips_streaming(tmp_path):
    for p in PROVIDERS:
        m = measure_processing_latency(p, base=str(tmp_path / p))
        assert m["became_streaming"] is True          # the byte -> streaming worked
        assert 0.0 <= m["processing_s"] < 1.0          # deriver path well under 1s
        assert m["provider"] == p


def test_codex_bytes_primary_path_is_covered(tmp_path):
    # codex CPU axis is unreliable; the streaming flip must come from BYTES, not CPU.
    m = measure_processing_latency("codex", base=str(tmp_path / "cx"))
    assert m["became_streaming"] is True


# --- AMEND (the operator-ordered): sub-second status latency on EVERY agent ----------
# FAST lane must be sub-second so end-to-end status latency is reliably <1s
# (aim ~0.5s p95). e2e = deriver processing (~ms) + the fixed FAST poll, so the
# bound is processing_s + FAST_POLL_S; asserting that per provider is the p95
# gate (p95 <= max <= processing + poll). The CPU "computing" axis stays on its
# 5s floor; sub-second applies to the FAST-lane status transitions.

def test_fast_poll_is_sub_second():
    from .telemetryd import FAST_POLL_S
    assert FAST_POLL_S <= 0.5, f"FAST_POLL_S={FAST_POLL_S} is not sub-second"


def test_e2e_status_latency_bound_under_1s_per_provider(tmp_path):
    from .telemetryd import FAST_POLL_S
    for p in PROVIDERS:
        m = measure_processing_latency(p, base=str(tmp_path / p))
        assert m["became_streaming"] is True
        e2e_bound = m["processing_s"] + FAST_POLL_S       # p95 <= max <= this
        assert e2e_bound < 1.0, f"{p}: e2e bound {e2e_bound:.3f}s >= 1s"
