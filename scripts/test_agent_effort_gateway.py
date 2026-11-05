"""GET/PUT /agent-model carry an agent's effort (scripts/agent_effort.py).

PUT {session, model, effort?}: Claude types `/effort <level>` into the idle pane after any model
switch; Codex picks the level in its own picker; a busy agent's pick (effort included) waits for
its turn to end; the chosen level is kept per agent, so the next spawn passes it too. A runtime
with no effort setting (Gemini, Grok), a headless seat whose supervisor predates effort, or a level
the runtime does not have is refused with nothing typed. A headless seat whose supervisor takes
set_effort gets the level through it (nothing typed, the host's settings never touched).
"""
import pytest

import scripts.agent_effort as AE
import scripts.agent_model as AM
import scripts.agent_model_codex as AMC
import scripts.watch_gateway as G
from scripts.test_agent_model import _Req, _body, gw  # noqa: F401  (fixture)
from scripts.test_gateway_scopes import _run

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
SID = "11111111-2222-4333-8444-555555555555"


class _Det:
    def __init__(self, state):
        self.state = state

    def get_agent_status(self, s):
        return {"state": self.state, "activity": "", "process": {}}


@pytest.fixture
def seat(gw, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MODEL_PENDING_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setenv("AGENT_EFFORT_FILE", str(tmp_path / "efforts.json"))
    det = _Det("idle")
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [SID])
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: None)
    gw["effort_live"] = []
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: (gw["effort_live"].append(kw["effort"]) or
                        (200, {"ok": True, "effort": kw["effort"], "switching": "switched"})))
    gw["det"], gw["efforts"] = det, tmp_path / "efforts.json"
    return gw


def _put(body, actor="app:ios"):
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", **body}, actor=actor)))
    return r.status, _body(r)


def _get():
    return _body(_run(G.handle_agent_model(_Req("GET", query={"session": "dev"}))))


def test_get_says_the_agents_effort_its_levels_and_when_a_pick_applies(seat):
    g = _get()
    assert g["effort"] is None and g["effort_levels"] == ["low", "medium", "high", "xhigh", "max"]
    assert g["effort_how"] == "live"
    AE.put(seat["efforts"], "dev", "high")
    assert _get()["effort"] == "high"


def test_an_effort_only_pick_types_effort_and_keeps_it_for_the_next_spawn(seat):
    status, b = _put({"model": OPUS, "effort": "high"})
    assert status == 200 and b["effort"] == "high" and b["effort_switching"] == "switched"
    assert seat["live"] == [], "the model it is on is not switched again"
    assert seat["effort_live"] == ["high"]
    assert AE.get(seat["efforts"], "dev") == "high"


def test_a_model_and_effort_pick_switches_the_model_first_then_the_effort(seat):
    status, b = _put({"model": SONNET, "effort": "low"})
    assert status == 200 and b["switching"] == "switched" and b["effort"] == "low"
    assert seat["live"] == [SONNET] and seat["effort_live"] == ["low"]
    assert seat["stored"] == [("dev", {"model": SONNET})] and AE.get(seat["efforts"], "dev") == "low"


def test_a_claude_level_is_typed_again_even_when_it_is_the_kept_one(seat):
    """The kept level is not proof of the session's level (a reapply may have failed): re-picking it
    types /effort again, which is cheap. (Codex reads its session's own level instead.)"""
    AE.put(seat["efforts"], "dev", "high")
    status, b = _put({"model": OPUS, "effort": "high"})
    assert status == 200 and seat["effort_live"] == ["high"] and seat["live"] == []


def test_a_reapply_that_finds_a_new_turn_is_a_warning_and_nothing_waits(seat, monkeypatch):
    """Model-only pick, kept level: /model went through, then a turn began before /effort. The
    model switch is reported as done (200) with the warning; nothing is left pending that would
    later be closed as 'switched' without typing."""
    AE.put(seat["efforts"], "dev", "high")
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: (409, {"ok": False, "error": "busy", "state": "working"}))
    status, b = _put({"model": SONNET})
    assert status == 200 and b["switching"] == "switched" and b["effort_not_set"] == "high"
    assert "effort_warning" in b and _get()["pending"] is None


def test_a_claude_model_switch_reapplies_the_agents_level_on_the_new_model(seat):
    """Claude keeps a level per model (modelSettings.<model>.effortLevel): after /model the session
    can be on another level than the one kept for the agent, so the kept level is typed again."""
    AE.put(seat["efforts"], "dev", "high")
    status, b = _put({"model": SONNET})
    assert status == 200 and seat["live"] == [SONNET] and seat["effort_live"] == ["high"]
    assert AE.get(seat["efforts"], "dev") == "high"


