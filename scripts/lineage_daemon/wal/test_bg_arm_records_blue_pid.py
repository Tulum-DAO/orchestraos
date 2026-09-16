"""RED (BG leg-(ii) P0.1 — bg_arm records blue's live pane pid AT PREWARM).

The immutable binding P0.1 reaps by must be captured while blue is still the live
canonical — i.e. at the prewarm beat, before any swap renames/repoints anything (mirrors
spawn_green's green pane-pid capture). bg_arm persists it to BgStateStore meta
`blue_pane_pid`; the reap seam later reads it. The resolver is dependency-injected so this
is testable without a live pane, and the capture is FAIL-SAFE (a resolver hiccup must
never abort the beat).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402

ROOT = "ios-watch-dev"


class _Seams:
    def __init__(self):
        self.calls = []

    def spawn(self, root, green_alias):
        self.calls.append("spawn")
        return {"alias": green_alias, "pid": 4242, "detached": True}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        self.calls.append("register_provisional")

    def project_now(self, root):
        self.calls.append("project_now")

    def verify(self, root, green_alias):
        self.calls.append("verify")
        return True

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append("hydrate")

    def produce(self, root, green_alias):
        self.calls.append("produce")

    def swap(self, root, green, blue_generation_id):
        return type("O", (), {"status": "complete"})()

    def reap(self, root, blue):
        self.calls.append("reap")


def _obs(ctx_pct=0.72):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": None,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def _arm(tmp_path, seams, blue_pane_pid_fn):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True,
                 blue_pane_pid_fn=blue_pane_pid_fn)


def test_prewarm_records_blue_pane_pid_to_meta(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams, blue_pane_pid_fn=lambda root: 3711009)
    arm.beat(_obs(ctx_pct=0.72))            # SOLO -> PREWARMING
    assert BgStateStore(str(tmp_path), ROOT).read_meta("blue_pane_pid") == 3711009, \
        "P0.1: blue's live pane pid must be captured at prewarm (the reap binding)"


def test_record_is_fail_safe_when_resolver_returns_none(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams, blue_pane_pid_fn=lambda root: None)
    arm.beat(_obs(ctx_pct=0.72))            # must NOT raise; prewarm still advances
    st = BgStateStore(str(tmp_path), ROOT)
    assert st.read()["state"] == "PREWARMING"
    assert st.read_meta("blue_pane_pid") is None   # nothing recorded, no crash


def test_record_is_fail_safe_when_resolver_raises(tmp_path):
    seams = _Seams()

    def _boom(root):
        raise RuntimeError("tmux hiccup")

    arm = _arm(tmp_path, seams, blue_pane_pid_fn=_boom)
    arm.beat(_obs(ctx_pct=0.72))            # a resolver raise must NEVER abort the beat
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "PREWARMING"
