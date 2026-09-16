"""GOAL-FINDING #4 beat-wiring: the PREWARMING->READY gate WAKES the green to ingest and
advances to READY only on ingestion PROVED by effect (through_seq >= delivered H). v2.1
addendum DEC-1789425184694395. Injectable (green_wake_fn) — legacy path (None) unchanged."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "wake-seat"
GREEN = f"{ROOT}-g7"


class _Seams:
    def __init__(self, verify=True):
        self._verify = verify
        self.hydrated = 0

    def register_provisional(self, *a, **k): pass
    def project_now(self, root): return True
    def spawn(self, root, green_alias): pass
    def verify(self, root, green_alias): return self._verify
    def hydrate(self, root, green_alias, since_seq): self.hydrated += 1
    def produce(self, root, green_alias): pass
    def reap(self, root, blue): pass


def _obs():
    return {"root": ROOT, "runtime": "claude", "ctx_pct": 0.72, "death": "",
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "m"}}


def _arm(tmp_path, seams, *, green_wake_fn, ingested):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    st = BgStateStore(str(tmp_path), ROOT)
    st.write_state("PREWARMING", reason="setup")
    st.write_meta("last_hydrated_seq", 10)          # delivered hydrate H = 10
    return BgArm(str(tmp_path), ROOT, seams=seams, cutover_active=lambda: True,
                 blue_pane_pid_fn=lambda root: None,
                 blue_wal_event_count_fn=lambda: 1,
                 green_wake_fn=green_wake_fn,
                 green_ingested_seq_fn=lambda g: ingested)


def _state(tmp_path):
    return BgStateStore(str(tmp_path), ROOT).read()["state"]


def test_ready_when_woken_and_ingested_through_h(tmp_path):
    arm = _arm(tmp_path, _Seams(verify=True), green_wake_fn=lambda g: True, ingested=10)
    arm.beat(_obs())
    assert _state(tmp_path) == "READY"
    assert BgStateStore(str(tmp_path), ROOT).read()["history"][-1]["reason"] == "ingest-verified"


def test_not_ready_when_green_never_woke_loud(tmp_path):
    arm = _arm(tmp_path, _Seams(verify=True), green_wake_fn=lambda g: False, ingested=10)
    arm.beat(_obs())
    assert _state(tmp_path) == "PREWARMING", "an un-woken green must NOT reach READY"
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_wake_failed_alarm") is True


def test_not_ready_when_ingested_below_h(tmp_path):
    arm = _arm(tmp_path, _Seams(verify=True), green_wake_fn=lambda g: True, ingested=5)
    arm.beat(_obs())
    assert _state(tmp_path) == "PREWARMING", "ingested 5 < H 10 -> not verified -> not READY"


def test_hydrate_delivered_before_wake(tmp_path):
    seams = _Seams(verify=True)
    arm = _arm(tmp_path, seams, green_wake_fn=lambda g: True, ingested=10)
    arm.beat(_obs())
    assert seams.hydrated == 1, "the digest must be DELIVERED (hydrate) before the wake"


def test_legacy_path_unchanged_without_waker(tmp_path):
    """green_wake_fn=None => legacy verify->READY (no #4 gate), so existing behavior holds."""
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    BgStateStore(str(tmp_path), ROOT).write_state("PREWARMING", reason="setup")
    arm = BgArm(str(tmp_path), ROOT, seams=_Seams(verify=True), cutover_active=lambda: True,
                blue_pane_pid_fn=lambda root: None, blue_wal_event_count_fn=lambda: 1)
    arm.beat(_obs())
    assert _state(tmp_path) == "READY"
    assert BgStateStore(str(tmp_path), ROOT).read()["history"][-1]["reason"] == "shadow-verified"


def test_wake_failed_flag_clears_when_a_later_wake_succeeds(tmp_path):
    """gm msg_86bcc168 item 1(b): the alarm flag is an EPISODE, not a tombstone — a wake that
    later succeeds (e.g. after the stale-screen kick) must clear it so the pager stops."""
    calls = {"n": 0}
    def wake(g):
        calls["n"] += 1
        return calls["n"] > 1                    # first beat fails, second succeeds
    arm = _arm(tmp_path, _Seams(verify=True), green_wake_fn=wake, ingested=10)
    arm.beat(_obs())
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_wake_failed_alarm") is True
    arm.beat(_obs())
    assert _state(tmp_path) == "READY"
    assert BgStateStore(str(tmp_path), ROOT).read_meta("green_wake_failed_alarm") is False