def test_no_kept_level_means_a_model_switch_types_no_effort(seat):
    assert _put({"model": SONNET})[0] == 200
    assert seat["effort_live"] == [] and AE.get(seat["efforts"], "dev") is None


def test_a_kept_level_that_fails_to_reapply_is_a_warning_not_a_failed_model_switch(seat, monkeypatch):
    AE.put(seat["efforts"], "dev", "high")
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: (502, {"ok": False, "error": "unconfirmed"}))
    status, b = _put({"model": SONNET})
    assert status == 200 and b["switching"] == "switched" and "effort_warning" in b


def test_the_same_level_after_a_model_change_is_still_typed(seat):
    AE.put(seat["efforts"], "dev", "high")
    assert _put({"model": SONNET, "effort": "high"})[0] == 200
    assert seat["effort_live"] == ["high"]


# --- a pick never drops the other half of a waiting one (review 1) -------------------------------

def test_an_effort_pick_keeps_the_model_switch_waiting(seat):
    seat["det"].state = "working"
    assert _put({"model": SONNET})[0] == 202
    # The composer sends the waiting model with the level...
    status, b = _put({"model": SONNET, "effort": "max"})
    assert status == 202 and _get()["pending"]["model"] == SONNET and _get()["pending"]["effort"] == "max"
    # ...and a client that sends the model it is on still keeps the waiting one.
    status, b = _put({"model": OPUS, "effort": "low"})
    assert status == 202 and b["model"] == SONNET
    assert _get()["pending"] == {**_get()["pending"], "model": SONNET, "effort": "low"}


def test_a_model_pick_keeps_the_effort_waiting(seat):
    seat["det"].state = "working"
    assert _put({"model": OPUS, "effort": "max"})[0] == 202
    status, b = _put({"model": SONNET})
    assert status == 202 and b["effort"] == "max"
    p = _get()["pending"]
    assert p["model"] == SONNET and p["effort"] == "max"
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == [("dev", "switched")]
    assert seat["live"] == [SONNET] and seat["effort_live"] == ["max"]


def test_picking_the_model_it_is_on_with_no_effort_cancels_everything_waiting(seat):
    """The explicit cancel stays reachable: the model it is on, and no effort in the request."""
    seat["det"].state = "working"
    assert _put({"model": OPUS, "effort": "max"})[0] == 202
    status, b = _put({"model": OPUS})
    assert status == 200 and b["changed"] is False and b["cancelled"] is True and _get()["pending"] is None
    assert _put({"model": SONNET, "effort": "max"})[0] == 202
    status, b = _put({"model": OPUS})
    assert status == 200 and b["cancelled"] is True and _get()["pending"] is None
    assert seat["live"] == [] and seat["effort_live"] == []


def test_an_idle_pick_takes_up_the_other_half_of_a_waiting_one(seat):
    seat["det"].state = "working"
    _put({"model": OPUS, "effort": "max"})
    seat["det"].state = "idle"
    assert _put({"model": SONNET})[0] == 200
    assert seat["live"] == [SONNET] and seat["effort_live"] == ["max"]
    assert _get()["pending"] is None


def test_a_model_switch_whose_effort_finds_a_new_turn_waits_with_the_NEW_model_as_current(seat, monkeypatch):
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: (409, {"ok": False, "error": "busy", "state": "working"}))
    status, b = _put({"model": SONNET, "effort": "low"})
    assert status == 202 and b["current"] == SONNET and b["model"] == SONNET and b["effort"] == "low"
    assert seat["live"] == [SONNET] and seat["stored"] == [("dev", {"model": SONNET})]
    assert _get()["pending"]["effort"] == "low"


def test_a_pick_waiting_on_a_seat_that_went_headless_is_closed_failed_with_nothing_typed(seat, monkeypatch):
    seat["det"].state = "working"
    assert _put({"model": OPUS, "effort": "max"})[0] == 202
    seat["det"].state = "idle"
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: "dev")
    _supervisor(monkeypatch, effort_control=False)
    out = G._apply_pending_model_switches()
    assert out and out[0][0] == "dev" and out[0][1].startswith("failed: a headless seat")
    assert seat["effort_live"] == [] and _get()["last_switch"]["outcome"] == "failed"


# --- Codex: the level the session is really on decides "no change" (review 4) --------------------

