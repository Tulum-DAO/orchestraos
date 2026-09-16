"""RED (DEC-1789517918317482 §3A + §3B obs fields, OB g42):
§3A  obs.state falls back to get_agent_status(seat) when collect's death.state is None
     (codex/gemini blues; collect stays claude-hook-scoped) — no runtime literal.
G2   state_age_s is OBSERVED idle: idle_since persisted in bg meta on first idle, cleared on
     any non-idle read, never inferred backwards. A collect-provided age is kept as is.
G3   blue_attached from tmux list-clients; unknown => True (fail-closed).
     blue_pending_cards from the approvals store (from_agent == root, pending); unknown => 0.
     blue_composer_text from the pane screen via composer_read; unknown => None.
     blue_turn_complete == (state == 'idle')."""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "fw-seat"


def _agent(death_state=None, death_age=None, runtime="codex"):
    d = {"exhausted": False, "court": False, "state": death_state}
    if death_age is not None:
        d["state_age_s"] = death_age
    return {"agent_id": ROOT, "runtime": runtime,
            "ctx": {"status_bar_pct": 30, "model": "m"}, "death": d}


def _blue():
    return {"generation": 1, "blue_generation_id": 11, "model": "m"}


def _obs(tmp_path, agent, now=1000.0, **kw):
    base = dict(status_fn=lambda r: {"state": "idle"},
                attached_fn=lambda r: False,
                pending_cards_fn=lambda r: 0,
                screen_fn=lambda r: ["› Ask Codex to do anything"],
                meta_store=BgStateStore(str(tmp_path), ROOT))
    base.update(kw)
    return bg_beat.build_obs(agent, _blue(), now=now, **base)


def test_state_falls_back_to_agent_status_when_death_state_absent(tmp_path):
    obs = _obs(tmp_path, _agent(None), status_fn=lambda r: {"state": "idle"})
    assert obs["state"] == "idle"
    assert obs["blue_turn_complete"] is True


def test_collect_state_wins_when_present(tmp_path):
    calls = []
    obs = _obs(tmp_path, _agent("busy", death_age=400),
               status_fn=lambda r: calls.append(r) or {"state": "idle"})
    assert obs["state"] == "busy" and obs["state_age_s"] == 400
    assert calls == []
    assert obs["blue_turn_complete"] is False


def test_idle_age_is_observed_across_beats_and_clears_on_non_idle(tmp_path):
    a = _agent(None)
    o1 = _obs(tmp_path, a, now=1000.0)
    assert o1["state_age_s"] == 0                       # first observed idle: age 0
    o2 = _obs(tmp_path, a, now=1070.0)
    assert o2["state_age_s"] == pytest.approx(70.0)
    o3 = _obs(tmp_path, a, now=1080.0, status_fn=lambda r: {"state": "working"})
    assert o3["state"] == "working" and o3["state_age_s"] == 0
    o4 = _obs(tmp_path, a, now=1100.0)
    assert o4["state_age_s"] == 0                       # restarted, never inferred backwards


def test_status_unknown_is_not_idle(tmp_path):
    obs = _obs(tmp_path, _agent(None), status_fn=lambda r: (_ for _ in ()).throw(OSError()))
    assert obs["state"] is None and obs["blue_turn_complete"] is False


def test_attached_and_unknown_attached_fail_closed(tmp_path):
    assert _obs(tmp_path, _agent(None), attached_fn=lambda r: True)["blue_attached"] is True
    assert _obs(tmp_path, _agent(None), attached_fn=lambda r: False)["blue_attached"] is False
    boom = lambda r: (_ for _ in ()).throw(RuntimeError("tmux"))  # noqa: E731
    assert _obs(tmp_path, _agent(None), attached_fn=boom)["blue_attached"] is True


def test_pending_cards_and_unknown_is_zero(tmp_path):
    assert _obs(tmp_path, _agent(None), pending_cards_fn=lambda r: 2)["blue_pending_cards"] == 2
    boom = lambda r: (_ for _ in ()).throw(RuntimeError("db"))  # noqa: E731
    assert _obs(tmp_path, _agent(None), pending_cards_fn=boom)["blue_pending_cards"] == 0


def test_composer_text_from_screen_and_unknown_is_none(tmp_path):
    assert _obs(tmp_path, _agent(None))["blue_composer_text"] == ""
    typed = lambda r: ["› deploy it"]  # noqa: E731
    assert _obs(tmp_path, _agent(None), screen_fn=typed)["blue_composer_text"] == "deploy it"
    boom = lambda r: (_ for _ in ()).throw(RuntimeError("tmux"))  # noqa: E731
    assert _obs(tmp_path, _agent(None), screen_fn=boom)["blue_composer_text"] is None


def test_legacy_call_without_probes_is_fail_closed_and_quiet():
    """Older callers/tests (no wal_dir, no injected probes) must not run live tmux/approvals
    probes; the new fields take their fail-closed defaults."""
    obs = bg_beat.build_obs(_agent("idle", death_age=10, runtime="claude"), _blue())
    assert obs["state"] == "idle" and obs["state_age_s"] == 10
    assert obs["blue_attached"] is True
    assert obs["blue_pending_cards"] == 0
    assert obs["blue_composer_text"] is None


# ---- gm msg_51be17ad (by effect 00:45Z): collect.py:168 fills death.state for EVERY runtime
# from the gathered status, so the §3A fallback never engaged for codex and G2's observed
# idle age only ran inside that fallback. collect's state_age_s for a codex/gemini seat is
# the deriver SNAPSHOT age (1-4 s), never observed idle -> idle_since was never persisted.
# Fix: observe idle on EVERY beat whatever the state source; state_age_s = max(collect age,
# observed age) so a real claude hook age is kept and a snapshot age is superseded.

def _codex_collected(state, age):
    return _agent(state, death_age=age, runtime="codex")


def test_observed_idle_accrues_when_collect_supplies_snapshot_state(tmp_path):
    calls = []
    kw = dict(status_fn=lambda r: calls.append(r) or {"state": "idle"})
    o1 = _obs(tmp_path, _codex_collected("idle", 2), now=1000.0, **kw)
    assert o1["state"] == "idle" and o1["blue_turn_complete"] is True
    assert o1["state_age_s"] == 2                      # first observed idle: snapshot age only
    o2 = _obs(tmp_path, _codex_collected("idle", 2), now=1900.0, **kw)
    assert o2["state_age_s"] == pytest.approx(900.0)   # observed idle dominates the snapshot
    assert calls == []                                  # collect state present: oracle not re-probed
    assert BgStateStore(str(tmp_path), ROOT).read_meta("idle_since") == 1000.0


def test_claude_hook_age_is_kept_when_larger_than_observed(tmp_path):
    o1 = _obs(tmp_path, _agent("idle", death_age=400, runtime="claude"), now=1000.0)
    assert o1["state_age_s"] == 400
    o2 = _obs(tmp_path, _agent("idle", death_age=410, runtime="claude"), now=1010.0)
    assert o2["state_age_s"] == 410                     # max(hook 410, observed 10)


def test_collect_non_idle_clears_observed_idle(tmp_path):
    _obs(tmp_path, _codex_collected("idle", 2), now=1000.0)
    o = _obs(tmp_path, _codex_collected("working", 0), now=1500.0)
    assert o["state"] == "working" and o["state_age_s"] == 0
    assert o["blue_turn_complete"] is False
    assert BgStateStore(str(tmp_path), ROOT).read_meta("idle_since") is None
