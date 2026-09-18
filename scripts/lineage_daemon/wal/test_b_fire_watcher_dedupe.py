"""DEFECT (gm msg_96438e1d): a bg_fire_signal read whose latest history entry is a
NON-transition (state == the prior recorded state, e.g. 'DRAINED->DRAINED
reason=swap-complete') must NOT page gm. Only a REAL transition (last history entry's
state differs from the one before it) pages, and only once — a second identical read
(same bg.json, no new transition) must not re-page even if a new same-state history
entry was appended in between (the exact 'DRAINED->DRAINED' beat spam the defect names).
"""
import json
import os
import sys

sys.path.insert(0, "scripts")

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "b_fire_watcher", os.path.join("scripts", "b_fire_watcher.py"))
bfw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bfw)


def _write_bg_json(path, state, history):
    with open(path, "w") as f:
        json.dump({"state": state, "history": history}, f)


def test_non_transition_same_state_does_not_signal(tmp_path):
    """DRAINED->DRAINED (last two history entries share the same state) => no signal."""
    p = tmp_path / "second-brain-dev.bg.json"
    _write_bg_json(p, "DRAINED", [
        {"state": "DEGRADED", "reason": "verify-stall"},
        {"state": "DRAINED", "reason": "swap-complete"},
        {"state": "DRAINED", "reason": "swap-complete"},
    ])
    assert bfw.bg_fire_signal(str(p)) is None


def test_real_transition_signals(tmp_path):
    p = tmp_path / "ios-watch-dev.bg.json"
    _write_bg_json(p, "DRAINED", [
        {"state": "SWAPPING", "reason": "ctx-fastpath"},
        {"state": "DRAINED", "reason": "swap-complete"},
    ])
    sig = bfw.bg_fire_signal(str(p))
    assert sig is not None
    state, key = sig
    assert state == "DRAINED"


def test_first_history_entry_alone_is_a_real_transition(tmp_path):
    """A single history entry (freshly armed/first fire) with no prior state to compare
    against is still a real transition — never suppress the FIRST fire."""
    p = tmp_path / "codex-dev-1.bg.json"
    _write_bg_json(p, "PREWARMING", [{"state": "PREWARMING", "reason": "arm"}])
    sig = bfw.bg_fire_signal(str(p))
    assert sig is not None


def test_notify_gm_on_fire_pages_once_for_real_transition_not_twice(tmp_path):
    calls = []

    def fake_notify(seat, state):
        calls.append((seat, state))

    marker = {}
    p = tmp_path / "pm-acme.bg.json"
    _write_bg_json(p, "DRAINED", [
        {"state": "SWAPPING", "reason": "ctx-fastpath"},
        {"state": "DRAINED", "reason": "swap-complete"},
    ])
    sig = bfw.bg_fire_signal(str(p))
    state, key = sig
    fired = bfw.notify_gm_on_fire("pm-acme", state, key,
                                  notify_fn=fake_notify, marker=marker)
    assert fired is True
    assert calls == [("pm-acme", "DRAINED")]

    # second identical read of the SAME bg.json: still a real transition by content,
    # but already recorded in the marker -> must not re-page.
    sig2 = bfw.bg_fire_signal(str(p))
    state2, key2 = sig2
    fired2 = bfw.notify_gm_on_fire("pm-acme", state2, key2,
                                   notify_fn=fake_notify, marker=marker)
    assert fired2 is False
    assert calls == [("pm-acme", "DRAINED")]  # no second page


def test_non_transition_spam_never_pages_even_across_many_appends(tmp_path):
    """The exact defect shape: the beat re-appends DRAINED->DRAINED every tick — must
    never page, no matter how many times the history grows with the same state."""
    calls = []
    marker = {}
    p = tmp_path / "acme-mcp-builder.bg.json"
    history = [{"state": "SWAPPING", "reason": "ctx-fastpath"},
               {"state": "DRAINED", "reason": "swap-complete"}]
    _write_bg_json(p, "DRAINED", history)
    sig = bfw.bg_fire_signal(str(p))
    state, key = sig
    bfw.notify_gm_on_fire("acme-mcp-builder", state, key,
                          notify_fn=lambda s, st: calls.append((s, st)), marker=marker)
    assert len(calls) == 1

    for _ in range(5):
        history = history + [{"state": "DRAINED", "reason": "swap-complete"}]
        _write_bg_json(p, "DRAINED", history)
        sig = bfw.bg_fire_signal(str(p))
        assert sig is None  # non-transition: never even a candidate signal
    assert len(calls) == 1
