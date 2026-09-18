#!/usr/bin/env python3
"""RED-first contract for red_alert_watch.py — the always-on RED ALERT watchdog.

Every 60s: for every live registered seat, classify (red_alert.classify), file a report per
NEW finding (dedup on seat+class while a report is open), post the card + Telegram + Arturo
mirror, and after the 120s window perform the immediate repair itself — safe repairs only,
never on an attached pane, escalating after two failed attempts. The decision core is pure.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import red_alert as RA  # noqa: E402
import red_alert_watch as W  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RED_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RED_ALERT_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def rep(cls="pane_dead", status="open", attempts=0, created_age=0, card=None, now=1000.0):
    return {
        "id": "ra_x", "class": cls, "status": status, "seats": ["gm"], "card_id": card,
        "created_at_epoch": now - created_age,
        "repair_attempts": [{"ok": False}] * attempts,
        "hold_until": None,
    }


# --- seat enumeration --------------------------------------------------------

def test_live_seats_are_online_cli_runtimes_with_a_tmux_session():
    registry = {"agents": {
        "gm": {"status": "online", "runtime": "claude", "tmux_session": "gm"},
        "parked": {"status": "parked", "runtime": "claude", "tmux_session": "parked"},
        "svc": {"status": "online", "runtime": "service", "tmux_session": "svc"},
        "nosess": {"status": "online", "runtime": "claude"},
        "gem": {"status": "online", "runtime": "gemini", "tmux_session": "gem"},
        "retired": {"status": "retired", "runtime": "claude", "tmux_session": "gm-gen3"},
    }}
    assert W.live_seats(registry, tmux_sessions={"gm", "gem", "svc", "parked", "gm-gen3"}) == [("gm", "gm"), ("gem", "gem")]


def test_live_seats_skips_registry_rows_whose_session_is_gone():
    registry = {"agents": {"gm": {"status": "online", "runtime": "claude", "tmux_session": "gm"}}}
    assert W.live_seats(registry, tmux_sessions=set()) == []


# --- decision core -----------------------------------------------------------

def test_no_card_yet_means_post_card():
    assert W.decide(rep(card=None), attached=False, answer=None, now=1000.0, armed=True)["action"] == "post_card"


def test_within_window_and_no_answer_waits():
    d = W.decide(rep(card="apr_1", created_age=30), attached=False, answer=None, now=1000.0, armed=True)
    assert d["action"] == "wait" and d["remaining_s"] == pytest.approx(90)


def test_window_elapsed_repairs():
    d = W.decide(rep(cls="pane_dead", card="apr_1", created_age=121), attached=False, answer=None, now=1000.0, armed=True)
    assert d["action"] == "repair" and d["fix"] == "respawn_resume"


def test_process_suspended_is_card_only_under_no_kills_rule():
    d = W.decide(rep(cls="process_suspended", card="apr_1", created_age=999), attached=False, answer="Repair now", now=1000.0, armed=True)
    assert d["action"] == "wait" and d["reason"] == "card_only"


def test_answer_repair_now_repairs_immediately():
    d = W.decide(rep(card="apr_1", created_age=5), attached=False, answer="Repair now", now=1000.0, armed=True)
    assert d["action"] == "repair"


def test_answer_wait_holds_30_minutes():
    d = W.decide(rep(card="apr_1", created_age=5), attached=False, answer="Wait", now=1000.0, armed=True)
    assert d["action"] == "hold" and d["hold_until"] == pytest.approx(1000.0 + 1800)


def test_answer_show_me_shows():
    d = W.decide(rep(card="apr_1", created_age=5), attached=False, answer="Show me", now=1000.0, armed=True)
    assert d["action"] == "show"


def test_attached_pane_is_card_only_even_after_window():
    d = W.decide(rep(card="apr_1", created_age=500), attached=True, answer=None, now=1000.0, armed=True)
    assert d["action"] == "wait" and d["reason"] == "attached"


def test_attached_pane_with_explicit_repair_now_repairs():
    d = W.decide(rep(card="apr_1", created_age=5), attached=True, answer="Repair now", now=1000.0, armed=True)
    assert d["action"] == "repair"


def test_card_only_class_never_auto_repairs():
    d = W.decide(rep(cls="login_screen", card="apr_1", created_age=500), attached=False, answer=None, now=1000.0, armed=True)
    assert d["action"] == "wait" and d["reason"] == "card_only"


def test_two_failed_attempts_escalates():
    d = W.decide(rep(card="apr_1", created_age=500, attempts=2), attached=False, answer=None, now=1000.0, armed=True)
    assert d["action"] == "escalate"


def test_disarmed_never_repairs_only_cards():
    d = W.decide(rep(card="apr_1", created_age=500), attached=False, answer="Repair now", now=1000.0, armed=False)
    assert d["action"] == "wait" and d["reason"] == "disarmed"


def test_hold_respected_until_expiry():
    r = rep(card="apr_1", created_age=500); r["hold_until"] = 1500.0
    assert W.decide(r, attached=False, answer="Wait", now=1000.0, armed=True)["action"] == "hold"
    assert W.decide(r, attached=False, answer="Wait", now=1600.0, armed=True)["action"] == "repair"


# --- dedup + report filing ---------------------------------------------------

def test_open_report_for_seat_class_dedups(store):
    ev = {"process_state": {"gm": [{"pid": 1, "stat": "T", "cmd": "claude"}]}, "pane_dead": {"gm": False}}
    r1 = W.file_finding("gm", RA.classify(ev, "gm"), ev, capture=lambda seats, **k: ev)
    r2 = W.file_finding("gm", RA.classify(ev, "gm"), ev, capture=lambda seats, **k: ev)
    assert r1["id"] == r2["id"]
    RA.resolve(r1["id"], note="t")
    r3 = W.file_finding("gm", RA.classify(ev, "gm"), ev, capture=lambda seats, **k: ev)
    assert r3["id"] != r1["id"]


# --- resume command derivation ----------------------------------------------

def test_resume_cmd_prefers_the_live_cli_argv_over_registry():
    procs = [{"pid": 5, "stat": "T", "tty": "pts/1", "cmd": "/x/bin/claude --resume abc --dangerously-skip-permissions --model claude-opus-5[1m] --settings /tmp/s.json"}]
    row = {"resume_command": "claude --resume abc --dangerously-skip-permissions", "session_id": "abc"}
    cmd = W.resume_cmd(procs, row)
    assert "--model claude-opus-5[1m]" in cmd and cmd.startswith("/x/bin/claude --resume abc")
    # (the watchdog only ever calls this with procs=[] now — a present process is never respawned)


def test_resume_cmd_falls_back_to_registry_when_no_process():
    row = {"resume_command": "claude --resume abc --dangerously-skip-permissions", "session_id": "abc"}
    assert W.resume_cmd([], row) == "claude --resume abc --dangerously-skip-permissions"


def test_resume_cmd_synthesizes_from_sid_when_registry_lacks_it():
    assert W.resume_cmd([], {"session_id": "abc", "runtime": "claude"}) == "claude --resume abc --dangerously-skip-permissions"


def test_resume_cmd_none_when_nothing_known():
    assert W.resume_cmd([], {}) is None


# --- provider ladder ---------------------------------------------------------

def test_next_model_walks_the_ladder():
    assert W.next_model("claude-opus-5[1m]", enabled=["claude", "gemini", "codex"]) == "claude-sonnet-5[1m]"
    assert W.next_model("claude-sonnet-5[1m]", enabled=["claude", "gemini", "codex"]) == "gemini"
    assert W.next_model("gemini", enabled=["claude", "gemini", "codex"]) == "codex"
    assert W.next_model("codex", enabled=["claude", "gemini", "codex"]) is None
    assert W.next_model("claude-sonnet-5[1m]", enabled=["claude"]) is None


def test_card_answer_normalisation():
    assert W.norm_answer({"status": "answered", "answer": "Repair now"}) == "Repair now"
    assert W.norm_answer({"status": "answered", "answer": "approve"}) == "Repair now"
    assert W.norm_answer({"status": "answered", "answer": "option", "answer_text": "wait"}) == "Wait"
    assert W.norm_answer({"status": "pending"}) is None


# --- the fleet-down class (: the tmux server was killed) --------

def test_fleet_down_files_one_card_only_report(store, monkeypatch):
    posted = []
    monkeypatch.setattr(W, "post_card", lambda r, ev, seat: posted.append(r["id"]) or "apr_x")
    reg = {"agents": {"gm": {"status": "online", "runtime": "claude", "tmux_session": "gm"},
                      "svc": {"status": "online", "runtime": "service"}}}
    r1 = W.fleet_down(reg)
    r2 = W.fleet_down(reg)
    assert r1["class"] == "tmux_server_dead" and r1["seats"] == ["fleet"]
    assert r1["id"] == r2["id"] and posted == [r1["id"], r1["id"]]  # no card_id persisted by the fake -> re-posted; dedup on the report
    d = W.decide({**r1, "card_id": "apr_x", "created_at_epoch": 0}, attached=False, answer=None, now=10_000.0, armed=True)
    assert d == {"action": "wait", "reason": "card_only"}


def test_fleet_down_with_no_online_seats_is_nothing(store):
    assert W.fleet_down({"agents": {}}) is None


# --- diagnosis spawn guard (the diag seat's own finding on ) -------

def test_spawn_diagnosis_refuses_unescalated_or_resolved(store, monkeypatch):
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    assert W.spawn_diagnosis({"id": "ra_x", "status": "open", "escalations": [], "repair_attempts": [], "seats": ["gm"], "_path": "p"}) == ""
    assert W.spawn_diagnosis({"id": "ra_x", "status": "resolved", "escalations": [{"reason": "r"}], "repair_attempts": [], "seats": ["gm"], "_path": "p"}) == ""


def test_switch_provider_never_on_an_attached_pane_even_with_repair_now():
    d = W.decide(rep(cls="out_of_usage", card="apr_1", created_age=500), attached=True, answer="Repair now", now=1000.0, armed=True)
    assert d["action"] == "wait" and d["reason"] == "attached"


# --- human-filed reports have NO pattern class (found from the phone, 2026-09-18) -----
# Both crashes below were invisible to this suite because every fixture had a class.

def test_post_card_survives_a_classless_human_report(store, monkeypatch):
    """A report filed by a person (the Report button) has class=None; the card title and the
    Telegram line must fall back to the severity wording instead of raising AttributeError."""
    sent = {}

    class _R:
        returncode = 0
        stdout = "apr_test123"
        stderr = ""

    def fake_run(argv, **kw):
        sent.setdefault("argv", []).append(argv)
        return _R()

    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W.RA, "update", lambda *a, **k: None)
    r = {"id": "ra_human", "class": None, "severity": "bug", "seats": ["gm"], "symptom": "the sheet says it broke",
         "_path": "/tmp/x.json", "evidence": {}}
    card = W.post_card(r, {"pane_snapshot": {}, "screen": {}}, "gm")
    assert card == "apr_test123"
    question = sent["argv"][0][-1]
    assert "bug" in question and "None" not in question


def test_post_card_classless_falls_back_to_issue_when_severity_missing(store, monkeypatch):
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "apr_x", "stderr": ""})())
    monkeypatch.setattr(W.RA, "update", lambda *a, **k: None)
    r = {"id": "ra_h2", "class": None, "severity": None, "seats": ["gm"], "symptom": "s", "_path": "/tmp/x.json", "evidence": {}}
    assert W.post_card(r, {}, "gm") == "apr_x"


# --- greens are not seats (gm denial of a report on orchestra-builder-g49) ------------
# A blue-green GREEN dying is often CORRECT (hydrate SeamTimeout under load -> the seam
# raised, the green was torn down, blue kept working). Respawning one would manufacture an
# orphan pane the fleet has a watcher for. Only the state machine may boot a green.

def _bg(tmp_path, root, state, **meta):
    import json
    d = tmp_path / "wal"
    d.mkdir(exist_ok=True)
    (d / f"{root}.bg.json").write_text(json.dumps({"state": state, "root": root, "meta": meta}))
    return str(d)


def test_green_status_plain_seat(tmp_path):
    assert W.green_status("gm", wal_dir=str(tmp_path)) == "not_green"
    assert W.green_status("orchestra-builder", wal_dir=str(tmp_path)) == "not_green"


def test_green_status_unexpected_green_when_root_is_solo(tmp_path):
    wal = _bg(tmp_path, "orchestra-builder", "SOLO", green_pane_id=None)
    assert W.green_status("orchestra-builder-g49", wal_dir=wal) == "unexpected_green"


def test_green_status_active_green_when_root_is_mid_swap(tmp_path):
    wal = _bg(tmp_path, "orchestra-builder", "PREWARMING", green_pane_id="%42", green_session_id="abc")
    assert W.green_status("orchestra-builder-g49", wal_dir=wal) == "active_green"


def test_green_status_missing_bg_file_is_unexpected(tmp_path):
    assert W.green_status("nobody-g7", wal_dir=str(tmp_path)) == "unexpected_green"


def test_tick_does_not_file_for_a_dead_unexpected_green(tmp_path, store, monkeypatch):
    wal = _bg(tmp_path, "orchestra-builder", "SOLO")
    monkeypatch.setattr(W, "WAL_DIR", wal)
    filed = []
    monkeypatch.setattr(W, "file_finding", lambda *a, **k: filed.append(a[0]))
    monkeypatch.setattr(W, "tmux_sessions", lambda: {"orchestra-builder-g49"})
    monkeypatch.setattr(W.RA, "capture_evidence", lambda seats, **k: {
        "process_state": {seats[0]: []}, "pane_dead": {seats[0]: True}, "attached": {seats[0]: False},
        "screen": {}, "registry_rows": {}, "sids": {}, "pane_snapshot": {}})
    reg = {"agents": {"orchestra-builder-g49": {"status": "online", "runtime": "claude", "tmux_session": "orchestra-builder-g49"}}}
    import json as _j
    p = tmp_path / "reg.json"
    p.write_text(_j.dumps(reg))
    W.tick(registry_path=str(p))
    assert filed == []


def test_tick_files_card_only_for_a_dead_active_green(tmp_path, store, monkeypatch):
    wal = _bg(tmp_path, "orchestra-builder", "VERIFY", green_pane_id="%42")
    monkeypatch.setattr(W, "WAL_DIR", wal)
    filed = []
    monkeypatch.setattr(W, "file_finding", lambda seat, f, ev, **k: filed.append(f) or {"id": "ra_x", "status": "open", "seats": [seat], "class": f["class"], "card_id": None, "created_at": "2026-09-18T00:00:00+00:00", "evidence": {}, "repair_attempts": [], "symptom": "s", "_path": "p"})
    monkeypatch.setattr(W, "handle", lambda *a, **k: None)
    monkeypatch.setattr(W, "tmux_sessions", lambda: {"orchestra-builder-g49"})
    monkeypatch.setattr(W.RA, "capture_evidence", lambda seats, **k: {
        "process_state": {seats[0]: []}, "pane_dead": {seats[0]: True}, "attached": {seats[0]: False},
        "screen": {}, "registry_rows": {}, "sids": {}, "pane_snapshot": {}})
    import json as _j
    p = tmp_path / "reg.json"
    p.write_text(_j.dumps({"agents": {"orchestra-builder-g49": {"status": "online", "runtime": "claude", "tmux_session": "orchestra-builder-g49"}}}))
    W.tick(registry_path=str(p))
    assert filed and filed[0]["class"] == "green_died"
    assert filed[0]["immediate_fix"]["action"] == "card_only"


def test_respawn_refuses_any_green_even_if_asked():
    ev = {"process_state": {"x-g9": []}, "registry_rows": {"x-g9": {"session_id": "abc", "runtime": "claude"}}, "pane_dead": {"x-g9": True}}
    ok, detail = W.repair_respawn("x-g9", "x-g9", ev)
    assert ok is False and "green" in detail.lower()


# --- the queue is evidence this week: never auto-answer a card (2026-09-18) --------
# the operator wants the pending queue INTACT — a saturated queue is what he is demonstrating, not
# a defect in the surface. A healed report gets a CORRECTION on the card, never a close.

def test_close_card_is_disabled_by_the_preserve_queue_flag(store, monkeypatch):
    calls = []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(W, "PRESERVE_PENDING_QUEUE", True)
    W.close_card("apr_x", "healed")
    verbs = [argv[2] for argv in calls if len(argv) > 2]      # approval.py <verb>
    assert "answer" not in verbs, "must not answer a card while the queue is preserved"
    assert "patch" in verbs, "the correction must still be written onto the card"


def test_close_card_answers_only_when_preservation_is_off(store, monkeypatch):
    calls = []
    monkeypatch.setattr(W.subprocess, "run", lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "{}", "stderr": ""})())
    monkeypatch.setattr(W, "PRESERVE_PENDING_QUEUE", False)
    W.close_card("apr_x", "healed")
    assert "answer" in [argv[2] for argv in calls if len(argv) > 2]


def test_card_corrections_append_and_never_rewrite_the_ask(store, monkeypatch):
    """A card in the live queue must still READ as the live ask (2026-09-18): a status note
    goes at the END, never at the top, or a pending row looks closed on its face."""
    captured = {}

    def fake_run(argv, **kw):
        if len(argv) > 2 and argv[2] == "patch":
            captured["summary"] = argv[argv.index("--summary") + 1]
        return type("R", (), {"returncode": 0, "stdout": '{"summary": "**What happened:** the original ask"}', "stderr": ""})()

    monkeypatch.setattr(W.subprocess, "run", fake_run)
    monkeypatch.setattr(W, "PRESERVE_PENDING_QUEUE", True)
    W.close_card("apr_x", "the seat healed")
    s = captured["summary"]
    assert s.startswith("**What happened:** the original ask")
    assert s.rstrip().endswith("the seat healed")
