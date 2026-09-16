"""Tests for the DURABLE BG-fire-notify belt folded into b_fire_watcher.py
(gm ask msg_42415974): on a bg_state PREWARM/SWAP transition of a BG-armed seat,
emit a durable notify to the CURRENT gm seat (+ Telegram) — daemon-level, so it
survives sessions (a session Monitor would miss a days-off fire)."""
import json

import b_fire_watcher as bfw


def test_bg_armed_seats_reads_bg_enabled_minus_global_disable(tmp_path):
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    (tmp_path / "second-brain-dev.bg_enabled").write_text("")
    assert bfw.bg_armed_seats(str(tmp_path)) == {"ios-watch-dev", "second-brain-dev"}
    # Global kill-switch present => nothing is armed (fleet-wide no-op).
    (tmp_path / "BG_DISABLED").write_text("kill")
    assert bfw.bg_armed_seats(str(tmp_path)) == set()


def test_bg_fire_signal_only_fires_on_prewarm_or_swap(tmp_path):
    p = tmp_path / "ios-watch-dev.bg.json"
    p.write_text(json.dumps({"state": "SOLO", "root": "ios-watch-dev", "history": []}))
    assert bfw.bg_fire_signal(str(p)) is None  # SOLO is not a fire

    p.write_text(json.dumps({"state": "PREWARMING", "root": "ios-watch-dev",
                             "history": [{"state": "SOLO"}, {"state": "PREWARMING", "reason": "ctx:0.80"}]}))
    sig = bfw.bg_fire_signal(str(p))
    assert sig is not None
    state, key = sig
    assert state == "PREWARMING"
    assert key  # a stable dedupe key for this transition


def test_bg_fire_signal_key_changes_across_transitions(tmp_path):
    p = tmp_path / "s.bg.json"
    p.write_text(json.dumps({"state": "PREWARMING", "root": "s",
                             "history": [{"state": "PREWARMING"}]}))
    k1 = bfw.bg_fire_signal(str(p))[1]
    p.write_text(json.dumps({"state": "SWAPPING", "root": "s",
                             "history": [{"state": "PREWARMING"}, {"state": "SWAPPING"}]}))
    k2 = bfw.bg_fire_signal(str(p))[1]
    assert k1 != k2  # a later fire transition must re-notify


def test_notify_gm_on_fire_is_idempotent_per_transition():
    calls = []
    marker = {}

    def fake_notify(seat, state):
        calls.append((seat, state))

    # first observation of this transition -> notify once
    bfw.notify_gm_on_fire("ios-watch-dev", "PREWARMING", "k1", notify_fn=fake_notify, marker=marker)
    bfw.notify_gm_on_fire("ios-watch-dev", "PREWARMING", "k1", notify_fn=fake_notify, marker=marker)
    assert calls == [("ios-watch-dev", "PREWARMING")]
    # a new transition key -> notify again
    bfw.notify_gm_on_fire("ios-watch-dev", "SWAPPING", "k2", notify_fn=fake_notify, marker=marker)
    assert calls == [("ios-watch-dev", "PREWARMING"), ("ios-watch-dev", "SWAPPING")]
    assert marker["bg:ios-watch-dev"] == "k2"
