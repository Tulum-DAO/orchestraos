"""RED (BG leg-(ii) M1a, Part A — wire the build-checkpoint into the capsule).

build_continue_capsule already has the priority "checkpoint > directive-thread >
degraded" (continue_capsule.py), but hydrate_green calls it with NO checkpoint= — so
the checkpoint branch is DEAD and a green always resumes from the inferred directive
thread, never from an explicit blue-authored BUILD-CHECKPOINT. M1a Part A loads
state/agent-handoffs/{root}.build-checkpoint.json (the producer's output) and threads
it into build_continue_capsule so the delivered capsule is source="checkpoint".

Drives the REAL hydrate_green against a REAL WalStore + a REAL checkpoint file and
asserts the delivered capsule BY EFFECT.

RED until hydrate_green loads + threads the checkpoint.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import hydrate_green  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "second-brain-dev"


def _seed(tmp_path, n=3):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    for i in range(n):
        store.append(ts=1000 + i, lineage_root=ROOT, generation=2, sid="b",
                     runtime="claude", kind="response", summary=f"e{i}", source_path="x")
    return store


def _write_checkpoint(orch, obj, next_action=None):
    d = os.path.join(orch, "state", "agent-handoffs")
    os.makedirs(d, exist_ok=True)
    cp = {"objective": obj}
    if next_action:
        cp["next_action"] = next_action
    with open(os.path.join(d, f"{ROOT}.build-checkpoint.json"), "w") as fh:
        json.dump(cp, fh)


def test_hydrate_uses_checkpoint_when_present(tmp_path):
    store = _seed(tmp_path)
    _write_checkpoint(str(tmp_path), "Ship the M1a checkpoint producer",
                      next_action="Wire build_continue_capsule checkpoint=")
    captured = []
    hydrate_green.hydrate_green(
        ROOT, f"{ROOT}-g5", since_seq=None, wal_dir=str(tmp_path), store=store,
        orchestra_dir=str(tmp_path),
        deliver_fn=lambda r, a, t, iv=None, cap=None: captured.append(cap))
    cap = captured[-1]
    assert cap is not None
    assert cap["source"] == "checkpoint", f"M1a: capsule must be checkpoint-sourced, got {cap['source']!r}"
    assert cap["objective"] == "Ship the M1a checkpoint producer"
    assert cap["degraded"] is False


def test_hydrate_falls_back_to_directive_thread_when_no_checkpoint(tmp_path):
    # no checkpoint file -> legacy behavior (directive-thread or degraded), never crashes
    store = _seed(tmp_path)
    captured = []
    hydrate_green.hydrate_green(
        ROOT, f"{ROOT}-g5", since_seq=None, wal_dir=str(tmp_path), store=store,
        orchestra_dir=str(tmp_path),
        deliver_fn=lambda r, a, t, iv=None, cap=None: captured.append(cap))
    cap = captured[-1]
    assert cap is not None
    assert cap["source"] != "checkpoint"   # no checkpoint file => not checkpoint-sourced


def test_hydrate_rejects_lineage_mismatched_checkpoint(tmp_path):
    # Condition 3 (consumer guard): a checkpoint stamped for a DIFFERENT lineage is
    # stale/wrong -> load returns None -> capsule falls back (never ship it).
    import json as _json
    store = _seed(tmp_path)
    d = os.path.join(str(tmp_path), "state", "agent-handoffs")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{ROOT}.build-checkpoint.json"), "w") as fh:
        _json.dump({"objective": "for another lineage", "lineage_root": "SOME-OTHER-ROOT"}, fh)
    captured = []
    hydrate_green.hydrate_green(
        ROOT, f"{ROOT}-g5", since_seq=None, wal_dir=str(tmp_path), store=store,
        orchestra_dir=str(tmp_path),
        deliver_fn=lambda r, a, t, iv=None, cap=None: captured.append(cap))
    assert captured[-1]["source"] != "checkpoint", "a lineage-mismatched checkpoint must be rejected"