def _codex(monkeypatch, tmp_path, effort_in_rollout):
    import json
    roll = tmp_path / "rollout-2026-11-03T00-00-00-00000000-0000-7000-8000-000000000001.jsonl"
    roll.write_text(json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.6-luna", "effort": effort_in_rollout}}) + "\n")
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("cx", {}, "codex", "gpt-5.6-luna"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gpt-5.6-luna", "title": "L"}])
    monkeypatch.setattr(G, "_seat_codex_rollout", lambda s, m=None: roll)
    called = []
    monkeypatch.setattr(AMC, "switch_live", lambda **kw: called.append(kw.get("effort")) or
                        (200, {"ok": True, "model": kw["model"], "switching": "switched"}))
    return called


def test_a_codex_effort_pick_is_compared_with_the_rollouts_level_not_the_store(seat, monkeypatch, tmp_path):
    called = _codex(monkeypatch, tmp_path, "low")
    AE.put(seat["efforts"], "cx", "high")            # stale: the session is really on low
    assert _put({"model": "gpt-5.6-luna", "effort": "high"})[0] == 200 and called == ["high"]


def test_a_codex_level_the_session_already_runs_is_no_change(seat, monkeypatch, tmp_path):
    called = _codex(monkeypatch, tmp_path, "high")
    status, b = _put({"model": "gpt-5.6-luna", "effort": "high"})
    assert status == 200 and b["changed"] is False and called == []


def test_a_level_the_runtime_does_not_have_is_422_with_nothing_typed(seat, monkeypatch):
    status, b = _put({"model": OPUS, "effort": "turbo"})
    assert status == 422 and seat["effort_live"] == [] and seat["live"] == []
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("gx", {}, "gemini", "gemini-3.1-pro"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gemini-3.1-pro", "title": "G"}])
    status, b = _put({"model": "gemini-3.1-pro", "effort": "high"})
    assert status == 422 and seat["stored"] == []
    g = _get()
    assert g["effort_levels"] == [] and g["effort_how"] is None


def test_a_failed_effort_switch_after_a_good_model_switch_keeps_the_model_and_says_so(seat, monkeypatch):
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: (502, {"ok": False, "error": "unconfirmed"}))
    status, b = _put({"model": SONNET, "effort": "low"})
    assert status == 502 and b["error"] == "unconfirmed" and b["model_switched"] is True
    assert seat["stored"] == [("dev", {"model": SONNET})], "the model switch happened; the seat keeps it"
    assert AE.get(seat["efforts"], "dev") is None, "an effort that did not apply is not recorded"


def test_a_busy_agents_effort_waits_for_its_turn_with_the_model(seat):
    seat["det"].state = "working"
    status, b = _put({"model": OPUS, "effort": "max"})
    assert status == 202 and b["effort"] == "max" and seat["effort_live"] == []
    assert _get()["pending"]["effort"] == "max"
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == [("dev", "switched")]
    assert seat["effort_live"] == ["max"] and seat["live"] == []
    assert AE.get(seat["efforts"], "dev") == "max"


def test_a_codex_pick_passes_the_level_to_its_picker(seat, monkeypatch):
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("cx", {}, "codex", "gpt-5.6-luna"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "gpt-5.6-luna", "title": "L"},
                                                              {"id": "gpt-5.6-terra", "title": "T"}])
    monkeypatch.setattr(G, "_seat_codex_rollout", lambda s, m=None: None)
    called = []
    monkeypatch.setattr(AMC, "switch_live", lambda **kw: called.append((kw["model"], kw.get("effort"))) or
                        (200, {"ok": True, "model": kw["model"], "switching": "switched", "effort": kw.get("effort")}))
    status, b = _put({"model": "gpt-5.6-luna", "effort": "xhigh"})
    assert status == 200 and called == [("gpt-5.6-luna", "xhigh")] and b["effort"] == "xhigh"
    assert AE.get(seat["efforts"], "cx") == "xhigh" and seat["effort_live"] == []
    status, b = _put({"model": "gpt-5.6-luna", "effort": "max"})
    assert status == 422, "Codex has no max"


# --- headless seats: through the supervisor (scripts/seat_supervisor.py set_effort) ---------------

def _supervisor(monkeypatch, effort_control=True, replies=None, down=False):
    """A stand-in for the seat's supervisor socket: records each request, answers status (with or
    without effort_control: a supervisor started before effort support has none) and set_*."""
    import headless_switch as HS
    sent = []

    def request(seat, payload, **kw):
        sent.append(payload)
        if down:
            raise HS.SupervisorDown("no socket")
        if payload["cmd"] == "status":
            return {"ok": True, "state": "idle", **({"effort_control": True} if effort_control else {})}
        return (replies or {}).get(payload["cmd"], {"ok": True})
    monkeypatch.setattr(HS, "request", request)
    return sent


@pytest.fixture
def headless(seat, monkeypatch):
    monkeypatch.setattr(G, "_headless_seat_for", lambda s: "dev")
    monkeypatch.setattr(AE, "switch_effort_live", lambda **kw: pytest.fail("typed /effort into a headless seat"))
    monkeypatch.setattr(AM, "switch_live", lambda **kw: pytest.fail("typed /model into a headless seat"))
    return seat


