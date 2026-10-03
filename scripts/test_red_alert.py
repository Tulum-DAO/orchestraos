#!/usr/bin/env python3
"""RED-first contract for red_alert.py — the RED ALERT crash-report standard.

Commission: prompts/red-alert-builder.md (the operator, 2026-09-17, after ^Z on the
harness bottom bar suspended gm). A crash report is ONE JSON file under
state/red-alert/<ts>-<slug>.json with the fixed schema in docs/RED_ALERT.md, the CLI is
scripts/red_alert.py (report / list / show / update / resolve / escalate), and the
classifier turns evidence (ps STAT, screen text, pane liveness) into a catalogued class
with an immediate repair the watchdog may perform itself.
"""
import json
import re
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


# --- screen classes match CLI banners only, never transcript prose ---

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
# clean /exit is not a crash (a green)
# ---------------------------------------------------------------------------
def _write_transcript(tmp_path, sid, lines):
    p = tmp_path / f"{sid}.jsonl"
    p.write_text("".join(json.dumps(l) + "\n" for l in lines))
    return str(p)


_EXIT_LINES = [
    {"type": "assistant", "message": {"role": "assistant", "content": "handoff written"}},
    {"type": "user", "message": {"role": "user", "content":
        "<command-name>/exit</command-name>\n<command-message>exit</command-message>"},
     "timestamp": "2026-01-01T00:00:00.000Z"},
    {"type": "user", "message": {"role": "user", "content": "<local-command-stdout>Goodbye!</local-command-stdout>"},
     "timestamp": "2026-01-01T00:00:00.000Z"},
]


def test_dead_pane_after_a_clean_exit_is_not_a_crash(tmp_path, monkeypatch):
    """A pane whose transcript ends in /exit was shut down GRACEFULLY — no crash.

    Whether the agent typed it or the fleet's reap path did (spawn-agent.sh --kill sends the
    same keystrokes) is invisible here and does not change the verdict.
    """
    sid = "cleanexit-0000-0000-0000-000000000000"
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


def test_a_reaped_green_and_a_self_retirement_are_indistinguishable_here(tmp_path, monkeypatch):
    """Guard against someone reading more into this signal than it carries
    (observed 2026-09-18): the fleet's reap types the same /exit keystrokes,
    so both shapes must return True — the function claims 'graceful shutdown', not 'the agent chose'."""
    reaped = _write_transcript(tmp_path, "sidR", [
        {"type": "assistant", "message": {"role": "assistant", "content": "holding"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}},
    ])
    monkeypatch.setattr(RA, "_transcript_path", lambda s: reaped if s == "sidR" else None)
    assert RA.exited_cleanly("sidR") is True
    farewell = _write_transcript(tmp_path, "sidS", [
        {"type": "assistant", "message": {"role": "assistant", "content": "handoff written, standing down"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}},
    ])
    monkeypatch.setattr(RA, "_transcript_path", lambda s: farewell if s == "sidS" else None)
    assert RA.exited_cleanly("sidS") is True


# ---------------------------------------------------------------------------
# exited_cleanly, exercised through the REAL _transcript_path
#
# Every pre-existing exited_cleanly test monkeypatches RA._transcript_path, which stubs out
# the only code path that was broken: the import inside it raised ModuleNotFoundError under
# CLI invocation, was swallowed by a bare `except Exception`, and made exited_cleanly return
# False for everything. A green suite therefore proved nothing. These tests patch one layer
# DEEPER (sid_invariants.find_transcript) so the real _transcript_path body — import,
# os.path.exists, str() — actually runs.
# ---------------------------------------------------------------------------
def _real_transcript(monkeypatch, tmp_path, records, name="t.jsonl"):
    """Write a transcript and route the REAL _transcript_path to it via sid_invariants."""
    p = tmp_path / name
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    import sid_invariants as SI
    monkeypatch.setattr(SI, "find_transcript", lambda sid: str(p))
    return p


