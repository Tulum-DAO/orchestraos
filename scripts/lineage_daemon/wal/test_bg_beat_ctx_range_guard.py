"""RED-first — build_obs must NEVER store an un-normalized ctx fraction.

By effect (gm): state/wal/inspiration.bg.json meta.last_valid_ctx_pct = 1.43 (>1). The arm
gate correctly refused it (retained-out-of-range) but the WRITER should never persist it.
Guard at the write: skip + LOUD breadcrumb (reason ctx:out-of-range), never silently clamp.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def _agent(pct):
    return {"agent_id": "ctxguard-victim", "runtime": "claude",
            "ctx": {"status_bar_pct": pct}, "death": {}}


def test_out_of_range_ctx_is_not_written_and_breadcrumbs(tmp_path):
    # status_bar 143 -> _sb_pct 1.43; empty detector_dir => adapter read is None => sb path.
    bg_beat.build_obs(_agent(143), {"session_id": "sid-x", "generation": 1, "blue_generation_id": 100}, wal_dir=str(tmp_path),
                      now=1000.0, detector_dir=str(tmp_path))
    st = BgStateStore(str(tmp_path), "ctxguard-victim")
    assert st.read_meta("last_valid_ctx_pct") is None, "an out-of-range ctx must NOT be stored"
    bc = st.read_meta("last_ctx_out_of_range")
    assert bc is not None and bc.get("pct") == 1.43, f"expected a breadcrumb; got {bc}"


def test_in_range_ctx_is_written(tmp_path):
    bg_beat.build_obs(_agent(23), {"session_id": "sid-x", "generation": 1, "blue_generation_id": 100}, wal_dir=str(tmp_path),
                      now=1000.0, detector_dir=str(tmp_path))
    st = BgStateStore(str(tmp_path), "ctxguard-victim")
    assert st.read_meta("last_valid_ctx_pct") == 0.23
    assert st.read_meta("last_ctx_out_of_range") is None
