"""A busy agent's model switch is DEFERRED, never typed into its turn (the operator: "switching a
busy agent is fine"; gm ruling 2026-10-22; contract agreed with ios-watch-dev).

PUT /agent-model on a working agent -> 202 pending, nothing typed; the gateway applies it the
moment the agent is idle, through the same locked path; a newer pick replaces it, the current
model cancels it; every applied or failed pick lands in `last_switch` with its reason.
"""
import pytest

import scripts.agent_model as AM
import scripts.agent_model_pending as AMP
import scripts.watch_gateway as G
from scripts.test_agent_model import _Req, _body, gw  # noqa: F401  (fixture)
from scripts.test_gateway_scopes import _run

SONNET, OPUS, HAIKU = "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"
SID = "11111111-2222-4333-8444-555555555555"


class _Det:
    def __init__(self, state):
        self.state = state

    def get_agent_status(self, s):
        return {"state": self.state, "activity": "", "process": {}}


@pytest.fixture
def seat(gw, monkeypatch, tmp_path):
    """The gw fixture's seat 'dev' (claude, on opus), a fenced pending file, a settable state."""
    monkeypatch.setenv("AGENT_MODEL_PENDING_FILE", str(tmp_path / "pending.json"))
    det = _Det("working")
    monkeypatch.setattr(G, "_agent_status", lambda: det)
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [SID])
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [
        {"id": OPUS, "title": "Opus 5.5"}, {"id": SONNET, "title": "Sonnet 5.5"}, {"id": HAIKU, "title": "Haiku"}])
    gw["det"], gw["file"] = det, tmp_path / "pending.json"
    return gw


def _put(model, actor="app:ios"):
    r = _run(G.handle_agent_model(_Req("PUT", {"session": "dev", "model": model}, actor=actor)))
    return r.status, _body(r)


def _get():
    return _body(_run(G.handle_agent_model(_Req("GET", query={"session": "dev"}))))


def test_a_busy_agent_gets_202_pending_and_nothing_is_typed(seat):
    status, b = _put(SONNET)
    assert status == 202
    assert b["status"] == "pending" and b["applies"] == "at turn end"
    assert b["model"] == SONNET and b["current"] == OPUS and b["pending_since"]
    assert seat["live"] == [] and seat["stored"] == []            # nothing typed, nothing stored yet
    g = _get()
    assert g["pending"] == {"model": SONNET, "since": b["pending_since"], "stale": False}
    assert g["last_switch"] is None


def test_a_stalled_agent_is_deferred_too(seat):
    seat["det"].state = "stalled"
    assert _put(SONNET)[0] == 202 and seat["live"] == []


def test_a_newer_pick_replaces_the_pending_one(seat):
    _put(SONNET)
    status, b = _put(HAIKU)
    assert status == 202 and _get()["pending"]["model"] == HAIKU


def test_picking_the_current_model_cancels_the_pending_switch(seat):
    _put(SONNET)
    status, b = _put(OPUS)
    assert status == 200 and b["changed"] is False and b["cancelled"] is True and b["pending"] is None
    assert _get()["pending"] is None and seat["live"] == []
    status, b = _put(OPUS)                                         # nothing pending: today's answer
    assert status == 200 and "cancelled" not in b


def test_it_applies_when_the_agent_goes_idle_through_the_normal_path(seat):
    _put(SONNET)
    assert G._apply_pending_model_switches() == []                 # still working: waits
    assert seat["live"] == []
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == [("dev", "switched")]
    assert seat["live"] == [SONNET] and seat["stored"] == [("dev", {"model": SONNET})]
    g = _get()
    assert g["pending"] is None
    assert g["last_switch"]["model"] == SONNET and g["last_switch"]["outcome"] == "switched"


def test_a_turn_that_starts_first_keeps_it_pending(seat, monkeypatch):
    _put(SONNET)
    seat["det"].state = "idle"
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (409, {"ok": False, "error": "busy", "state": "working"}))
    assert G._apply_pending_model_switches() == []
    assert _get()["pending"]["model"] == SONNET