def test_transcript_path_import_resolves_under_cli_invocation(monkeypatch, tmp_path):
    """A2: the sid_invariants import must resolve, not be swallowed into None.

    This is the regression guard for the defect that made exited_cleanly dead on the CLI
    path. It deliberately does NOT stub _transcript_path.
    """
    p = _real_transcript(monkeypatch, tmp_path, [{"type": "user", "x": 1}], name="imp.jsonl")
    assert RA._transcript_path("sid-import") == str(p)


def test_exited_cleanly_true_on_a_real_user_exit(monkeypatch, tmp_path):
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "assistant", "message": {"role": "assistant", "content": "working"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}},
    ])
    assert RA.exited_cleanly("sid-clean") is True


def test_d1_assistant_turn_quoting_the_exit_marker_is_not_a_clean_exit(monkeypatch, tmp_path):
    """D1: the old loop tested _EXIT_CMD before _WORKED_AFTER on the SAME line, so an
    assistant turn that merely QUOTES the marker read as a graceful shutdown. docs/RED_ALERT.md
    quotes it, so documenting the feature was enough to break it."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "explain the rule"}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": "if <command-name>/exit</command-name> is the last thing, it shut down gracefully"}},
    ])
    assert RA.exited_cleanly("sid-quoted") is False


def test_d2_a_real_exit_under_many_trailing_records_is_still_clean(monkeypatch, tmp_path):
    """D2: tail defaulted to 5 PHYSICAL lines, so a genuine /exit buried under trailing
    system records fell out of the window and was reported as a CRASH."""
    recs = [{"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}}]
    recs += [{"type": "system", "subtype": "notice", "n": i} for i in range(12)]
    _real_transcript(monkeypatch, tmp_path, recs)
    assert RA.exited_cleanly("sid-buried") is True


def test_d3_a_resume_after_an_exit_is_a_real_crash(monkeypatch, tmp_path):
    """D3: a stale /exit followed by a resume used to read as a clean exit. A user record
    after the /exit proves the seat was used again, so the later death is a real crash."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": "actually, keep going"}},
    ])
    assert RA.exited_cleanly("sid-resumed") is False


def test_exited_cleanly_false_when_no_transcript(monkeypatch, tmp_path):
    import sid_invariants as SI
    monkeypatch.setattr(SI, "find_transcript", lambda sid: None)
    assert RA.exited_cleanly("sid-none") is False


def test_post_exit_cli_echo_records_do_not_look_like_a_resume(monkeypatch, tmp_path):
    """Regression for a defect introduced while FIXING D3.

    A real /exit is followed by the CLI's own echo (`<local-command-stdout>Goodbye!</…>`) as a
    *user* record. A naive "any user record after the /exit means resumed" rule reported every
    clean shutdown as a crash — the same false-crash class as D2. Plumbing must be skipped.
    """
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "assistant", "message": {"role": "assistant", "content": "handoff written"}},
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>\n<command-message>exit</command-message>"}},
        {"type": "user", "message": {"role": "user", "content":
            "<local-command-stdout>Goodbye!</local-command-stdout>"}},
    ])
    assert RA.exited_cleanly("sid-echo") is True


# ---------------------------------------------------------------------------
# Round-6 regressions: four ways the FIRST rewrite still excused a real crash.
# All four were in the dangerous direction (crash excused, no report filed), and all four
# survived because no earlier test used a REAL user-side shape: a tool_result record, or a
# content-block list. The original fix matched _EXIT_CMD against the RAW JSON LINE, which is
# the same mistake as the code it replaced, moved from assistant records to user records.
# ---------------------------------------------------------------------------
def test_r6a_a_tool_result_quoting_the_marker_is_not_a_clean_exit(monkeypatch, tmp_path):
    """A tool_result is a USER-type record whose content is a list of blocks. Grepping
    docs/RED_ALERT.md (which quotes the marker at :73) must not excuse a later crash."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content":
             "docs/RED_ALERT.md:73: a terminal <command-name>/exit</command-name> (or /quit);"}]}},
    ])
    assert RA.exited_cleanly("sid-toolresult") is False


def test_r6b_a_human_prompt_quoting_the_marker_is_not_a_clean_exit(monkeypatch, tmp_path):
    """A resume whose prompt merely MENTIONS the marker must still read as a resume."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content":
            "please fix the docs: <command-name>/exit</command-name> is quoted at line 73"}},
    ])
    assert RA.exited_cleanly("sid-quoting-prompt") is False


