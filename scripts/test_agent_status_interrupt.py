"""A terminal Esc must read IDLE, not 'working' for 300s (Shaw field report 2026-10-07).

Imports the real detector. The helper under test is the authoritative-source half of the fix;
the hook-merge half is covered by the integration test at the bottom.
"""
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def A():
    spec = importlib.util.spec_from_file_location("agent_status_ut",
                                                  os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _jsonl(tmp_path, *entries):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(p)


def _user_text(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant(text="ok"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_the_interrupt_marker_reads_interrupted(A, tmp_path):
    p = _jsonl(tmp_path, _user_text("do a thing"), _assistant(),
               _user_text("[Request interrupted by user]"))
    assert A._transcript_tail_is_interrupted(p) is True


def test_the_for_tool_use_variant_counts_too(A, tmp_path):
    p = _jsonl(tmp_path, _assistant(), _user_text("[Request interrupted by user for tool use]"))
    assert A._transcript_tail_is_interrupted(p) is True


def test_IT_SELF_CLEARS_once_the_operator_types_again(A, tmp_path):
    # The whole reason the newest user entry DECIDES rather than continues scanning: Shaw
    # interrupts, then sends a photo or a message, and the seat must read working again.
    p = _jsonl(tmp_path, _assistant(), _user_text("[Request interrupted by user]"),
               _user_text("reply ready"))
    assert A._transcript_tail_is_interrupted(p) is False


def test_an_assistant_turn_after_the_interrupt_wins(A, tmp_path):
    p = _jsonl(tmp_path, _user_text("[Request interrupted by user]"), _assistant())
    assert A._transcript_tail_is_interrupted(p) is False


def test_a_tool_result_tail_is_MID_FLIGHT_not_interrupted(A, tmp_path):
    p = _jsonl(tmp_path, _user_text("[Request interrupted by user]"),
               {"type": "user", "message": {"content": [{"type": "tool_result", "content": ""}]}})
    assert A._transcript_tail_is_interrupted(p) is False


def test_pytest_output_is_NOT_an_interrupt(A, tmp_path):
    # 236 live occurrences of this shape. A screen grep for 'Interrupted' would call a seat
    # running tests an interrupted seat; the marker is anchored for exactly this reason.
    p = _jsonl(tmp_path, _assistant(),
               _user_text("Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!"))
    assert A._transcript_tail_is_interrupted(p) is False


def test_an_ordinary_prompt_is_not_an_interrupt(A, tmp_path):
    p = _jsonl(tmp_path, _assistant(), _user_text("please keep going"))
    assert A._transcript_tail_is_interrupted(p) is False


def test_meta_and_sidechain_rows_are_skipped(A, tmp_path):
    p = _jsonl(tmp_path, _assistant(), _user_text("[Request interrupted by user]"),
               {"type": "user", "isMeta": True,
                "message": {"content": [{"type": "text", "text": "noise"}]}},
               {"type": "user", "isSidechain": True,
                "message": {"content": [{"type": "text", "text": "noise"}]}})
    assert A._transcript_tail_is_interrupted(p) is True


def test_unreadable_or_absent_fails_CLOSED_to_todays_behaviour(A, tmp_path):
    # Fail-closed means "keep promoting to working": a live turn must never be masked.
    assert A._transcript_tail_is_interrupted(None) is False
    assert A._transcript_tail_is_interrupted(str(tmp_path / "nope.jsonl")) is False
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n[also not\n")
    assert A._transcript_tail_is_interrupted(str(bad)) is False


# ---- THE WIRING, not just the helper -----------------------------------------
# My first version of this file tested only _transcript_tail_is_interrupted. Deleting the
# ENTIRE hook-merge branch — the actual fix — left all 9 of those tests green. A gate that
# cannot fail is not a gate, so the merge gets driven end to end here, through the real
# parser and the real override, with no stubs but the pane/process boundary.
import time as _time


def _wire(A, monkeypatch, tmp_path, tail_entries, hook_state="working", hook_age=0.0,
          screen="", hook_event="PreToolUse"):
    home = tmp_path / "home"
    cwd = "/home/user/repos/demo"
    slug = A._project_slug(cwd)
    proj = home / ".claude" / "projects" / slug
    proj.mkdir(parents=True)
    sid = "sess-abc"
    (proj / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in tail_entries) + "\n")
    monkeypatch.setattr(A, "check_claude_process",
                        lambda s: {"running": True, "cpu": 1.0, "pid": 1234,
                                   "rss_mb": 100, "elapsed": "01:00"})
    monkeypatch.setattr(A, "get_tmux_output", lambda s, *a, **k: screen)  # default: NO turn
    monkeypatch.setattr(A, "EVENTS_DIR", str(tmp_path))
    monkeypatch.setattr(A, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(A, "get_pane_id", lambda s: "%9")
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    with open(os.path.join(str(tmp_path), "9.json"), "w") as f:
        json.dump({"event": hook_event, "state": hook_state,
                   "ts": _time.time() - hook_age, "tool": "Bash",
                   "cwd": cwd, "session_id": sid}, f)
    return A.get_agent_status("esc-sess")


def test_END_TO_END_a_terminal_Esc_reads_IDLE_not_working(A, monkeypatch, tmp_path):
    # Shaw's exact case: Esc pressed in the terminal, no Stop hook fired, hook still says
    # 'working' and is fresh. Before the fix this reported working/"Active turn"/hook.
    st = _wire(A, monkeypatch, tmp_path,
               [_assistant(), _user_text("[Request interrupted by user]")])
    assert st["state"] != "working", "an interrupted seat must not report an active turn"
    assert "Active turn" not in (st["activity"] or "")
    assert st["confidence"] != "hook"


def test_END_TO_END_POSITIVE_CONTROL_a_real_live_turn_is_still_rescued(A, monkeypatch, tmp_path):
    # The control that proves the branch above is a DISTINCTION and not a blanket off-switch:
    # same stale-screen situation, transcript NOT interrupted -> F2 must still promote.
    st = _wire(A, monkeypatch, tmp_path,
               [_user_text("do a thing"), _assistant()])
    assert st["state"] == "working", "F2 must still rescue a genuinely live turn"
    assert st["confidence"] == "hook"


def test_END_TO_END_it_self_clears_when_the_operator_replies(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path,
               [_assistant(), _user_text("[Request interrupted by user]"),
                _user_text("reply ready")])
    assert st["state"] == "working", "a fresh submit after an interrupt is a live turn again"


# ---- Esc on an AskUserQuestion MENU (Shaw P0, gm msg_cad79aff, 2026-10-07) --------------------
# The menu fires a Notification hook -> the pane event says 'waiting_permission'. Esc fires NO
# hook, so the override that turns an idle screen into waiting_permission held the seat for the
# full HOOK_WAITING_TTL_S (900 s) and the gateway answered every send with 409 "busy". Reproduced
# by effect on a scratch seat (v2.1.284): screen idle, composer EMPTY ('❯' + NBSP), transcript tail
# '[Request interrupted by user for tool use]', hook Notification/waiting_permission.
# gm's hypothesis (residue in the composer) was DISPROVED by the raw bytes: the composer is empty.
# The fixture IS that screen, captured with capture-pane -p -e, the scratch path scrubbed.
_FIXTURE = os.path.join(_HERE, "fixtures", "esc-auq", "post_esc_idle_v2.1.284.ansi")


def _post_esc_screen():
    with open(_FIXTURE, encoding="utf-8") as f:
        return f.read()


def _with_typed(screen, text):
    # Real typed text renders in the DEFAULT style right after the '❯'+NBSP prompt.
    lines = screen.split("\n")
    idx = max(i for i, l in enumerate(lines) if "\u276f\u00a0" in l)
    lines[idx] = lines[idx].rstrip() + text
    return "\n".join(lines)


_ESC_ON_AUQ_TAIL = [
    _user_text("ask me two questions"),
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": {}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": "The user doesn't want to proceed with this tool use."}]}},
    _user_text("[Request interrupted by user for tool use]"),
]


