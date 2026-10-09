"""Claude Code 2.1.286+ draws a permission prompt's command BETWEEN `╌` dashed lines, inside the `─`
frame (real capture: fixtures/claude-2.1.295/perm_bash_dashed.pane.txt). _menu_context treated the
dashed line as the frame edge, so the context came back EMPTY: the operator's card read "Do you want
to proceed?" with no command, and two different commands got the SAME menu identity, which is the
stale-tap hazard the context exists to prevent (a tap meant for one prompt approving another).
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_spec = importlib.util.spec_from_file_location("agent_status_295", HERE / "agent-status.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)
import menu_bridge_core as core  # noqa: E402

FIX = HERE / "fixtures"


def _lines(name):
    return (FIX / name).read_text().split("\n")


def _menu(lines):
    m = A.parse_pending_menu(lines)
    assert m is not None
    return m


def test_a_2_1_295_bash_prompt_keeps_its_command_in_the_context():
    m = _menu(_lines("claude-2.1.295/perm_bash_dashed.pane.txt"))
    assert m["kind"] == "permission" and m["question"] == "Do you want to proceed?"
    ctx = m.get("context") or ""
    assert "touch /tmp/probe-2-1-295.txt" in ctx, ctx
    assert "Bash command" in ctx and "Create empty probe file in /tmp" in ctx
    assert "╌" not in ctx, "the dashed separators are not content"


def test_two_different_commands_never_share_an_identity():
    lines = _lines("claude-2.1.295/perm_bash_dashed.pane.txt")
    other = [l.replace("touch /tmp/probe-2-1-295.txt", "rm -rf /home/orchestra/data")
              .replace("Create empty probe file in /tmp", "Delete the data dir") for l in lines]
    a, b = _menu(lines), _menu(other)
    assert core.menu_identity(a["question"], a.get("context", "")) != core.menu_identity(b["question"], b.get("context", ""))


def test_the_tip_line_is_not_part_of_the_identity():
    # a hint under the title is the CLI's advice, not what the prompt is about: it must not move the key
    lines = _lines("claude-2.1.295/perm_bash_dashed.pane.txt")
    no_tip = [l for l in lines if not l.strip().startswith("Tip:")]
    a, b = _menu(lines), _menu(no_tip)
    assert core.menu_identity(a["question"], a.get("context", "")) == core.menu_identity(b["question"], b.get("context", ""))
    assert "Tip:" not in (a.get("context") or "")


def test_the_parallel_prompt_names_its_own_command():
    m = _menu(_lines("claude-2.1.295/perm_bash_dashed_parallel.pane.txt"))
    assert "touch /tmp/probe-a.txt" in (m.get("context") or "")


def test_the_2_1_295_question_menu_and_idle_screen_are_unchanged():
    m = _menu(_lines("claude-2.1.295/askuserquestion.pane.txt"))
    assert m["kind"] == "options" and m["question"] == "Which color?"
    assert [o["label"] if isinstance(o, dict) else o for o in m["options"]][:3] == ["Red", "Blue", "Green"]
    assert A.parse_pending_menu(_lines("claude-2.1.295/idle_manual_mode.pane.txt")) is None


def test_the_2_1_284_shape_still_parses_as_before():
    m = _menu(_lines("menus/perm_bash.pane.txt"))
    assert "curl -s https://example.com -o /tmp/ex.html" in (m.get("context") or "")
