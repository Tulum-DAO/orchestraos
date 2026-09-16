# RED-first tests for the THREE-RUNTIME PANE-INJECT INVARIANT (Bug 1).
#
# The live bug (gemini-gm dx msg_d30b98a6/msg_4fbc8aee, the operator-ratified): inject_message sent
# text+Enter in ONE `send-keys '{text}' Enter`, then decided "landed" by whether the message text
# was VISIBLE anywhere in the pane, and on doubt RE-RAN the whole paste. Three failures:
#   - Gemini/AGY: paste doesn't submit, _landed races/fails, retry re-pastes -> DUPLICATE.
#   - Claude/Codex: Enter absorbed into paste buffer -> text strands unexecuted, yet the stranded
#     text makes the visibility check say "landed" -> false-positive, never retried.
#
# The invariant under test:
#   1. Two-phase submit: strip trailing CR/LF; send text LITERALLY (send-keys -l), no Enter; settle;
#      then a STANDALONE carriage return (send-keys C-m) fires onSubmit.
#   2. _landed verifies the COMPOSER cleared or a turn is active -- NOT that text is visible.
#      Stranded composer text reads as NOT landed.
#   3. Retry sends C-m ONLY, never a re-paste.
#
# Pane fixtures below are REAL captures (2026-09-07) of live Claude/Codex/Gemini panes on this VPS.

from services.arturo import pane_inject as pi


# ---- REAL captured composer shapes (tmux capture-pane -p, live panes) -----------------------

CLAUDE_ACTIVE = """\
* Determining... (running stop hooks... 9/11 . 9m 25s . down 36.0k tokens)

------------------------------------------------------------------------------
>
------------------------------------------------------------------------------
  up /gsd:update | Opus 4.8 (1M context) | agent-orchestra #####..... 57%
  bypass permissions on (shift+tab to cycle) . for agents
"""

CLAUDE_IDLE = """\
  Done (3 tool uses . 1m 4s)

------------------------------------------------------------------------------
>
------------------------------------------------------------------------------
  up /gsd:update | Opus 4.8 (1M context) | agent-orchestra #.........14%
  bypass permissions on (shift+tab to cycle) . for agents
"""

CODEX_IDLE = """\
+--------------------------------------------------------------------+
|                                                                    |
+--------------------------------------------------------------------+


> Ask Codex to do anything

  gpt-5.6-terra medium fast . ~/scripts/agent-orchestra/.claude/worktrees/codex-provider
"""

GEMINI_IDLE = """\
------------------------------------------------------------------------------
>
------------------------------------------------------------------------------
? for shortcuts                                                Gemini 3.7 Flash . low
"""

# The FAILURE shape (Bug 1 stranded case): the injected message sits in the composer, unexecuted.
# Modeled by placing the real message on the real prompt line of each real idle capture.
MSG = "please post the P6 gate summary to shaw and confirm the flag state"


def _strand(idle_capture, prompt_char):
    # replace the empty prompt line with one carrying the stranded message
    lines = idle_capture.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == prompt_char or s == f"{prompt_char} Ask Codex to do anything".strip():
            lines[i] = f"{prompt_char} {MSG}"
            break
    return "\n".join(lines) + "\n"


CLAUDE_STRANDED = _strand(CLAUDE_IDLE, ">")
CODEX_STRANDED = _strand(CODEX_IDLE, ">")
GEMINI_STRANDED = _strand(GEMINI_IDLE, ">")


# ---- 1. two-phase submit: paste is LITERAL and carries NO Enter -----------------------------

def test_paste_command_is_literal_and_has_no_enter():
    cmd = pi.paste_command("gm", "hello there", mac=False)
    assert "send-keys" in cmd
    assert " -l " in f" {cmd} "            # literal-key mode
    assert "hello there" in cmd
    # the whole point: the paste must NOT submit
    assert "Enter" not in cmd
    assert "C-m" not in cmd


def test_paste_strips_trailing_crlf_so_it_cannot_autosubmit():
    cmd = pi.paste_command("gm", "do the thing\r\n", mac=False)
    assert "do the thing" in cmd
    # trailing newline stripped -> the literal paste ends at the text, no embedded submit
    assert "\\r" not in cmd and "\r" not in cmd
    assert not cmd.rstrip("'\" ").endswith("\n")


def test_paste_escapes_single_quotes_for_shell():
    cmd = pi.paste_command("gm", "shaw's call", mac=False)
    # single quote in the body must be shell-escaped, not left to break the quoting
    assert "'\\''" in cmd


def test_mac_paste_carries_homebrew_path_prefix():
    assert pi.paste_command("gm", "x", mac=True).startswith("PATH=/opt/homebrew/bin:$PATH ")
    assert not pi.paste_command("gm", "x", mac=False).startswith("PATH=")


# ---- 2. submit is a STANDALONE carriage return that carries no text -------------------------

def test_submit_command_is_standalone_cr_with_no_text():
    cmd = pi.submit_command("gm", mac=False)
    assert cmd.rstrip().endswith("C-m")
    assert "-l" not in cmd
    assert "Enter" not in cmd            # C-m, not the Enter keyword that can be paste-absorbed


# ---- 3. _landed: turn active OR composer cleared == landed; STRANDED text == NOT landed ------

def test_active_turn_reads_as_landed():
    assert pi.is_landed(CLAUDE_ACTIVE, MSG) is True


def test_cleared_composer_reads_as_landed():
    # empty prompt, no spinner -> submitted (or idle) -> landed
    assert pi.is_landed(CLAUDE_IDLE, MSG) is True
    assert pi.is_landed(CODEX_IDLE, MSG) is True     # placeholder == empty
    assert pi.is_landed(GEMINI_IDLE, MSG) is True


def test_stranded_text_reads_as_NOT_landed_all_runtimes():
    # THE crux: the message sitting in the composer must NOT be mistaken for "landed"
    assert pi.is_landed(CLAUDE_STRANDED, MSG) is False
    assert pi.is_landed(CODEX_STRANDED, MSG) is False
    assert pi.is_landed(GEMINI_STRANDED, MSG) is False


def test_message_present_as_completed_turn_but_composer_clear_is_landed():
    # message echoed up in the transcript, composer clear below -> landed (old code false-negatived
    # nothing here, but the point is we key off the COMPOSER, not whole-pane visibility)
    pane = "> " + MSG + "\n\n  Done (1 tool use . 3s)\n\n---\n> \n---\n  model . 10%\n"
    assert pi.is_landed(pane, MSG) is True