def test_a_failed_apply_is_closed_with_its_reason_never_silently(seat, monkeypatch):
    _put(SONNET)
    seat["det"].state = "idle"
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (502, {"ok": False, "error": "unconfirmable",
                                                              "detail": "no confirming line"}))
    assert G._apply_pending_model_switches() == [("dev", "failed: no confirming line")]
    g = _get()
    assert g["pending"] is None
    assert g["last_switch"] == {"model": SONNET, "at": g["last_switch"]["at"], "outcome": "failed",
                                "reason": "no confirming line"}


def test_a_rotation_before_the_turn_ends_closes_the_pick_with_a_reason(seat, monkeypatch):
    _put(SONNET)
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: ["99999999-2222-4333-8444-555555555555"])
    seat["det"].state = "idle"
    out = G._apply_pending_model_switches()
    assert out == [("dev", "failed: the agent moved to a new session before its turn ended")]
    assert seat["live"] == [] and _get()["last_switch"]["outcome"] == "failed"


def test_an_agent_that_is_gone_closes_the_pick(seat, monkeypatch):
    _put(SONNET)
    monkeypatch.setattr(G, "_tmux_session_names", lambda: set())
    assert G._apply_pending_model_switches() == [("dev", "failed: the agent is no longer running")]


def test_a_stale_switch_is_reported_once_and_kept(seat, monkeypatch):
    _, b = _put(SONNET)
    said = []
    monkeypatch.setattr(G, "_report_stale_model_switch", lambda s, e: said.append((s, e["model"])))
    later = AMP.get(AMP.state_path(G.ORCH_DIR), "dev")["since_ts"] + AMP.STALE_AFTER_S + 1
    G._apply_pending_model_switches(now=later)
    G._apply_pending_model_switches(now=later + 60)
    assert said == [("dev", SONNET)]                               # once
    assert AMP.view(AMP.get(AMP.state_path(G.ORCH_DIR), "dev"), now=later)["stale"] is True


def test_an_idle_switch_that_screen_finds_mid_turn_is_deferred(seat, monkeypatch):
    """The detector said idle, the idle-only send saw a turn on screen: 409 busy -> 202 pending."""
    seat["det"].state = "idle"
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (409, {"ok": False, "error": "busy", "state": "working"}))
    status, b = _put(SONNET)
    assert status == 202 and b["status"] == "pending"


def test_an_idle_switch_that_happens_drops_an_older_pending_pick(seat):
    _put(HAIKU)                                                    # busy: pending haiku
    seat["det"].state = "idle"
    status, b = _put(SONNET)                                       # idle now: switched at once
    assert status == 200 and b["switching"] == "switched"
    assert _get()["pending"] is None                               # haiku would have undone it


def test_a_runtime_that_switches_at_rotation_is_never_pending(seat, monkeypatch):
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("gm", {}, "gemini", "g-1"))
    monkeypatch.setattr(AM, "load_catalog", lambda rt, p, st: [{"id": "g-1", "title": "a"}, {"id": "g-2", "title": "b"}])
    status, b = _put("g-2", actor="operator:web")
    assert status == 200 and b["switching"] == "next_rotation" and _get()["pending"] is None


def test_the_pending_file_survives_a_restart_and_a_replaced_pick_is_not_closed(tmp_path):
    f = tmp_path / "p.json"
    first = AMP.put(f, "s", "a", now=100.0)
    AMP.put(f, "s", "b", now=200.0)                                # replaced before the apply finished
    assert AMP.resolve(f, "s", first["since"], "switched") is False
    assert AMP.get(f, "s")["model"] == "b"                         # read back from disk
    assert AMP.cancel(f, "s")["model"] == "b" and AMP.get(f, "s") is None


def test_the_test_run_never_writes_the_live_pending_file():
    import os
    assert os.environ.get("AGENT_MODEL_PENDING_FILE"), "conftest fences the pending file"
    assert "orchestra-test-data-" in os.environ["AGENT_MODEL_PENDING_FILE"] or "pytest" in os.environ["AGENT_MODEL_PENDING_FILE"]