def test_a_headless_seat_whose_supervisor_predates_effort_hides_it_and_refuses(headless, monkeypatch):
    sent = _supervisor(monkeypatch, effort_control=False)
    assert _get()["effort_how"] is None
    status, b = _put({"model": OPUS, "effort": "high"})
    assert status == 422 and "supervisor" in b["error"]
    assert [p["cmd"] for p in sent if p["cmd"] != "status"] == []
    assert AE.get(headless["efforts"], "dev") is None


def test_a_headless_seat_with_no_supervisor_running_hides_the_control(headless, monkeypatch):
    _supervisor(monkeypatch, down=True)
    assert _get()["effort_how"] is None


def test_a_headless_seat_takes_an_effort_through_its_supervisor_and_keeps_it(headless, monkeypatch):
    sent = _supervisor(monkeypatch, replies={"set_effort": {"ok": True, "effort": "high", "how": "apply_flag_settings"}})
    assert _get()["effort_how"] == "live"
    status, b = _put({"model": OPUS, "effort": "high"})
    assert status == 200 and b["effort"] == "high" and b["effort_switching"] == "switched" and b["headless"] is True
    assert [p for p in sent if p["cmd"] != "status"] == [{"cmd": "set_effort", "effort": "high"}]
    assert AE.get(headless["efforts"], "dev") == "high"


def test_a_headless_model_and_effort_pick_switches_the_model_then_the_effort(headless, monkeypatch):
    sent = _supervisor(monkeypatch, replies={"set_model": {"ok": True, "how": "set_model"},
                                             "set_effort": {"ok": True, "effort": "low", "how": "apply_flag_settings"}})
    status, b = _put({"model": SONNET, "effort": "low"})
    assert status == 200 and b["switching"] == "switched" and b["effort"] == "low"
    assert [p for p in sent if p["cmd"] != "status"] == [{"cmd": "set_model", "model": SONNET},
                                                         {"cmd": "set_effort", "effort": "low"}]
    assert headless["stored"] == [("dev", {"model": SONNET})] and AE.get(headless["efforts"], "dev") == "low"


def test_a_headless_model_switch_does_not_reapply_the_kept_level(headless, monkeypatch):
    """The supervisor's level is a session flag the CLI keeps across set_model (and every restart passes
    --effort), unlike a pane's per-model saved level: a model-only pick sends no set_effort."""
    AE.put(headless["efforts"], "dev", "high")
    sent = _supervisor(monkeypatch, replies={"set_model": {"ok": True, "how": "set_model"}})
    assert _put({"model": SONNET})[0] == 200
    assert [p["cmd"] for p in sent if p["cmd"] != "status"] == ["set_model"]


def test_a_busy_headless_seat_defers_the_effort_and_the_applier_sends_it(headless, monkeypatch):
    sent = _supervisor(monkeypatch, replies={"set_effort": {"ok": False, "reason": "busy", "state": "working"}})
    status, b = _put({"model": OPUS, "effort": "max"})
    assert status == 202 and _get()["pending"]["effort"] == "max"
    assert AE.get(headless["efforts"], "dev") is None
    _supervisor(monkeypatch, replies={"set_effort": {"ok": True, "effort": "max", "how": "apply_flag_settings"}})
    assert G._apply_pending_model_switches() == [("dev", "switched")]
    assert AE.get(headless["efforts"], "dev") == "max"


def test_a_headless_effort_the_cli_capped_is_a_failure_and_not_kept(headless, monkeypatch):
    _supervisor(monkeypatch, replies={"set_effort": {"ok": False, "reason": "another level", "now": "high"}})
    status, b = _put({"model": OPUS, "effort": "max"})
    assert status == 502 and b["ok"] is False and "high" in b["error"]
    assert AE.get(headless["efforts"], "dev") is None


def test_a_headless_effort_with_the_supervisor_gone_is_503(headless, monkeypatch):
    sent = _supervisor(monkeypatch)
    assert _get()["effort_how"] == "live"
    _supervisor(monkeypatch, down=True)
    status, b = _put({"model": OPUS, "effort": "low"})
    assert status in (422, 503) and AE.get(headless["efforts"], "dev") is None


def test_a_headless_effort_waits_for_the_next_start_on_a_call_and_is_kept(headless, monkeypatch):
    _supervisor(monkeypatch, replies={"set_effort": {"ok": True, "effort": "low", "how": "next-start",
                                                     "why": "on a call"}})
    status, b = _put({"model": OPUS, "effort": "low"})
    assert status == 200 and b["effort_switching"] == "next_start" and AE.get(headless["efforts"], "dev") == "low"
