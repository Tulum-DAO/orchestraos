"""RED-first (gm msg_86bcc168 item 5): the router DEAD-LETTERED 10 live rows tonight
(7 to orchestra-builder 03:58-07:15Z while the seat was idle at 15%, 3 to gm) — the SLA
escalation cap terminated mail to seats that were merely mis-read as not-idle. Escalation
must still TERMINATE (leg-4 act 2: no 34h re-escalation loops), but termination now PARKS
the row (status stays pending; no more escalation; retried on the next idle cycle) instead
of destroying it, the sender is told it is PARKED (not terminated), the target's idle-read
evidence is logged, and nothing terminal happens within 15 min of a rotation of the target."""
import importlib.util
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

_here = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "message_router", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

SESSION = "park-seat"


def _quiet(monkeypatch):
    monkeypatch.setattr(mr, "telegram", lambda *a, **k: None)
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_hook_says_working_fresh", lambda s: False)
    monkeypatch.setattr(mr, "hook_state", lambda s: None)
    monkeypatch.setattr(mr, "target_class", lambda s: "agent")
    monkeypatch.setattr(mr, "_target_rotated_within", lambda s, now, window_s=900: False)


def _capped_state(now):
    return {f"{SESSION}|not-idle": {
        "logged_at": now,
        "msgs": {"msg_p1": {"escalations": mr.HOLD_ESCALATE_MAX, "escalated_at": 0}}}}


def _msg():
    return {"id": "msg_p1", "from_agent": "gm", "subject": "s",
            "created_at": "2026-01-01T00:00:00+00:00"}   # ancient: age floor met


