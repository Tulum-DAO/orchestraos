"""RED-first for probe_answer — computing the probe answer a PERFECT green produces.

The verify seam grades Green's probe answer (last-K WAL events + working-set) against
the WAL via grade_probe. Nothing yet PRODUCES that answer: the green never writes
state/agent-handoffs/<green>.probe.json, so verify stays False (the real gap gm's
shakedown surfaced at beat 2). This module computes the correct answer FROM the WAL —
dual-use:
  * DRILL (B, now): the harness injects it between beat1/beat2 so verify grade_probe
    runs FOR REAL and passes for a perfect green, unblocking swap/completion/reap
    validation off-watch. (Drill-injected != bar #4; A wires the real green side.)
  * REAL (A, next): the green computes its OWN answer from its ingested WAL using the
    SAME function and commits it — the true lossless proof.

compute_probe_answer MUST produce an answer grade_probe grades ok=True (it is THE
answer key), and write_probe_answer MUST land it at the exact path the verify seam
reads — proven THROUGH the real _Seams/bounded_call path, not a mock.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import probe_answer  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.probe import grade_probe  # noqa: E402
from lineage_daemon.wal import bg_beat  # noqa: E402


def _seed(wal_dir, root, n=6):
    store = WalStore(os.path.join(wal_dir, f"{root}.db"))
    for i in range(n):
        kind = "file_mod" if i % 2 == 0 else "response"
        body = f"git:/cwd#path{i}.py" if kind == "file_mod" else None
        store.append(ts=1000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind=kind, summary=f"e{i}",
                     body_ref=body, source_path="x")
    return store


def test_compute_probe_answer_grades_ok(tmp_path):
    """The computed answer is THE answer key — grade_probe must grade it ok=True."""
    root = "bg-drill-victim"
    store = _seed(str(tmp_path), root)
    answer = probe_answer.compute_probe_answer(store, root)
    grade = grade_probe(store, root, answer)
    assert grade["ok"] is True
    assert grade["last_events_ok"] is True
    assert grade["working_set_ok"] is True


def test_write_probe_answer_lands_at_the_verify_read_path(tmp_path):
    """write_probe_answer must write exactly where _default_probe_answer_fn reads:
    <orchestra_dir>/state/agent-handoffs/<green_alias>.probe.json."""
    root = "bg-drill-victim"
    green_alias = f"{root}-g2"
    (tmp_path / "state" / "agent-handoffs").mkdir(parents=True)
    (tmp_path / "state" / "wal").mkdir(parents=True)
    store = _seed(str(tmp_path / "state" / "wal"), root)

    path = probe_answer.write_probe_answer(
        str(tmp_path), root, green_alias, store)
    expected = os.path.join(str(tmp_path), "state", "agent-handoffs",
                            f"{green_alias}.probe.json")
    assert path == expected
    assert os.path.exists(expected)
    with open(expected) as fh:
        written = json.load(fh)
    assert "last_events" in written and "working_set" in written


def test_injected_answer_makes_real_verify_seam_return_true(tmp_path):
    """END-TO-END through the REAL _Seams (bounded_call worker): after drill-inject,
    the verify seam reads the file, grade_probe passes, verify -> True. This is what
    unblocks beat2 PREWARMING->READY in the shakedown."""
    root = "bg-drill-victim"
    green_alias = f"{root}-g2"
    (tmp_path / "state" / "agent-handoffs").mkdir(parents=True)
    wal_dir = tmp_path / "state" / "wal"
    wal_dir.mkdir(parents=True)
    store = _seed(str(wal_dir), root)

    blue = {"generation": 1, "model": "claude-opus-4-8[1m]"}
    spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn = bg_beat._live_effect_seams(
        root, str(tmp_path), str(wal_dir), blue, live_fn=lambda r, g: True)  # answer_fn defaults to the file reader
    seams = bg_beat._Seams(
        register_provisional=lambda *a, **k: None,
        project_now=lambda *a, **k: True, swap=lambda *a, **k: None,
        spawn_fn=spawn_fn, verify_fn=verify_fn, hydrate_fn=hydrate_fn,
        reap_fn=reap_fn, timeout_s=10.0)

    # BEFORE inject: no probe.json -> verify False (the surfaced gap)
    assert seams.verify(root, green_alias) is False
    # DRILL INJECT the correct answer from the real WAL
    probe_answer.write_probe_answer(str(tmp_path), root, green_alias, store)
    # AFTER inject: the real verify seam (through bounded_call) reads it -> True
    assert seams.verify(root, green_alias) is True


def test_empty_wal_answer_is_still_gradeable(tmp_path):
    """A green with an empty WAL produces an empty-but-valid answer (no crash);
    grade_probe grades it ok against an empty WAL (degenerate but coherent)."""
    root = "bg-drill-victim"
    (tmp_path / "state" / "wal").mkdir(parents=True)
    store = WalStore(os.path.join(str(tmp_path / "state" / "wal"), f"{root}.db"))
    answer = probe_answer.compute_probe_answer(store, root)
    assert answer["last_events"] == []
    assert answer["working_set"] == []
    assert grade_probe(store, root, answer)["ok"] is True
