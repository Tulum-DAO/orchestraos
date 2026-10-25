"""DOWN seats: a seat whose agent process exited (pane alive at a shell) is PAGED to its
escalation and the owner channel instead of being held silently; rows stay pending. A blind
detector pages once about itself, never once per seat. Fakes only; nothing is sent."""
import importlib.util
import inspect
import os

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_down",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

OWNER = mr.OWNER_CHANNEL
T = 1_800_000_000.0   # any realistic epoch: page throttles start at 0


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_send_page", lambda to, s, b: out.append((to, s, b)))
    monkeypatch.setattr(mr, "escalation_targets", lambda seat: ["lead-a", OWNER])
    return out


def _status(state, running=None):
    st = {"state": state}
    if running is not None:
        st["process"] = {"running": running}
    return st


def test_down_comes_from_the_detector(monkeypatch):
    monkeypatch.setattr(mr, "_detector_status", lambda s: _status("stopped"))
    assert mr.seat_is_down("a") is True
    monkeypatch.setattr(mr, "_detector_status", lambda s: _status("idle", running=False))
    assert mr.seat_is_down("a") is True
    monkeypatch.setattr(mr, "_detector_status", lambda s: _status("working"))
    assert mr.seat_is_down("a") is False


def test_detector_error_is_not_down_and_is_recorded(monkeypatch):
    def boom(s):
        raise RuntimeError("detector exploded")
    monkeypatch.setattr(mr, "_detector_status", boom)
    errs = []
    assert mr.seat_is_down("a", errors=errs) is False
    assert errs and errs[0][0] == "a"


def test_probe_is_cached_once_per_recipient_per_cycle(monkeypatch):
    calls = []
    monkeypatch.setattr(mr, "_detector_status", lambda s: calls.append(s) or _status("stopped"))
    cache = {}
    for _ in range(3):
        assert mr.seat_is_down("a", cache=cache)
    assert calls == ["a"]


def test_page_goes_to_escalation_and_owner_with_the_facts(sent):
    state = {}
    anchor = mr.mark_seat_down(state, "a", T + 1000.0)
    assert mr.page_seat_down(state, "a", 3, T + 1000.0 + 3900, anchor) is True
    assert [t for t, _, _ in sent] == ["lead-a", OWNER]
    subject, body = sent[0][1], sent[1][2]
    assert "[DOWN] a" in subject and "3 message(s) held" in subject and "1h05m" in subject
    assert "PENDING" in body and "Also told: lead-a" in body


def test_page_is_throttled_but_never_latched_off(sent):
    state = {}
    anchor = mr.mark_seat_down(state, "a", T + 0.0)
    assert mr.page_seat_down(state, "a", 1, T + 10.0, anchor)
    assert not mr.page_seat_down(state, "a", 1, T + 10.0 + mr.DOWN_PAGE_REPEAT_S - 1, anchor)
    assert mr.page_seat_down(state, "a", 1, T + 10.0 + mr.DOWN_PAGE_REPEAT_S, anchor)


def test_owner_hop_is_deduped_on_the_same_condition(sent):
    state = {}
    anchor = mr.mark_seat_down(state, "a", T + 0.0)
    mr.page_seat_down(state, "a", 1, T + 10.0, anchor)
    mr.page_seat_down(state, "a", 1, T + 10.0 + mr.DOWN_PAGE_REPEAT_S, anchor)
    owners = [t for t, _, _ in sent if t == OWNER]
    agents = [t for t, _, _ in sent if t == "lead-a"]
    assert len(agents) == 2 and len(owners) == 1


def test_anchor_is_first_seen_and_recovery_notifies_once(sent):
    state = {}
    assert mr.mark_seat_down(state, "a", T + 100.0) == T + 100.0
    assert mr.mark_seat_down(state, "a", T + 500.0) == T + 100.0
    mr.page_seat_down(state, "a", 1, T + 500.0, T + 100.0)
    sent.clear()
    mr.clear_seat_down(state, "a", now=T + 700.0)
    assert "a" not in state
    assert len(sent) == 1 and sent[0][0] == OWNER and "[RECOVERED] a" in sent[0][1]
    mr.clear_seat_down(state, "a", now=T + 800.0)          # nothing on record: silent
    assert len(sent) == 1


def test_owner_is_reached_when_the_agent_hop_fails_or_the_seam_breaks(monkeypatch):
    out = []
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)

    def flaky(to, s, b):
        if to != OWNER:
            raise RuntimeError("hop down")
        out.append((to, b))
    monkeypatch.setattr(mr, "_send_page", flaky)
    monkeypatch.setattr(mr, "escalation_targets", lambda seat: ["lead-a", OWNER])
    mr.page_seat_down({}, "a", 1, T + 10.0, T + 0.0)
    assert out and "agent hop FAILED" in out[-1][1]

    def broken(seat):
        raise mr.EscalationSeamBroken("wrong shape")
    monkeypatch.setattr(mr, "escalation_targets", broken)
    mr.page_seat_down({}, "b", 1, T + 10.0, T + 0.0)
    assert "ESCALATION FAILURE" in out[-1][1] and "BROKEN" in out[-1][1]


def test_detector_health_pages_once_after_consecutive_blind_cycles(sent):
    state = {}
    for i in range(mr.DOWN_DETECTOR_BLIND_CYCLES - 1):
        assert not mr.note_detector_health(state, 2, 2, T + float(i), "err")
    assert mr.note_detector_health(state, 2, 2, T + 100.0, "err")
    assert [t for t, _, _ in sent] == ["gm", OWNER]
    assert not mr.note_detector_health(state, 2, 2, T + 101.0, "err")      # throttled
    assert not mr.note_detector_health(state, 2, 1, T + 102.0, None)       # one seat's error: not blind
    assert state[mr.DETECTOR_KEY]["consecutive"] == 0


def test_down_state_round_trips_and_tolerates_a_bad_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "DOWN_FILE", tmp_path / "state" / "downs.json")
    assert mr.load_downs() == {}
    mr.save_downs({"a": {"first_seen": 1.0}})
    assert mr.load_downs() == {"a": {"first_seen": 1.0}}
    (tmp_path / "state" / "downs.json").write_text("{not json")
    assert mr.load_downs() == {}


def test_loop_pages_down_before_holding_and_persists():
    src = inspect.getsource(mr.main)
    idle = src.index("if not _idle:")
    down = src.index("if seat_is_down(session, cache=_down_cache, errors=_down_errors):")
    hold = src.index('"not-idle", now', idle)
    assert idle < down < hold
    assert src.index("note_detector_health(") < src.index("save_downs(down_state)")
    assert "down_state = load_downs()" in src