def test_cap_parks_instead_of_dead_lettering(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    hs = _capped_state(now)
    killed, parked, batch = [], [], {}
    mr.note_hold(hs, SESSION, _msg(), "not-idle", now,
                 dead_letter_fn=lambda mid, why: killed.append(mid) or True,
                 park_fn=lambda mid, why: parked.append((mid, why)) or True,
                 dl_batch=batch)
    assert killed == [], "a capped row was destroyed; it must be parked"
    assert [p[0] for p in parked] == ["msg_p1"]
    assert "parked" in parked[0][1].lower() and "not-idle" in parked[0][1]
    mrec = hs[f"{SESSION}|not-idle"]["msgs"]["msg_p1"]
    assert mrec.get("parked") is True and not mrec.get("dead_lettered")
    assert batch == {("gm", "agent"): ["msg_p1"]}      # sender told once, batched


def test_parked_row_never_re_escalates_or_re_parks(monkeypatch):
    _quiet(monkeypatch)
    pings = []
    monkeypatch.setattr(mr, "telegram", lambda t: pings.append(t))
    now = time.time()
    hs = _capped_state(now)
    parked = []
    park = lambda mid, why: parked.append(mid) or True
    mr.note_hold(hs, SESSION, _msg(), "not-idle", now, park_fn=park, dl_batch={})
    later = now + mr.HOLD_ESCALATE_REPEAT_S + 5
    mr.note_hold(hs, SESSION, _msg(), "not-idle", later, park_fn=park, dl_batch={})
    mr.note_hold(hs, SESSION, _msg(), "not-idle", later + mr.HOLD_ESCALATE_REPEAT_S + 5,
                 park_fn=park, dl_batch={})
    assert parked == ["msg_p1"]                        # parked exactly once
    assert pings == []                                 # no the operator telegram after the cap


def test_idle_read_evidence_is_logged_at_park(monkeypatch):
    _quiet(monkeypatch)
    lines = []
    monkeypatch.setattr(mr, "log", lambda t: lines.append(t))
    monkeypatch.setattr(mr, "hook_state", lambda s: ("idle", time.time() - 4000))
    now = time.time()
    mr.note_hold(_capped_state(now), SESSION, _msg(), "not-idle", now,
                 park_fn=lambda mid, why: True, dl_batch={})
    park_lines = [l for l in lines if "PARKED" in l]
    assert park_lines and "hook=idle" in park_lines[0] and "age" in park_lines[0]


def test_recent_rotation_of_target_holds_without_counting(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(mr, "_target_rotated_within", lambda s, now, window_s=900: True)
    pings, parked = [], []
    monkeypatch.setattr(mr, "telegram", lambda t: pings.append(t))
    now = time.time()
    # (a) at the cap: must NOT park during the rotation window
    hs = _capped_state(now)
    mr.note_hold(hs, SESSION, _msg(), "not-idle", now,
                 park_fn=lambda mid, why: parked.append(mid) or True, dl_batch={})
    assert parked == [] and not hs[f"{SESSION}|not-idle"]["msgs"]["msg_p1"].get("parked")
    # (b) below the cap: must NOT count an escalation nor telegram either
    hs2 = {f"{SESSION}|not-idle": {"logged_at": now,
                                   "msgs": {"msg_p1": {"escalations": 1, "escalated_at": 0}}}}
    mr.note_hold(hs2, SESSION, _msg(), "not-idle", now, park_fn=lambda m, w: True, dl_batch={})
    assert hs2[f"{SESSION}|not-idle"]["msgs"]["msg_p1"]["escalations"] == 1
    assert pings == []


def test_park_notice_wording_is_parked_not_terminated(monkeypatch):
    sent = []
    n = mr.flush_dead_letter_notices({("gm", "agent"): ["msg_p1", "msg_p2"]},
                                     send_fn=lambda sender, text: sent.append((sender, text)))
    assert n == 1 and sent[0][0] == "gm"
    text = sent[0][1]
    assert "PARKED" in text and "msg_p1" in text and "msg_p2" in text
    assert "terminated" not in text.lower() and "never deliverable" not in text.lower()
    assert "still pending" in text.lower() or "still queued" in text.lower()


def _gen_db(tmp_path, promoted_ago_s, root="park-seat"):
    db = tmp_path / "orchestra-registry.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, "
              "session_id TEXT, promoted_at TEXT, retired_at TEXT)")
    ts = (datetime.now(timezone.utc) - timedelta(seconds=promoted_ago_s)).isoformat()
    c.execute("INSERT INTO generations (root, generation, session_id, promoted_at) VALUES (?,?,?,?)",
              [root, 43, "sid", ts])
    c.commit(); c.close()
    return str(db)


def test_target_rotated_within_reads_generation_promoted_at(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    now = time.time()
    fresh = _gen_db(tmp_path, 300)
    monkeypatch.setattr(mr, "_registry_db_path", lambda: fresh)
    assert mr._target_rotated_within("park-seat", now) is True
    assert mr._target_rotated_within("park-seat-g43", now) is True     # -gN alias -> root
    assert mr._target_rotated_within("park-seat-gen43", now) is True
    assert mr._target_rotated_within("other-seat", now) is False
    (tmp_path / "old").mkdir(exist_ok=True)
    old = _gen_db(tmp_path / "old", 3600)
    monkeypatch.setattr(mr, "_registry_db_path", lambda: old)
    assert mr._target_rotated_within("park-seat", now) is False
    (tmp_path / "future").mkdir(exist_ok=True)
    future = _gen_db(tmp_path / "future", -3600)          # promoted "after" now
    monkeypatch.setattr(mr, "_registry_db_path", lambda: future)
    assert mr._target_rotated_within("park-seat", now) is False


def test_target_rotated_within_fails_open_false(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, "log", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_registry_db_path", lambda: str(tmp_path / "missing.db"))
    assert mr._target_rotated_within("park-seat", time.time()) is False


# ---- park notices per TARGET EPISODE, not per router run (g43 baton OPEN item 2, gm
# msg_e9a921fe ruling (3), 2026-09-16): one stuck target used to send the sender a fresh
# [PARKED] notice on EVERY router run that capped another row -- N notices for one episode.
# An episode opens at the first park notice for (sender, target) and closes when a row to
# that target actually DELIVERS (or after PARK_NOTICE_EPISODE_S as a fallback).

def _state_with(now, *mids):
    return {f"{SESSION}|not-idle": {
        "logged_at": now,
        "msgs": {m: {"escalations": mr.HOLD_ESCALATE_MAX, "escalated_at": 0} for m in mids}}}


def _msg_id(mid, sender="gm"):
    return {"id": mid, "from_agent": sender, "subject": "s",
            "created_at": "2026-01-01T00:00:00+00:00"}


def test_second_park_to_same_target_in_open_episode_sends_no_second_notice(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    hs = _state_with(now, "msg_p1", "msg_p2")
    parked, b1, b2 = [], {}, {}
    park = lambda mid, why: parked.append(mid) or True
    mr.note_hold(hs, SESSION, _msg_id("msg_p1"), "not-idle", now, park_fn=park, dl_batch=b1)
    # a LATER router run caps a second row to the same stuck target
    mr.note_hold(hs, SESSION, _msg_id("msg_p2"), "not-idle", now + 1800, park_fn=park, dl_batch=b2)
    assert parked == ["msg_p1", "msg_p2"], "both rows are still parked"
    assert b1 == {("gm", "agent"): ["msg_p1"]}
    assert b2 == {}, f"second notice in the same episode: {b2}"


def test_delivery_to_target_closes_the_episode_so_a_new_park_notifies_again(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    hs = _state_with(now, "msg_p1", "msg_p2")
    park = lambda mid, why: True
    mr.note_hold(hs, SESSION, _msg_id("msg_p1"), "not-idle", now, park_fn=park, dl_batch={})
    mr.close_park_episode(hs, SESSION)                     # a row to SESSION delivered
    b = {}
    mr.note_hold(hs, SESSION, _msg_id("msg_p2"), "not-idle", now + 60, park_fn=park, dl_batch=b)
    assert b == {("gm", "agent"): ["msg_p2"]}


def test_episodes_are_per_sender_and_per_target(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    hs = _state_with(now, "msg_p1", "msg_p2")
    hs["other-seat|not-idle"] = {"logged_at": now, "msgs": {
        "msg_p3": {"escalations": mr.HOLD_ESCALATE_MAX, "escalated_at": 0}}}
    park = lambda mid, why: True
    mr.note_hold(hs, SESSION, _msg_id("msg_p1", "gm"), "not-idle", now, park_fn=park, dl_batch={})
    b = {}
    mr.note_hold(hs, SESSION, _msg_id("msg_p2", "rab"), "not-idle", now + 1, park_fn=park, dl_batch=b)
    mr.note_hold(hs, "other-seat", _msg_id("msg_p3", "gm"), "not-idle", now + 2, park_fn=park, dl_batch=b)
    assert b == {("rab", "agent"): ["msg_p2"], ("gm", "agent"): ["msg_p3"]}


def test_episode_fallback_expiry_reopens_notices(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    hs = _state_with(now, "msg_p1", "msg_p2")
    park = lambda mid, why: True
    mr.note_hold(hs, SESSION, _msg_id("msg_p1"), "not-idle", now, park_fn=park, dl_batch={})
    b = {}
    mr.note_hold(hs, SESSION, _msg_id("msg_p2"), "not-idle", now + mr.PARK_NOTICE_EPISODE_S + 1,
                 park_fn=park, dl_batch=b)
    assert b == {("gm", "agent"): ["msg_p2"]}