def test_the_fixture_is_an_IDLE_screen_with_an_EMPTY_composer(A):
    # Pins the measurement the fix rests on, so a parser change cannot silently move it.
    st = A.parse_status(_post_esc_screen())
    assert st["state"] == "idle", st
    assert A.parse_status(_with_typed(_post_esc_screen(), "x y"))["state"] == "stranded_input"


def test_END_TO_END_Esc_on_an_AUQ_menu_reads_IDLE_not_waiting(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path, _ESC_ON_AUQ_TAIL, hook_state="waiting_permission",
               hook_age=5.0, screen=_post_esc_screen(), hook_event="Notification")
    assert st["state"] == "idle", st
    assert st["confidence"] != "hook"


def test_END_TO_END_POSITIVE_CONTROL_a_pending_permission_still_holds(A, monkeypatch, tmp_path):
    # Same idle screen, same fresh waiting hook, but the transcript tail is the tool_use itself:
    # the menu is (as far as we can tell) still up and the screen merely missed it. The hook must
    # still hold the seat. This is what proves the fix is a distinction, not an off-switch.
    st = _wire(A, monkeypatch, tmp_path, _ESC_ON_AUQ_TAIL[:2], hook_state="waiting_permission",
               hook_age=5.0, screen=_post_esc_screen(), hook_event="Notification")
    assert st["state"] == "waiting_permission", st
    assert st["confidence"] == "hook"


def test_END_TO_END_real_typed_text_after_the_Esc_is_STILL_stranded(A, monkeypatch, tmp_path):
    # Never read real typed text as idle (gm's condition).
    st = _wire(A, monkeypatch, tmp_path, _ESC_ON_AUQ_TAIL, hook_state="waiting_permission",
               hook_age=5.0, screen=_with_typed(_post_esc_screen(), "half a message"),
               hook_event="Notification")
    assert st["state"] == "stranded_input", st
    assert "half a message" in json.dumps(st)


def test_END_TO_END_Esc_on_AUQ_self_clears_when_the_operator_replies(A, monkeypatch, tmp_path):
    st = _wire(A, monkeypatch, tmp_path, _ESC_ON_AUQ_TAIL + [_user_text("reply ready")],
               hook_state="waiting_permission", hook_age=5.0, screen=_post_esc_screen(),
               hook_event="Notification")
    assert st["state"] == "waiting_permission", "a new submit ends the interrupt; the hook stands"