def test_the_gateway_runs_the_applier_and_a_shutdown_ends_it_promptly(tmp_path, monkeypatch):
    """A real app run: the loop starts with the app, applies a pending pick of an idle agent, and
    shutdown ends it in about a second (the restart must never wait on it, see #465)."""
    import asyncio
    import time
    from aiohttp.test_utils import TestServer
    from scripts.test_events_shutdown import _NoStore
    monkeypatch.setenv("AGENT_MODEL_PENDING_FILE", str(tmp_path / "pending.json"))
    monkeypatch.setattr(G, "ApprovalStore", _NoStore)
    applied = []
    import threading
    monkeypatch.setattr(G, "_apply_pending_model_switches",
                        lambda now=None: applied.append(threading.current_thread() is threading.main_thread()) or [])
    AMP.put(AMP.state_path(G.ORCH_DIR), "dev", SONNET)

    async def go():
        server = TestServer(G.build_app())
        await server.start_server()
        for _ in range(50):
            if applied:
                break
            await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await asyncio.wait_for(server.close(), timeout=10)
        return time.monotonic() - t0
    took = asyncio.run(go())
    assert applied, "the loop ran a pass while a pick was pending"
    assert not any(applied), "every pass, the record read included, runs off the event loop"
    assert took < 3


# ------------------------------------------------------------------ codex R1 on #475
def test_a_pick_cancelled_after_the_pass_listed_it_is_never_typed(seat, monkeypatch):
    """The pass lists the pending picks, then the operator cancels one before its turn: the apply
    re-reads it under the session's switch lock and types nothing."""
    _put(SONNET)
    listed = dict(AMP.pending_sessions(AMP.state_path(G.ORCH_DIR)))
    _put(OPUS)                                                     # cancel
    monkeypatch.setattr(AMP, "pending_sessions", lambda f: listed)
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == []
    assert seat["live"] == []


def test_a_pick_replaced_after_the_pass_listed_it_applies_the_NEW_pick(seat, monkeypatch):
    _put(SONNET)
    listed = dict(AMP.pending_sessions(AMP.state_path(G.ORCH_DIR)))
    _put(HAIKU)                                                    # replace while still busy
    monkeypatch.setattr(AMP, "pending_sessions", lambda f: listed)
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == [("dev", "switched")]
    assert seat["live"] == [HAIKU]


def test_a_put_that_arrives_mid_apply_waits_and_then_decides_on_the_fresh_model(seat, monkeypatch):
    """A real concurrent PUT while the applier is typing the switch: it blocks on the agent's switch
    lock, and once the apply is done it reads the seat afresh: the model it asked for is already
    the agent's, so it changes nothing and types nothing (codex R2 on #475)."""
    import threading
    import time
    model = {"now": OPUS}
    monkeypatch.setattr(G, "_seat_for_session", lambda s: ("dev", {}, "claude", model["now"]))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def switch(**kw):
        calls.append(kw["model"])
        entered.set()
        release.wait(5)
        model["now"] = kw["model"]
        return 200, {"ok": True, "model": kw["model"], "switching": "switched"}
    monkeypatch.setattr(AM, "switch_live", switch)
    _put(SONNET)                                                   # busy: pending
    seat["det"].state = "idle"
    applier = threading.Thread(target=G._apply_pending_model_switches)
    applier.start()
    assert entered.wait(5)                                         # the apply is typing now
    got = {}
    put = threading.Thread(target=lambda: got.update(r=G._put_model("dev", SONNET, AM.load_catalog("claude", None, None), "app:ios")))
    put.start()
    time.sleep(0.3)
    assert put.is_alive() and not got                              # blocked on the switch lock
    release.set()
    applier.join(5)
    put.join(5)
    status, body = got["r"]
    assert status == 200 and body["changed"] is False              # fresh read: already sonnet
    assert calls == [SONNET]                                       # typed once, by the applier


def test_a_busy_agent_whose_identity_is_unknown_is_refused_not_deferred(seat, monkeypatch):
    """No session id to tell a rotation apart: today's 409, nothing recorded, nothing typed."""
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [])
    monkeypatch.setattr(AM, "switch_live", lambda **kw: (409, {"ok": False, "error": "busy", "state": "working"}))
    status, b = _put(SONNET)
    assert status == 409 and _get()["pending"] is None


