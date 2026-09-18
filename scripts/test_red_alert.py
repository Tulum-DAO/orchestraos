#!/usr/bin/env python3
"""RED-first contract for red_alert.py — the RED ALERT crash-report standard.

Commission: prompts/red-alert-builder.md (the operator 2026-09-17 23:30 Tulum, after ^Z on the
harness bottom bar suspended gm). A crash report is ONE JSON file under
state/red-alert/<ts>-<slug>.json with the fixed schema in docs/RED_ALERT.md, the CLI is
scripts/red_alert.py (report / list / show / update / resolve / escalate), and the
classifier turns evidence (ps STAT, screen text, pane liveness) into a catalogued class
with an immediate repair the watchdog may perform itself.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import red_alert as RA  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RED_ALERT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("RED_ALERT_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def fake_evidence(seats, **kw):
    return {
        "pane_snapshot": {s: f"/tmp/{s}-pane.txt" for s in seats},
        "process_state": {s: [{"pid": 1, "stat": "T", "cmd": "claude --resume abc"}] for s in seats},
        "log_excerpt": {s: "tail" for s in seats},
        "sids": {s: "abc" for s in seats},
        "registry_rows": {s: {"tmux_session": s, "session_id": "abc"} for s in seats},
    }


# --- schema -----------------------------------------------------------------

def test_report_writes_one_json_with_full_schema(store):
    rep = RA.report(reported_by="user", channel="harness", severity="crash", seats=["gm"],
                    symptom="^Z suspended gm", capture=fake_evidence)
    path = rep["_path"]
    assert path.endswith(".json") and os.path.dirname(path) == os.environ["RED_ALERT_STATE_DIR"]
    on_disk = json.load(open(path))
    for key in RA.SCHEMA_KEYS:
        assert key in on_disk, key
    assert on_disk["id"].startswith("ra_")
    assert on_disk["status"] == "open"
    assert on_disk["seats"] == ["gm"]
    assert on_disk["evidence"]["process_state"]["gm"][0]["stat"] == "T"
    assert on_disk["timeline"][0]["event"] == "reported"


def test_report_rejects_unknown_severity_and_reporter(store):
    with pytest.raises(ValueError):
        RA.report(reported_by="ghost", channel="x", severity="crash", seats=["gm"], symptom="s", capture=fake_evidence)
    with pytest.raises(ValueError):
        RA.report(reported_by="user", channel="x", severity="meh", seats=["gm"], symptom="s", capture=fake_evidence)


def test_report_filename_is_ts_slug(store):
    rep = RA.report(reported_by="watchdog", channel="watchdog", severity="error", seats=["gm"],
                    symptom="Out of usage credits on screen", capture=fake_evidence)
    base = os.path.basename(rep["_path"])
    ts, _, slug = base.partition("-")
    assert len(ts) == 16 and ts.endswith("Z")
    assert slug.startswith("gm-process-suspended")  # class wins the slug when evidence classifies


# --- classifier (the catalogue of errors the system upholds itself to) -------

def test_classify_suspended_process_is_the_ctrl_z_class():
    ev = fake_evidence(["gm"])
    c = RA.classify(ev, "gm")
    assert c["class"] == "process_suspended"
    assert c["severity"] == "crash"
    assert c["immediate_fix"]["action"] == "card_only"  # NO KILLS rule: a present process is never touched


def test_classify_dead_pane():
    ev = fake_evidence(["gm"])
    ev["process_state"]["gm"] = []
    ev["pane_dead"] = {"gm": True}
    c = RA.classify(ev, "gm")
    assert c["class"] == "pane_dead"
    assert c["immediate_fix"]["action"] == "respawn_resume"


@pytest.mark.parametrize("screen,cls", [
    ("You've hit your usage limit · out of usage credits", "out_of_usage"),
    ("API Error: 529 overloaded", "api_error"),
    ("Select login method:\n 1. Claude account", "login_screen"),
    ("Bypass Permissions mode\n Yes, I accept", "bypass_permissions_dialog"),
    ("Claude Code has been suspended. Run `fg` to bring Claude Code back.", "process_suspended"),
])
def test_classify_screen_text(screen, cls):
    ev = fake_evidence(["gm"])
    ev["process_state"]["gm"] = [{"pid": 1, "stat": "Sl+", "cmd": "claude"}]
    ev["screen"] = {"gm": screen}
    assert RA.classify(ev, "gm")["class"] == cls


def test_remote_control_disconnected_notice_is_not_a_login_screen():
    ev = fake_evidence(["gm"])
    ev["process_state"]["gm"] = [{"pid": 1, "stat": "Sl+", "cmd": "claude"}]
    ev["screen"] = {"gm": "● Remote Control disconnected — signed-in claude.ai account changed — run /remote-control ..., or /login to switch back\n❯ "}
    assert RA.classify(ev, "gm") is None


def test_classify_healthy_returns_none():
    ev = fake_evidence(["gm"])
    ev["process_state"]["gm"] = [{"pid": 1, "stat": "Sl+", "cmd": "claude"}]
    ev["screen"] = {"gm": "❯ "}
    assert RA.classify(ev, "gm") is None


def test_every_catalogue_class_has_immediate_fix_and_doc():
    doc = open(os.path.join(HERE, "..", "docs", "RED_ALERT.md")).read()
    for name, entry in RA.CATALOGUE.items():
        assert f"`{name}`" in doc, f"{name} missing from docs/RED_ALERT.md catalogue table"
        assert entry["severity"] in RA.SEVERITIES, name
        assert "immediate_fix" in entry and "action" in entry["immediate_fix"], name
        assert entry.get("doc"), name


# --- lifecycle --------------------------------------------------------------

def test_list_resolve_escalate_roundtrip(store):
    rep = RA.report(reported_by="agent", channel="msg_store", severity="bug", seats=["x"],
                    symptom="composer stuck", capture=fake_evidence)
    rid = rep["id"]
    assert [r["id"] for r in RA.list_reports()] == [rid]
    assert RA.list_reports(status="resolved") == []
    RA.update(rid, diagnosis="paste newline", immediate_fix={"action": "bare_enter"}, status="repairing")
    r = RA.show(rid)
    assert r["status"] == "repairing" and r["diagnosis"] == "paste newline"
    RA.escalate(rid, reason="repair failed twice")
    r = RA.show(rid)
    assert r["escalations"][0]["reason"] == "repair failed twice"
    assert r["status"] == "awaiting-approval"
    RA.resolve(rid, note="fixed by effect")
    r = RA.show(rid)
    assert r["status"] == "resolved" and r["timeline"][-1]["event"] == "resolved"
    assert [x["id"] for x in RA.list_reports(status="resolved")] == [rid]


def test_update_rejects_unknown_key_and_bad_status(store):
    rep = RA.report(reported_by="agent", channel="msg_store", severity="bug", seats=["x"],
                    symptom="s", capture=fake_evidence)
    with pytest.raises(ValueError):
        RA.update(rep["id"], status="done")
    with pytest.raises(ValueError):
        RA.update(rep["id"], bogus=1)


def test_cli_report_and_list(store, capsys):
    rc = RA.main(["report", "--reported-by", "user", "--channel", "telegram", "--severity", "crash",
                  "--seat", "gm", "--symptom", "gm is frozen", "--no-capture"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ra_" in out
    RA.main(["list"])
    out = capsys.readouterr().out
    assert "gm is frozen" in out and "open" in out


def test_cli_report_kind_derives_severity_and_prints_json(store, capsys):
    rc = RA.main(["report", "--reported-by", "user", "--channel", "dashboard", "--kind", "suggestion",
                  "--seat", "gm", "--symptom", "make the bar bigger", "--no-capture"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["severity"] == "improvement" and out["id"].startswith("ra_")
    assert RA.show(out["id"])["kind"] == "suggestion"


def test_surface_routes_improvements_away_from_cards(store, monkeypatch):
    calls = []
    monkeypatch.setattr(RA, "_sp_run_for_test", None, raising=False)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a[0][0]) or type("R", (), {"returncode": 0, "stdout": "{\"sent\": true}"})())
    d = RA.report(reported_by="user", channel="dashboard", severity="improvement", seats=["gm"], symptom="idea", capture=fake_evidence)
    out = RA.surface(d)
    assert out["card_id"] is None and out["surfaced"] is True
    assert not any("approval.py" in c for c in calls)


# --- ps parsing (the ^Z signal) ---------------------------------------------

def test_parse_ps_tree_marks_stopped():
    ps = "  PID STAT TT       CMD\n 1406688 T    pts/60   node claude --resume abc\n 1406700 Sl   pts/60   mcp-server\n"
    rows = RA.parse_ps(ps)
    assert rows[0] == {"pid": 1406688, "stat": "T", "tty": "pts/60", "cmd": "node claude --resume abc"}
    assert RA.any_stopped(rows) is True
    assert RA.any_stopped([{"pid": 1, "stat": "Sl+", "tty": "", "cmd": "x"}]) is False


# --- screen classes match CLI banners only, never transcript prose () ---

FIX = os.path.join(HERE, "fixtures", "red-alert")


def _screen_ev(text):
    ev = fake_evidence(["s"])
    ev["process_state"]["s"] = [{"pid": 1, "stat": "Sl+", "cmd": "claude"}]
    ev["pane_dead"] = {"s": False}
    ev["screen"] = {"s": text}
    return ev


@pytest.mark.parametrize("name", ["false_positive_prose_usage_limit.txt", "false_positive_prose_gm_usage_limit.txt"])
def test_prose_mentioning_usage_limit_is_not_out_of_usage(name):
    p = os.path.join(FIX, name)
    if not os.path.exists(p):
        pytest.skip("private capture not shipped in this tree")
    assert RA.classify(_screen_ev(open(p).read()), "s") is None


def test_real_out_of_usage_credits_banner_is_out_of_usage():
    p = os.path.join(FIX, "real_out_of_usage_credits_banner.txt")
    text = open(p).read() if os.path.exists(p) else (
        "❯ resume and continue\n  ⎿  You're out of usage credits. Run /usage-credits to keep using Fable 5.1 or /model to switch models.\n"
        "✻ Cooked for 0s\n❯ \n  ⬆ status │ Fable 5.1 │ 80%")
    assert RA.classify(_screen_ev(text), "s")["class"] == "out_of_usage"


@pytest.mark.parametrize("line", [
    "  ⎿  You've hit your usage limit · resets 3pm",
    "  ⎿  API Error: 529 {\"type\":\"overloaded_error\"}",
    "  ⎿  Not logged in. Run /login",
])
def test_system_lines_classify(line):
    text = "● some assistant prose\n" + line + "\n❯ \n  ⬆ status bar"
    assert RA.classify(_screen_ev(text), "s") is not None


@pytest.mark.parametrize("line", [
    "● I stopped because of the API Error the user mentioned yesterday",
    "  the doc says: Select login method is the first screen",
    "● we skipped the wave given the usage limit; insufficient credits was the reason last week",
    "  ⎿  grep output: 'You're out of usage credits' appears in docs/RED_ALERT.md:64",
])
def test_prose_never_classifies(line):
    text = "● prose\n" + line + "\n❯ \n  ⬆ status bar"
    assert RA.classify(_screen_ev(text), "s") is None


def test_suspended_needs_the_shell_stop_line_too():
    assert RA.classify(_screen_ev("● the doc says Claude Code has been suspended once\n❯ "), "s") is None
    assert RA.classify(_screen_ev("Claude Code has been suspended. Run `fg` to bring Claude Code back.\n[1] + Stopped  claude\n$ "), "s")["class"] == "process_suspended"


def test_bypass_dialog_needs_accept_row():
    assert RA.classify(_screen_ev("● I read about Bypass Permissions mode in the docs\n❯ "), "s") is None
    assert RA.classify(_screen_ev("  Bypass Permissions mode\n  ❯ 1. No, exit\n    2. Yes, I accept\n"), "s")["class"] == "bypass_permissions_dialog"


# ---------------------------------------------------------------------------
# clean /exit is not a crash (, orchestra-builder-g49 2026-09-18 17:33Z)
# ---------------------------------------------------------------------------
def _write_transcript(tmp_path, sid, lines):
    p = tmp_path / f"{sid}.jsonl"
    p.write_text("".join(json.dumps(l) + "\n" for l in lines))
    return str(p)


_EXIT_LINES = [
    {"type": "assistant", "message": {"role": "assistant", "content": "handoff written"}},
    {"type": "user", "message": {"role": "user", "content":
        "<command-name>/exit</command-name>\n<command-message>exit</command-message>"},
     "timestamp": "2026-09-18T17:33:00.563Z"},
    {"type": "user", "message": {"role": "user", "content": "<local-command-stdout>Goodbye!</local-command-stdout>"},
     "timestamp": "2026-09-18T17:33:00.563Z"},
]


def test_dead_pane_after_a_clean_exit_is_not_a_crash(tmp_path, monkeypatch):
    """a green typed /exit at 17:33:00Z; the watchdog filed pane_dead 4s later and carded the operator."""
    sid = "4ee157f1-d402-4304-96ff-bdc764a2fcfd"
    path = _write_transcript(tmp_path, sid, _EXIT_LINES)
    monkeypatch.setattr(RA, "_transcript_path", lambda s: path if s == sid else None)
    ev = {"process_state": {"g49": []}, "pane_dead": {"g49": True},
          "attached": {"g49": False}, "screen": {}, "sids": {"g49": sid}}
    assert RA.classify(ev, "g49") is None


def test_dead_pane_without_a_clean_exit_is_still_pane_dead(tmp_path, monkeypatch):
    sid = "deadbeef-0000-0000-0000-000000000000"
    path = _write_transcript(tmp_path, sid, [
        {"type": "assistant", "message": {"role": "assistant", "content": "still working"}}])
    monkeypatch.setattr(RA, "_transcript_path", lambda s: path if s == sid else None)
    ev = {"process_state": {"s": []}, "pane_dead": {"s": True},
          "attached": {"s": False}, "screen": {}, "sids": {"s": sid}}
    assert RA.classify(ev, "s")["class"] == "pane_dead"


def test_exit_buried_under_later_work_is_still_pane_dead(tmp_path, monkeypatch):
    """A resumed sid appends: an /exit from a PREVIOUS life must not excuse today's crash."""
    sid = "resumed00-0000-0000-0000-000000000000"
    path = _write_transcript(tmp_path, sid, _EXIT_LINES + [
        {"type": "assistant", "message": {"role": "assistant", "content": f"turn {i}"}} for i in range(8)])
    monkeypatch.setattr(RA, "_transcript_path", lambda s: path if s == sid else None)
    ev = {"process_state": {"s": []}, "pane_dead": {"s": True},
          "attached": {"s": False}, "screen": {}, "sids": {"s": sid}}
    assert RA.classify(ev, "s")["class"] == "pane_dead"