def test_r6c_mismatched_plumbing_tags_cannot_swallow_a_real_prompt(monkeypatch, tmp_path):
    """The plumbing pattern must back-reference its tag name. Alternating open and close
    independently let one 'pair' span a real prompt and skip it as non-decisive."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content":
            "<command-message>compact</command-message> actually keep going and fix the bug"
            " <command-args>none</command-args>"}},
    ])
    assert RA.exited_cleanly("sid-mismatched") is False


def test_r6d_a_resume_whose_first_record_is_a_tool_result_is_not_a_clean_exit(monkeypatch, tmp_path):
    """A tool_result block has no 'text' key, so joined text is empty. Empty must mean
    'not decisive', never 'clean exit'."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": "keep going"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "resuming"}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}},
    ])
    assert RA.exited_cleanly("sid-resume-toolresult") is False


def test_a_real_slash_command_record_shape_is_a_clean_exit(monkeypatch, tmp_path):
    """The real on-disk shape of a slash command: command-name + command-message + command-args,
    nothing else. Entirely plumbing, so it IS the exit."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "assistant", "message": {"role": "assistant", "content": "handoff written"}},
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>\n<command-message>exit</command-message>\n"
            "<command-args></command-args>"}},
    ])
    assert RA.exited_cleanly("sid-real-shape") is True


def test_tail_lines_reads_from_the_end_not_the_whole_file(tmp_path):
    """_tail_lines must not pull a 40MB transcript into memory to slice 400 lines."""
    p = tmp_path / "big.jsonl"
    with open(p, "w") as fh:
        for i in range(5000):
            fh.write(json.dumps({"type": "system", "n": i, "pad": "x" * 200}) + "\n")
    got = RA._tail_lines(str(p), 10)
    assert len(got) == 10
    assert json.loads(got[-1])["n"] == 4999


# ---------------------------------------------------------------------------
# Round-7 regressions: two more wrong verdicts, one dangerous, one reachable.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("weird", [
    {"role": "user", "content": {"text": "keep going"}},                 # content as a bare dict
    {"role": "user", "content": ["keep going"]},                         # list of raw strings
    {"role": "user"},                                                    # no content key at all
])
def test_r7_an_unrecognised_content_shape_fails_safe(monkeypatch, tmp_path, weird):
    """An unknown content shape must NOT be skipped: skipping lets an older /exit excuse the
    death that followed. It must read as prose and report a crash."""
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": weird},
    ])
    assert RA.exited_cleanly("sid-weird-shape") is False


def test_r7_tail_keeps_a_complete_first_line_on_an_exact_boundary(tmp_path):
    """The old code dropped the first line unconditionally, so a budget landing exactly on a
    line boundary discarded a COMPLETE line — and if that line held the /exit, a clean shutdown
    was reported as a crash."""
    p = tmp_path / "exact.jsonl"
    line = json.dumps({"type": "user", "n": 1}) + "\n"
    p.write_text(line * 4)
    # budget cut exactly at a line start: the first retained line must survive intact
    got = RA._tail_lines(str(p), 10, budget=len(line) * 2)
    assert len(got) == 2
    assert all(json.loads(g)["n"] == 1 for g in got)


def test_r7_tail_keeps_the_fragment_when_one_line_exceeds_the_budget(tmp_path):
    """A single line larger than the budget used to yield [], which made exited_cleanly report a
    crash against a seat that had shut down cleanly. A real transcript on this host has a
    40.47MB single line."""
    p = tmp_path / "huge.jsonl"
    p.write_text("x" * 5000 + "\n")
    got = RA._tail_lines(str(p), 10, budget=1000)
    assert got != []
    assert got[-1].startswith("x")


def test_r7_tail_survives_a_split_multibyte_character(tmp_path):
    """A multi-byte character split at the seek boundary must not raise."""
    p = tmp_path / "utf8.jsonl"
    p.write_bytes(("é" * 500 + "\n" + json.dumps({"type": "user", "n": 2}) + "\n").encode())
    got = RA._tail_lines(str(p), 10, budget=701)   # lands mid-character
    assert json.loads(got[-1])["n"] == 2


# ---------------------------------------------------------------------------
# THE FAIL-SAFE INVARIANT (gm ruling msg_1f665c10)
#
# Every defect found across seven review rounds failed in the SAME direction: a real crash
# silently excused and no report filed -- the quiet, dangerous way. The invariant that forecloses
# the whole class, asserted here as a property rather than hoped for:
#
#     exited_cleanly may return True ONLY when a user record is POSITIVELY PROVEN to be entirely
#     command plumbing containing the exit marker. Every uncertain, unparseable or unknown shape
#     must return False.
#
# A parsing gap may therefore cost a FALSE ALARM (noisy, safe) but never a silently-excused crash
# (quiet, dangerous).
# ---------------------------------------------------------------------------
def _independently_is_allplumbing_exit(content) -> bool:
    """Recomputed WITHOUT the production helpers, so the test cannot inherit their bugs.

    True only when content is a string, holds /exit or /quit inside a command-name tag, and every
    non-whitespace character outside the known plumbing tag pairs is gone.
    """
    if isinstance(content, list):
        # A block list is admissible: pull the text blocks with deliberately simple logic of our
        # own (tool_result blocks are the harness talking to itself, never human input).
        parts = [b.get("text") for b in content
                 if isinstance(b, dict) and b.get("type") != "tool_result"
                 and isinstance(b.get("text"), str)]
        if not parts:
            return False
        content = "".join(parts)
    if not isinstance(content, str):
        return False
    if not re.search(r"<command-name>\s*/(exit|quit)\s*</command-name>", content):
        return False
    stripped = content
    for tag in ("local-command-stdout", "local-command-stderr", "command-message",
                "command-args", "command-name"):
        stripped = re.sub(rf"<{tag}>.*?</{tag}>", "", stripped, flags=re.S)
    # An EMPTY residue only counts as skippable plumbing if plumbing was actually REMOVED. Empty
    # or whitespace-only content proves nothing and must fail safe. A previous revision of this
    # oracle omitted the `stripped != content` clause and so legitimised exactly that hole
    # instead of making the code fail safe -- the review caught it.
    return stripped.strip() == "" and stripped != content



def _independently_is_legitimately_skippable(content) -> bool:
    """A record that exited_cleanly MAY skip without excusing a crash.

    Two kinds, both recomputed independently of the production helpers:
      * a block list of pure tool_results -- the harness talking to itself, never a human;
      * content that is ENTIRELY command plumbing with no prose left over -- the CLI's own
        post-/exit echo, e.g. <local-command-stdout>Goodbye!</local-command-stdout>.
    """
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return True
    if isinstance(content, list):
        parts = [b.get("text") for b in content
                 if isinstance(b, dict) and b.get("type") != "tool_result"
                 and isinstance(b.get("text"), str)]
        if not parts:
            return False
        content = "".join(parts)
    if not isinstance(content, str):
        return False
    # ONLY the CLI's own OUTPUT echo may be skipped. The previous form stripped command-name,
    # command-message and command-args too, so it exempted a /compact or /model record -- a human
    # using the seat again -- and formally blessed the production hole this property exists to
    # foreclose. It also lacked the "something was actually removed" clause that its sibling
    # oracle has, so it waved through empty and whitespace-only content as well.
    stripped = content
    for tag in ("local-command-stdout", "local-command-stderr"):
        stripped = re.sub(rf"<{tag}>.*?</{tag}>", "", stripped, flags=re.S)
    return stripped.strip() == "" and stripped != content


# Every shape we can think of, well-formed and malformed. The point is NOT that each has a known
# answer -- it is that True is only ever reachable through positive proof.
_SHAPES = [
    "<command-name>/exit</command-name>",
    "<command-name>/quit</command-name>",
    "<command-name>/exit</command-name>\n<command-message>exit</command-message>",
    "<command-name>/exit</command-name>\n<command-args></command-args>",
    "  <command-name>/exit</command-name>  ",
    "<command-name>/exit</command-name>\r\n<command-message>exit</command-message>",
    "please read <command-name>/exit</command-name> at line 74",
    "<command-message>compact</command-message> keep going <command-args>none</command-args>",
    "<command-name>/exit</command-name> and then keep working",
    "<local-command-stdout>Goodbye!</local-command-stdout>",
    "<command-name>/exit<command-name>/exit</command-name></command-name>",
    "<command-name>/exit</command-name",                       # unclosed
    "keep going",
    "   ",
    "",
    "<command-name>/model</command-name>\n<command-message>model</command-message>",
    [{"type": "tool_result", "tool_use_id": "t", "content": "<command-name>/exit</command-name>"}],
    [{"type": "text", "text": "<command-name>/exit</command-name>"}],
    [{"type": "text", "text": "   "}],
    [{"type": "tool_result", "content": "x"}, {"type": "text", "text": "keep going"}],
    [{"type": "thinking", "thinking": "quiet"}],
    ["<command-name>/exit</command-name>"],                    # raw string in block list
    "<local-command-stdout>ok</local-command-stdout>\n<command-name>/compact</command-name>",
    "<local-command-stdout>ok</local-command-stdout>\n<command-args></command-args>",
    [],                                                        # empty block list
    {"text": "<command-name>/exit</command-name>"},            # content as a dict
    None,
    42,
]


@pytest.mark.parametrize("shape", _SHAPES, ids=range(len(_SHAPES)))
def test_fail_safe_invariant_true_requires_positive_allplumbing_proof(monkeypatch, tmp_path, shape):
    rec = {"type": "user", "message": {"role": "user"}}
    if shape is not None or True:
        rec["message"]["content"] = shape
    _real_transcript(monkeypatch, tmp_path, [rec], name="inv.jsonl")
    got = RA.exited_cleanly("sid-invariant")
    assert got in (True, False)
    if got is True:
        assert _independently_is_allplumbing_exit(shape), (
            f"INVARIANT VIOLATED: returned True (crash excused) for a shape that is not provably "
            f"all-plumbing: {shape!r}"
        )


def test_fail_safe_invariant_holds_with_an_older_exit_above(monkeypatch, tmp_path):
    """The dangerous composition: a real /exit EARLIER in the tail, then an odd record. If the odd
    record is skipped as non-decisive, the stale /exit excuses the death. Only a pure tool_result
    list may legitimately be skipped."""
    for shape in _SHAPES:
        _real_transcript(monkeypatch, tmp_path, [
            {"type": "user", "message": {"role": "user", "content":
                "<command-name>/exit</command-name>"}},
            {"type": "user", "message": {"role": "user", "content": shape}},
        ], name="inv2.jsonl")
        got = RA.exited_cleanly("sid-invariant-2")
        if got is True:
            assert (_independently_is_allplumbing_exit(shape)
                    or _independently_is_legitimately_skippable(shape)), (
                f"INVARIANT VIOLATED: a stale /exit excused the death because {shape!r} was "
                f"skipped as non-decisive"
            )


@pytest.mark.parametrize("empty", ["", "   ", "\n", []])
def test_r8_empty_content_proves_nothing_and_must_fail_safe(monkeypatch, tmp_path, empty):
    """Round 8. Residue is empty for TWO different reasons and conflating them was a real hole:
    the CLI's post-/exit echo genuinely held plumbing tags, whereas empty or whitespace-only
    content held nothing at all and proved nothing. The second was being SKIPPED, so a stale
    /exit above still excused the later death.

    Worse, when the invariant test first went red on these shapes I WIDENED ITS ORACLE to bless
    the behaviour instead of fixing the code. The review caught that. The oracle now requires
    that plumbing was actually removed, and these shapes fail safe.
    """
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": empty}},
    ], name="r8.jsonl")
    assert RA.exited_cleanly("sid-empty") is False


def test_r8_the_legitimate_skips_still_work(monkeypatch, tmp_path):
    """The two records that MAY be skipped must keep being skipped, or this fix would trade a
    silent-excuse bug for a false-alarm bug."""
    for shape in ([{"type": "tool_result", "tool_use_id": "t", "content": "x"}],
                  "<local-command-stdout>Goodbye!</local-command-stdout>"):
        _real_transcript(monkeypatch, tmp_path, [
            {"type": "user", "message": {"role": "user", "content":
                "<command-name>/exit</command-name>"}},
            {"type": "user", "message": {"role": "user", "content": shape}},
        ], name="r8b.jsonl")
        assert RA.exited_cleanly("sid-legit-skip") is True, shape


@pytest.mark.parametrize("cmd", [
    "<command-name>/compact</command-name>",
    "<command-name>/compact</command-name>\n<command-message>compact</command-message>",
    "<command-name>/model</command-name>\n<command-message>model</command-message>",
    "<command-args></command-args>",
])
def test_r9_another_slash_command_after_an_exit_is_the_seat_being_used_again(
        monkeypatch, tmp_path, cmd):
    """Round 9, a LIVE production hole. _exit_verdict skipped ANY all-plumbing record that lacked
    the exit marker, so /compact and /model -- a human using the seat again -- were waved through
    and a stale /exit anywhere in the 400-line tail excused the death.

    An out-of-memory death during compaction leaves no assistant turn after the /compact, which is
    exactly this arrangement: the crash that most needs reporting was the one being excused.

    Only the CLI's own OUTPUT echo (local-command-stdout/stderr) may be skipped.
    """
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": cmd}},
    ], name="r9.jsonl")
    assert RA.exited_cleanly("sid-other-command") is False


# ---------------------------------------------------------------------------
# Round-10 coverage gaps. The shipped behaviour was already CORRECT on all three of these; what
# was missing was any test that would notice if a future refactor broke them. Each corresponds to
# a single-line mutation that survived all 143 tests while flipping a verdict to "clean exit".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("composite", [
    "<local-command-stdout>ok</local-command-stdout>\n<command-name>/compact</command-name>",
    "<local-command-stdout>ok</local-command-stdout>\n<command-args></command-args>",
    "<command-name>/model</command-name>\n<local-command-stderr>warn</local-command-stderr>",
])
def test_r10_an_echo_beside_a_command_is_still_the_seat_being_used(monkeypatch, tmp_path, composite):
    """One record holding BOTH the CLI's output echo AND a command tag must NOT be skipped.

    The echo-skip must require that the echo is ALL there is. Testing only that an echo is PRESENT
    puts the round-9 hole one composition step away, and the shape matrix had no entry combining
    an echo tag with a command tag, so nothing would have noticed.
    """
    _real_transcript(monkeypatch, tmp_path, [
        {"type": "user", "message": {"role": "user", "content":
            "<command-name>/exit</command-name>"}},
        {"type": "user", "message": {"role": "user", "content": composite}},
    ], name="r10a.jsonl")
    assert RA.exited_cleanly("sid-echo-plus-command") is False


def test_r10_an_unparseable_tail_line_carrying_prose_is_not_skipped(monkeypatch, tmp_path):
    """A truncated, non-JSON tail line that still carries human prose must report, not be skipped.

    If the unparseable branch degraded to a bare `continue`, a stale /exit above would excuse the
    death -- and a truncated final line is exactly what a hard kill leaves behind.
    """
    p = tmp_path / "trunc.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content":
                    "<command-name>/exit</command-name>"}}) + "\n"
        + '{"type": "user", "message": {"role": "user", "content": "actually keep go'
    )
    import sid_invariants as SI
    monkeypatch.setattr(SI, "find_transcript", lambda sid: str(p))
    assert RA.exited_cleanly("sid-truncated") is False


def test_r10_an_unreadable_transcript_reports_rather_than_excuses(monkeypatch, tmp_path):
    """If the transcript cannot be read at all, nothing is proven, so it must report a crash.

    The OSError guard returning True would excuse every crash whose transcript had been rotated,
    chmodded or removed -- and nothing covered that path.
    """
    p = tmp_path / "gone.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content":
                 "<command-name>/exit</command-name>"}}) + "\n")
    import sid_invariants as SI
    monkeypatch.setattr(SI, "find_transcript", lambda sid: str(p))
    assert RA.exited_cleanly("sid-readable") is True        # control: readable => clean exit
    monkeypatch.setattr(RA, "_tail_lines",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
    assert RA.exited_cleanly("sid-unreadable") is False
