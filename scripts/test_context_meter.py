"""A seat's context % is read off ITS OWN status bar, or not at all.

Operator report, 2026-10-09: the approvals UI showed a seat at 99% context, later 48%, with no
rotation; the operator stopped a working seat thinking it was about to overflow. Its real usage was
39% (the status line shows usage scaled to 80%, so it drew 49%). The screen reader took the
BOTTOM-MOST line anywhere on the screen that carried a meter glyph and a '%'. While a menu is
open (AskUserQuestion, a permission prompt), Claude Code does not draw the status bar, so that
scan fell through to whatever sat above it: tool output with a progress bar, or a meter the agent
quoted. The lineage daemon's fallback (`enrich._capture_status_line`) read the same way.

Both now take the meter only from the footer: the lines under the composer box (the last frame
rule, closing a box that starts with the `❯` composer).
No composer box, or no meter under it -> ''.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_HERE, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from context_meter import footer_meter_line  # noqa: E402
from scripts.lineage_daemon.enrich import _capture_status_line  # noqa: E402

RULE = "─" * 80
# Our status bar as a live pane draws it (statusLine command: model | project | meter).
BAR = "  Opus 5.5 (1M context) │ orchestraos █████░░░░░ 49%"
MODE = "  ⏵⏵ bypass permissions on · 1 shell"
# Output an agent produced in this turn: a progress bar, and a meter it quoted.
FOREIGN = [
    "● Bash(./upload.sh)",
    "  ⎿  uploading ██████████ 99%",
    "● The other seat's bar read: Opus 5.5 (1M context) │ web ██████████ 97%",
]
IDLE = ["✻ Worked for 3s", *FOREIGN, RULE, "❯ ", RULE, BAR, MODE]
# Claude Code 2.1.295 AskUserQuestion (shape of a real capture): no composer, no status bar.
AUQ = [*FOREIGN, RULE, " ☐ Color", "Which color?", "❯ 1. Red", "     Warm", "  2. Blue",
       "     Cool", "  3. Type something.", RULE, "  4. Chat about this",
       "Enter to select · ↑/↓ to navigate · Esc to cancel"]
PERMISSION = [*FOREIGN, RULE, " Bash command", "", "   touch /tmp/x", "", " Do you want to proceed?",
              " ❯ 1. Yes", "   2. No", "", " Esc to cancel · Tab to amend"]


def _agent_status():
    spec = importlib.util.spec_from_file_location("agent_status_ctx", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_footer_meter_is_read_when_the_status_bar_is_drawn():
    assert footer_meter_line(IDLE) == BAR
    assert _agent_status().parse_status("\n".join(IDLE))["context_pct"] == "49%"
    assert _capture_status_line("s", lambda _s: "\n".join(IDLE)) == BAR


def test_a_menu_hides_the_bar_and_nothing_above_it_is_read_instead():
    status = _agent_status()
    for name, screen in (("AskUserQuestion", AUQ), ("permission", PERMISSION)):
        assert footer_meter_line(screen) == "", name
        assert status.parse_status("\n".join(screen))["context_pct"] == "", name
        assert _capture_status_line("s", lambda _s, sc=screen: "\n".join(sc)) == "", name


def test_a_rule_inside_the_conversation_does_not_open_a_footer():
    # Agent output draws separators too; only the LAST rule on the screen bounds the footer.
    screen = ["● Summary", RULE, "  ⎿  uploading ██████████ 99%", *AUQ]
    assert footer_meter_line(screen) == ""
    assert _agent_status().parse_status("\n".join(screen))["context_pct"] == ""


def test_an_earlier_composer_frame_left_on_screen_is_not_the_live_box():
    # A redraw can leave an old box in the visible history; the live box is the LAST one.
    screen = [RULE, "❯ earlier prompt", RULE, "  ⎿  ██████████ 99%", *AUQ]
    assert footer_meter_line(screen) == ""


def test_a_highlighted_option_right_under_a_rule_is_not_a_composer():
    # Review (agy, #333): a menu with no header line puts "❯ 1." directly under its frame.
    screen = [RULE, "❯ 1. Proceed", "  2. Stop", RULE, "  ⎿  ██████████ 90%"]
    assert footer_meter_line(screen) == ""


def test_a_typed_message_containing_a_rule_still_finds_the_bar():
    # Review (agy, #333): a rule-like line typed into the composer is inside the box.
    screen = [*FOREIGN, RULE, "❯ notes:", "  " + "─" * 20, "  more", RULE, BAR, MODE]
    assert footer_meter_line(screen) == BAR


def test_a_glyph_row_without_a_percent_is_not_the_meter():
    screen = [RULE, "❯ ", RULE, "  ░░ syncing", BAR]
    assert footer_meter_line(screen) == BAR


def test_menu_content_under_the_menu_frame_is_not_the_footer():
    # A permission prompt shows the command under its frame, and option labels are any text.
    cmd = [RULE, " Bash command", "", "   printf 'Opus 5.5 (1M context) │ x █████████░ 91%'", " ❯ 1. Yes", "   2. No"]
    auq = [RULE, " ☐ Context", "Which?", "❯ 1. Red", RULE, "  2. Context ██████████ 98%", "Enter to select"]
    for screen in (cmd, auq):
        assert footer_meter_line(screen) == "", screen
        assert _agent_status().parse_status("\n".join(screen))["context_pct"] == ""


def test_a_wrapped_composer_still_anchors_the_footer():
    screen = [RULE, "❯ a long typed message that wraps", "  onto a second row", "  and a third", RULE, BAR, MODE]
    assert footer_meter_line(screen) == BAR


def test_output_below_the_conversation_but_above_the_box_is_not_the_footer():
    # A meter printed just above the composer (spinner zone) is still output, not our bar.
    screen = [*FOREIGN, RULE, "❯ ", RULE, MODE]
    assert footer_meter_line(screen) == ""
    assert _agent_status().parse_status("\n".join(screen))["context_pct"] == ""


def test_the_auto_compact_form_still_reads_as_used():
    screen = [RULE, "❯ ", RULE, "  Opus 5.5 (1M context) │ orchestraos 💀 ██████████ 3% until auto-compact"]
    assert _agent_status().parse_status("\n".join(screen))["context_pct"] == "97%"


def test_trailing_blank_rows_under_the_footer_do_not_hide_it():
    assert footer_meter_line([*IDLE, "", "", ""]) == BAR


def test_no_rule_at_all_reads_nothing():
    assert footer_meter_line(FOREIGN) == ""
    assert footer_meter_line([]) == ""