def test_no_sid_or_missing_transcript_stays_pane_dead(monkeypatch):
    monkeypatch.setattr(RA, "_transcript_path", lambda s: None)
    ev = {"process_state": {"s": []}, "pane_dead": {"s": True},
          "attached": {"s": False}, "screen": {}, "sids": {}}
    assert RA.classify(ev, "s")["class"] == "pane_dead"


def test_a_human_shaped_report_has_no_pattern_class(store):
    """The category ios-watch-dev's phone exposed: nothing in this suite filed a report the
    way a person does, so every code path that assumed `class` is a string had no caller
    that could reach it. This fixture is that caller."""
    d = RA.report(reported_by="user", channel="ios", severity="bug", seats=["gm"],
                  symptom="the app said it could not reach you", capture=None)
    assert d["class"] is None and d["evidence"] == {} and d["severity"] == "bug"
    assert RA.show(d["id"])["class"] is None


# --- hardening for exited_cleanly (landed by another seat; this closes its one gap) ---
# Their tail-of-5 `any()` excused a crash whenever an /exit sat a few lines above later
# work (a resumed sid, or an /exit the user cancelled). Last decisive line wins instead.

def test_exit_does_not_excuse_a_crash_that_came_after_more_work(tmp_path, monkeypatch):
    path = _write_transcript(tmp_path, "sidW", _EXIT_LINES + [
        {"type": "user", "message": {"role": "user", "content": "resumed, continue"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]}},
    ])
    monkeypatch.setattr(RA, "_transcript_path", lambda s: path if s == "sidW" else None)
    assert RA.exited_cleanly("sidW") is False
    ev = fake_evidence(["s"])
    ev["process_state"]["s"] = []
    ev["pane_dead"] = {"s": True}
    ev["sids"] = {"s": "sidW"}
    assert RA.classify(ev, "s")["class"] == "pane_dead"


def test_exit_still_excuses_a_pane_that_died_right_after_it(tmp_path, monkeypatch):
    path = _write_transcript(tmp_path, "sidX", _EXIT_LINES)
    monkeypatch.setattr(RA, "_transcript_path", lambda s: path if s == "sidX" else None)
    assert RA.exited_cleanly("sidX") is True
