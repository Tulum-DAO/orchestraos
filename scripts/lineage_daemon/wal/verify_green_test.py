"""RED-first tests for verify_green.py — the WAL-probe shadow-verify seam (gm bar #4).

verify returns True ONLY when Green provably ingested Blue's WAL — it answers the
last-K events + working-set probe correctly (grade_probe, WAL = answer key). This is
BY EFFECT (recall proof), never a timed poll: a booted-but-wedged Green (no answer /
wrong answer) returns False and stays PREWARMING. gm bar #4 (LOSSLESS): a correct
answer == zero context/intent loss.

Green surfaces its answer via an injected `answer_fn` (in the drill: a file/WAL
boot-artifact Green writes). None => still warming => False (not raise).
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import verify_green  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.probe import _working_set  # noqa: E402


def _seed_wal(tmp_path, root, n=6):
    store = WalStore(str(tmp_path / f"{root}.db"))
    for i in range(n):
        kind = "file_mod" if i % 2 == 0 else "response"
        body = f"git:/cwd#path{i}.py" if kind == "file_mod" else None
        store.append(ts=1000 + i, lineage_root=root, generation=2, sid="sid-b",
                     runtime="claude", kind=kind, summary=f"event {i}",
                     body_ref=body, source_path="x")
    return store


def _correct_answer(store, root, k=5):
    """The answer a LOSSLESSLY-hydrated Green would give: exact last-K + working-set."""
    evs = [{"seq": r["seq"], "summary": r["summary"]}
           for r in store.events(root)[-k:]]
    return {"last_events": evs, "working_set": _working_set(store, root)}


def test_correct_probe_answer_verifies_true_lossless(tmp_path):
    """gm bar #4: a Green that answers the last-K + working-set correctly proves
    lossless ingest => verify True."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    answer = _correct_answer(store, root)
    ok = verify_green.verify_green(
        root, f"{root}-g-green", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: answer)
    assert ok is True


def test_no_answer_yet_is_false_not_raise(tmp_path):
    """Green still warming (no answer artifact) => False, NOT raise (stay PREWARMING)."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    ok = verify_green.verify_green(
        root, f"{root}-g-green", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: None)
    assert ok is False


def test_wrong_answer_is_false_booted_but_wedged(tmp_path):
    """A booted-but-wedged Green (pane exists, wrong/partial recall) => False.
    This is the premature-grade lesson: aliveness is NOT readiness."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    wrong = {"last_events": [{"seq": 1, "summary": "wrong"}], "working_set": []}
    ok = verify_green.verify_green(
        root, f"{root}-g-green", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: wrong)
    assert ok is False


def test_partial_working_set_is_false(tmp_path):
    """Correct events but a lossy working-set (dropped a touched path) => False
    (lossless bar: intent/working-state must be complete)."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    ans = _correct_answer(store, root)
    ans["working_set"] = ans["working_set"][:-1]   # drop one path = lossy
    ok = verify_green.verify_green(
        root, f"{root}-g-green", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: ans)
    assert ok is False


def test_verify_gates_the_swap_contract(tmp_path):
    """verify_green returns a plain bool that bg_arm uses to gate READY (and thus
    the swap): True only on a correct answer. This pins the gating contract —
    a False verify must never advance toward swap."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    assert verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: None) is False
    assert verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: _correct_answer(store, root)) is True


def test_verify_grades_only_the_delivered_scope(tmp_path):
    """Blue-authoritative scope (bar#4 gap-c): a mid-life green answers only the
    delivered delta. verify with the scope grades that slice -> True; full-WAL
    grading (no scope) false-fails the perfect mid-life green -> False."""
    from lineage_daemon.wal.probe import _working_set_from_events
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root, n=8)
    slc = [r for r in store.events(root) if 4 < r["seq"] <= 8]
    ans = {"last_events": [{"seq": r["seq"], "summary": r["summary"]} for r in slc],
           "working_set": _working_set_from_events(slc)}
    scope = {"since_seq": 4, "through_seq": 8}
    assert verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: ans, scope=scope) is True
    assert verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: ans) is False


# ---- Layer-2 green-liveness gate (DEC-1788655588): ANDed, fail-closed ----

def test_channel_passes_but_green_not_live_is_false(tmp_path):
    """The live-fire defect: the channel probe PASSES (orchestrator-produced) but the
    green agent is frozen pre-init -> live_fn False -> verify False (never promote a
    dead green)."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    ok = verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: _correct_answer(store, root),
        live_fn=lambda r, g: False)
    assert ok is False


def test_channel_passes_and_green_live_is_true(tmp_path):
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    ok = verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: _correct_answer(store, root),
        live_fn=lambda r, g: True)
    assert ok is True


def test_liveness_resolver_raises_fails_closed(tmp_path):
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    def boom(r, g):
        raise RuntimeError("tmux/transcript resolve blew up")
    ok = verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: _correct_answer(store, root), live_fn=boom)
    assert ok is False


def test_channel_fails_short_circuits_before_liveness(tmp_path):
    """A lossy channel answer is False regardless of liveness (AND; channel first)."""
    root = "bg-drill-victim"
    store = _seed_wal(tmp_path, root)
    called = []
    ok = verify_green.verify_green(
        root, "g", wal_dir=str(tmp_path), store=store,
        answer_fn=lambda r, a: {"last_events": [{"seq": 1, "summary": "wrong"}],
                                "working_set": []},
        live_fn=lambda r, g: called.append(1) or True)
    assert ok is False
    assert not called   # liveness not even consulted when the channel probe fails
