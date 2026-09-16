"""Stage C trigger-wiring — the Stop-hook completion-check ENTRY (primary trigger).

A Stop hook fires at the PREDECESSOR's own turn boundary — structurally immune to
the frozen-dead-pane false-fire class (a dead pane has no turn boundary -> never
fires; the daemon beat scans it forever). This entry is a THIN caller: read the
hook JSON -> resolve the predecessor agent-id from its session_id -> dispatch DRY
(armed=False by default; arming the card live is a separate the operator go).

HARD CONTRACT (mirrors rotation-self-trigger.js / state-event-hook): ALWAYS exit 0,
never wedge a turn; any error is swallowed. The check is READ-ONLY here.
"""
import json

from scripts.lineage_daemon import graduation_stop_hook as H


def _readers(**kw):
    from scripts.lineage_daemon.graduation_dispatch_test import _readers as R
    return R(**kw)


def test_no_session_id_is_silent_noop():
    out = H.run_stop_hook({}, resolve_pred=lambda sid: "x-gen2",
                          readers_for=lambda pred: _readers())
    assert out["status"] == "noop:no-session"


def test_unresolvable_predecessor_is_silent_noop():
    out = H.run_stop_hook({"session_id": "sid-x"},
                          resolve_pred=lambda sid: None,
                          readers_for=lambda pred: _readers())
    assert out["status"] == "noop:not-a-lineage-predecessor"


def test_dispatches_dry_on_graded_pass_predecessor():
    out = H.run_stop_hook(
        {"session_id": "sid-x"},
        resolve_pred=lambda sid: "x-gen2",
        readers_for=lambda pred: _readers(grade="PASS"),
        disabled_path="/none")
    # DRY by default: reports the plan (await_card) + would-emit, no live effect.
    assert out["status"] == "dispatched"
    assert out["dispatch"]["action"] == "await_card"
    assert out["dispatch"]["emitted"] == {"would_emit": True}


def test_keep_when_grade_pending():
    out = H.run_stop_hook(
        {"session_id": "sid-x"},
        resolve_pred=lambda sid: "x-gen2",
        readers_for=lambda pred: _readers(grade=None),
        disabled_path="/none")
    assert out["dispatch"]["action"] == "keep"


def test_never_raises_swallows_reader_error():
    def _boom(pred):
        raise RuntimeError("reader exploded")
    out = H.run_stop_hook({"session_id": "sid-x"},
                          resolve_pred=lambda sid: "x-gen2",
                          readers_for=_boom)
    assert out["status"].startswith("error")     # recorded, not raised


def test_main_parses_stdin_and_exits_zero(capsys):
    payload = json.dumps({"session_id": None})
    rc = H.main(stdin_text=payload,
                resolve_pred=lambda sid: None,
                readers_for=lambda pred: _readers())
    assert rc == 0                               # ALWAYS exit 0, never wedge a turn


# ---- executor wiring (task #6): the production path passes REAL executors -----

def test_run_stop_hook_defaults_to_live_executors(monkeypatch):
    # With no explicit executors, run_stop_hook binds the live executor dict so an
    # armed dispatch has REAL seams (today it passed None -> would crash).
    captured = {}

    def _fake_dispatch(pred, readers, **kw):
        captured.update(kw)
        return {"action": "keep", "reason": "stub"}
    monkeypatch.setattr(H.GD, "dispatch_graduation", _fake_dispatch)

    marker = {"kill_gate1_fn": "K", "promote_fn": "P", "retire_fn": "R",
              "create_fn": "C", "notify_fn": "N", "skip_notify_fn": "S"}
    monkeypatch.setattr(H, "_live_executors", lambda: marker)

    out = H.run_stop_hook({"session_id": "sid-x"},
                          resolve_pred=lambda sid: "x-gen2",
                          readers_for=lambda pred: _readers(grade="PASS"),
                          disabled_path="/none")
    assert out["status"] == "dispatched"
    # every real executor threaded into dispatch on the production path
    for k, v in marker.items():
        assert captured[k] == v


def test_explicit_executors_override_the_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(H.GD, "dispatch_graduation",
                        lambda pred, readers, **kw: captured.update(kw) or
                        {"action": "keep", "reason": "stub"})
    # If _live_executors is called it would explode — proving it is NOT called when
    # explicit executors are supplied.
    def _boom():
        raise AssertionError("live executors must not be built when injected")
    monkeypatch.setattr(H, "_live_executors", _boom)

    ex = {"kill_gate1_fn": "kg", "promote_fn": "pf", "retire_fn": "rf",
          "create_fn": "cf", "notify_fn": "nf", "skip_notify_fn": "sf"}
    out = H.run_stop_hook({"session_id": "sid-x"},
                          resolve_pred=lambda sid: "x-gen2",
                          readers_for=lambda pred: _readers(grade="PASS"),
                          disabled_path="/none", executors=ex)
    assert out["status"] == "dispatched"
    assert captured["promote_fn"] == "pf"


def test_live_executor_build_failure_is_swallowed(monkeypatch):
    # HARD CONTRACT: if building the live executors raises, run_stop_hook must NOT
    # wedge — it degrades to a no-executor dispatch (which still runs DRY safely).
    monkeypatch.setattr(H, "_live_executors",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    out = H.run_stop_hook({"session_id": "sid-x"},
                          resolve_pred=lambda sid: "x-gen2",
                          readers_for=lambda pred: _readers(grade=None),
                          disabled_path="/none")
    # grade pending -> keep; the point is it did not raise despite the build failure
    assert out["status"] == "dispatched"
    assert out["dispatch"]["action"] == "keep"


def test_live_executors_returns_six_seams():
    ex = H._live_executors()
    assert set(ex) == {"kill_gate1_fn", "promote_fn", "retire_fn",
                       "create_fn", "notify_fn", "skip_notify_fn"}
    assert all(callable(v) for v in ex.values())


def test_main_production_path_stays_dry_and_exits_zero(monkeypatch, capsys):
    # End-to-end main() with live resolvers + live executors, but a session that
    # resolves to no predecessor -> silent noop. Proves main() wires executors on
    # the production path AND still ALWAYS exits 0 (armed stays False -> DRY).
    monkeypatch.setattr(
        "scripts.lineage_daemon.graduation_resolvers.live_resolvers",
        lambda: ((lambda sid: None), (lambda pred: _readers())))
    rc = H.main(stdin_text=json.dumps({"session_id": "sid-x"}))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "noop:not-a-lineage-predecessor"