def test_an_apply_never_runs_blind_when_the_identity_cannot_be_read(seat, monkeypatch):
    _put(SONNET)
    seat["det"].state = "idle"
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [])
    assert G._apply_pending_model_switches() == []
    assert seat["live"] == [] and _get()["pending"]["model"] == SONNET   # waits; stale report covers it


def test_mixed_identity_evidence_is_a_new_session_not_a_match(seat, monkeypatch):
    """A stale pane event next to a rotated seat's new id overlaps the recorded identity: it must
    be EXACTLY the recorded identity, or the pick is closed (codex R2 on #475)."""
    _put(SONNET)                                                   # recorded identity: {SID}
    new_sid = "99999999-2222-4333-8444-555555555555"
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [SID, new_sid])
    seat["det"].state = "idle"
    assert G._apply_pending_model_switches() == [("dev", "failed: the agent moved to a new session before its turn ended")]
    assert seat["live"] == []


def test_an_unreadable_record_is_never_overwritten(seat):
    """A corrupt record is not "nothing pending": a PUT refuses (503) and leaves it as it is, a
    pass changes nothing, and GET says the record cannot be read."""
    f = AMP.state_path(G.ORCH_DIR)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{ not json")
    status, b = _put(SONNET)
    assert status == 503 and seat["live"] == []
    assert f.read_text() == "{ not json"
    import pytest as _pt
    with _pt.raises(AMP.StateUnreadable):
        G._apply_pending_model_switches()
    assert f.read_text() == "{ not json"
    g = _get()
    assert g["pending"] is None and "pending_error" in g


@pytest.mark.parametrize("bad", [
    '{"pending": {"dev": null}, "last": {}}',
    '{"pending": {"dev": {"model": "x"}}, "last": {}}',
    '{"pending": {"dev": {"model": "x", "since": "t", "since_ts": 1, "sids": "abc"}}, "last": {}}',
    '{"pending": {}, "last": {"dev": {"model": "x", "at": "t", "outcome": "maybe"}}}',
    '{"pending": {"dev": {"model": "x", "since": "t", "since_ts": 1e9999, "sids": []}}, "last": {}}',
    '{"pending": {"dev": {"model": "x", "since": "t", "since_ts": 1, "sids": [], "reported": "yes"}}, "last": {}}',
    '{"pending": {"dev": {"model": "x", "since": "t", "since_ts": 1, "sids": [], "by": 7}}, "last": {}}',
    '{"pending": {}, "last": {"dev": {"model": "x", "at": "t", "outcome": "failed", "reason": {}}}}',
])
def test_a_malformed_ENTRY_is_a_damaged_record_too(seat, bad):
    """Valid JSON with a broken entry: refused like a corrupt file, and left as it is (codex R3)."""
    f = AMP.state_path(G.ORCH_DIR)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(bad)
    status, _ = _put(SONNET)
    assert status == 503 and f.read_text() == bad and seat["live"] == []


def test_a_pick_waiting_on_an_unreadable_identity_is_still_reported_once(seat, monkeypatch):
    _put(SONNET)
    monkeypatch.setattr(G, "_seat_session_ids", lambda s, meta=None: [])
    seat["det"].state = "idle"
    said = []
    monkeypatch.setattr(G, "_report_stale_model_switch", lambda s, e: said.append(s))
    later = AMP.get(AMP.state_path(G.ORCH_DIR), "dev")["since_ts"] + AMP.STALE_AFTER_S + 1
    G._apply_pending_model_switches(now=later)
    G._apply_pending_model_switches(now=later + 60)
    assert said == ["dev"] and seat["live"] == []


def test_a_pick_stamped_at_epoch_zero_is_stale_not_fresh():
    assert AMP.view({"model": "x", "since": "t", "since_ts": 0}, now=AMP.STALE_AFTER_S + 1)["stale"] is True


def test_no_record_read_runs_on_the_event_loop(seat, monkeypatch):
    """GET /agent-model and the 5 s pass read the record off the event loop: a stalled
    filesystem never stalls the gateway (codex R6 on #475)."""
    import threading
    where = []
    real_get = AMP.get
    monkeypatch.setattr(AMP, "get", lambda f, s: where.append(threading.current_thread() is threading.main_thread()) or real_get(f, s))
    _get()
    assert where and not any(where)
