"""RED-first for the cross-thread SQLite wall (real-fire wall #5, verify/hydrate lane).

The DB-touching seams (verify, hydrate) run INSIDE bounded_call's worker thread
(the v2-2 SeamTimeout bound). _live_effect_seams built ONE WalStore in the MAIN
thread at seam-bind time and closed over it, so the worker thread did a SQLite op on
a main-thread connection -> `ProgrammingError: SQLite objects created in a thread can
only be used in that same thread`. spawn passed (tmux-only, no SQLite); verify is the
first DB-touching seam under bounded_call, so first to hit it; hydrate would too.

The fake-seam suite mocked probe/store, so it never crossed the thread boundary — the
RED test MUST drive the seam THROUGH the real _Seams (bounded_call worker), NOT call
verify_green/hydrate_green directly on the main thread (that was exactly the mock gap).
"""
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402


def _seed_wal(wal_dir, root, n=4):
    store = WalStore(os.path.join(wal_dir, f"{root}.db"))
    for i in range(n):
        store.append(ts=1000 + i, lineage_root=root, generation=2, sid="b",
                     runtime="claude", kind="response", summary=f"e{i}",
                     source_path="x")
    return store


def _build_real_seams(wal_dir, root, *, answer=None):
    """Build the REAL _Seams via _live_effect_seams (main thread bind), with an
    injected answer_fn so verify has a deterministic input. This is the exact path
    bg_supervise_fleet uses — verify/hydrate run through bounded_call."""
    blue = {"generation": 1, "model": "claude-opus-4-8[1m]"}
    spawn_fn, verify_fn, hydrate_fn, reap_fn, produce_fn = bg_beat._live_effect_seams(
        root, "/nonexistent-orch", wal_dir, blue,
        answer_fn=(lambda r, g: answer), deliver_fn=lambda r, g, t, iv=None, cap=None: None, live_fn=lambda r, g: True)
    return bg_beat._Seams(
        register_provisional=lambda *a, **k: None,
        project_now=lambda *a, **k: True, swap=lambda *a, **k: None,
        spawn_fn=spawn_fn, verify_fn=verify_fn, hydrate_fn=hydrate_fn,
        reap_fn=reap_fn, timeout_s=10.0)


def test_verify_seam_through_bounded_call_no_cross_thread_error(tmp_path):
    """verify() goes through bounded_call (worker thread); it must NOT raise the
    cross-thread SQLite ProgrammingError. RED before the fix (shared main-thread
    WalStore), GREEN after (store opened inside the worker)."""
    root = "bg-drill-victim"
    _seed_wal(str(tmp_path), root)
    seams = _build_real_seams(str(tmp_path), root, answer=None)  # None => warming => False
    # Must return a bool (not raise ProgrammingError from the worker thread).
    result = seams.verify(root, f"{root}-g2")
    assert result is False   # no answer artifact => not ready, but NO cross-thread crash


def test_verify_seam_true_answer_through_bounded_call(tmp_path):
    """A correct probe answer through the worker thread resolves True — proves the
    DB read actually happens in the worker (not just that it didn't crash)."""
    root = "bg-drill-victim"
    store = _seed_wal(str(tmp_path), root)
    from lineage_daemon.wal.probe import _working_set
    answer = {"last_events": [{"seq": r["seq"], "summary": r["summary"]}
                              for r in store.events(root)[-5:]],
              "working_set": _working_set(store, root)}
    seams = _build_real_seams(str(tmp_path), root, answer=answer)
    assert seams.verify(root, f"{root}-g2") is True


def test_hydrate_seam_through_bounded_call_no_cross_thread_error(tmp_path):
    """hydrate() also runs through bounded_call and touches the WalStore — same
    cross-thread hazard; must not raise ProgrammingError."""
    root = "bg-drill-victim"
    _seed_wal(str(tmp_path), root)
    seams = _build_real_seams(str(tmp_path), root)
    out = seams.hydrate(root, f"{root}-g2", 0)   # delta hydrate in the worker thread
    assert isinstance(out, dict)
    assert out.get("count", 0) >= 1   # shipped the seeded delta, no cross-thread crash


def test_verify_stall_progress_fn_also_thread_safe(tmp_path):
    """The stall-bound progress_fn calls store.max_seq() and ALSO runs inside the
    bounded_call worker (via the wrapped verify) — it must use a worker-thread
    connection too, not the main-thread one."""
    root = "bg-drill-victim"
    _seed_wal(str(tmp_path), root)
    seams = _build_real_seams(str(tmp_path), root, answer=None)
    # First call: verify False (warming) -> progress_fn(store.max_seq()) runs in the
    # worker; must not raise cross-thread.
    assert seams.verify(root, f"{root}-g2") is False
