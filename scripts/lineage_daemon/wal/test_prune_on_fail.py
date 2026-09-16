"""(d) PRUNE-ON-FAIL — any fail-closed path AFTER a provisional green is registered
(spawn fail / VerifyStalled / timeout) must remove the leaked provisional: prune its DB
generations row + reap its pane + reset the seat to SOLO, so no live provisional row or
pane survives (the M1 delta the live re-fire left: demo-gemini-pred-g2 stayed a live
provisional). Injected prune/reap so it is unit-testable without a live DB/tmux.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def test_prune_failed_green_prunes_reaps_and_resets(tmp_path):
    root = "demo-gemini-pred"
    green_alias = f"{root}-g2"
    st = BgStateStore(str(tmp_path), root)
    # seat had reached PREWARMING with a registered provisional + green markers
    st.write_state("PREWARMING", reason="ctx:prewarm")
    st.write_meta("green_session_id", "206f75be-cbcd-44c6-8c86-8f87ef3c7eff")
    st.write_meta("ingest_wake_sent", True)
    st.write_meta("verify_stall_beats", 6)

    pruned, reaped = [], []
    bg_beat.prune_failed_green(
        str(tmp_path), str(tmp_path), root, 2, green_alias,
        prune_fn=lambda: pruned.append((root, 2)) or True,
        reap_pane_fn=lambda a: reaped.append(a))

    assert pruned == [(root, 2)], "the provisional DB row must be pruned"
    assert reaped == [green_alias], "the green pane must be reaped"
    # seat reset to SOLO so a re-arm re-spawns cleanly (no verify against a gone green)
    assert st.read()["state"] == "SOLO"
    # green markers cleared (no stale sid / sent-flag / stall count leaking into a retry)
    assert st.read_meta("green_session_id") is None
    assert st.read_meta("ingest_wake_sent") in (None, False)
    assert (st.read_meta("verify_stall_beats") or 0) == 0


def test_prune_failed_green_is_failsoft(tmp_path):
    """We are already on a failure path — a prune/reap hiccup must NEVER raise."""
    root = "demo-gemini-pred"
    st = BgStateStore(str(tmp_path), root)
    st.write_state("PREWARMING", reason="x")

    def boom(*a, **k):
        raise RuntimeError("db down")

    # must not raise despite both injected effects throwing
    bg_beat.prune_failed_green(
        str(tmp_path), str(tmp_path), root, 2, f"{root}-g2",
        prune_fn=boom, reap_pane_fn=boom)
    # still reset to SOLO (state reset is best-effort but attempted)
    assert st.read()["state"] == "SOLO"
