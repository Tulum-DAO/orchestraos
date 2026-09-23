"""RED (BG leg-(ii) arm-precondition (c) — close the produce_checkpoint_supervised
operator-attach TOCTOU).

(ii-B)'s wrapper checked the operator-attachment ONCE (at the wrapper) and, when the operator was absent,
passed the live-Blue inject into produce_checkpoint WITHOUT re-passing operator_attached_fn — so
if the operator attaches to Blue's pane in the window BETWEEN the wrapper check (T0) and the actual
inject (T1), the inject still fires (one nuisance pane-nudge into a seat the operator is on). gm arm-
precondition: re-pass operator_attached_fn down so produce_checkpoint RE-CHECKS just before
injecting (accepting a 2nd tmux list-clients).

Drives the REAL produce_checkpoint against a REAL WalStore with a flip-flop operator_attached_fn
(absent at T0, attached at T1) — the inject must NOT fire; it must fall back to (b).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import checkpoint_producer as cp  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "second-brain-dev"


def _seed(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    store.append(ts=100, lineage_root=ROOT, generation=2, sid="b", runtime="claude",
                 kind="prompt", summary="text (40 chars)", body_ref="t.jsonl:real",
                 source_path="x")
    return store, (lambda ref: {"t.jsonl:real": "Finish the TOCTOU fix"}.get(ref, ""))


def _fake_clock():
    t = [0.0]

    def now():
        t[0] += 0.6
        return t[0]
    return now


def test_operator_attaching_between_wrapper_check_and_inject_is_recaught(tmp_path):
    store, resolver = _seed(tmp_path)
    calls = {"operator": 0, "inject": 0}

    def operator_attached_fn():
        # absent at the wrapper's T0 check, ATTACHED by produce_checkpoint's T1 re-check
        calls["operator"] += 1
        return calls["operator"] >= 2

    def real_inject_fn(root):
        calls["inject"] += 1

    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=ROOT,
        real_inject_fn=real_inject_fn,
        alive_fn=lambda pid: True,          # Blue alive
        operator_attached_fn=operator_attached_fn,  # flip-flop: False then True
        resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)

    assert calls["operator"] >= 2, "produce_checkpoint must RE-CHECK the operator just before injecting"
    assert calls["inject"] == 0, \
        "TOCTOU: the operator attaching between the wrapper check and the inject must be re-caught"
    assert out["source"] == "wal-derived", "fell back to (b) once the re-check saw the operator"


def test_no_operator_still_injects_and_blue_authors(tmp_path):
    # regression: when the operator stays absent through BOTH checks, the inject still fires
    store, resolver = _seed(tmp_path)

    def inject_fn(root):
        cp.write_build_checkpoint(str(tmp_path), root,
                                  {"objective": "Blue authored", "source": "blue"})

    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=ROOT,
        real_inject_fn=inject_fn,
        alive_fn=lambda pid: True,
        operator_attached_fn=lambda: False,     # absent throughout
        resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)
    assert out["source"] == "blue"
