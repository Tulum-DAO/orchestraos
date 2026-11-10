"""The installed PreToolUse backtick guard, under test (gm msg_92ad1acb, orchestra-builder g58).

The guard is fleet state (~/.claude/hooks/body-backtick-guard.py, wired in ~/.claude/settings.json)
and had no test. It gates EVERY Bash call on the fleet, so both failure directions are expensive:
a false ALLOW executes shell code the sender never sees, a false BLOCK stops the fleet sending mail.

The 09-20 rule gated on `--(body|text)\\s+["']`, so a backtick in --subject went straight through,
executed, and silently shortened a DELIVERED mail (2026-09-22, orchestra-builder g57). The rule is
now INVERTED: every argument of a send invocation is scanned and the SAFE carriers are what is
allowed out. GUARD_PATH is skipped rather than failed when the hook is absent (it is fleet state,
not a repo artifact), so this suite stays green on an export.
"""
import json
import os
import subprocess

import pytest

# The LOGIC is in-repo and is what this suite grades by default (gm CONDITION-1,
# DEC-1787640211 / msg_46d4f05a: hook logic lives in the repo, versioned and auditable;
# ~/.claude/hooks holds a thin shim that execs it). BACKTICK_GUARD points the same suite at
# the INSTALLED shim, which is how the deploy is proven end to end after a pick.
GUARD = os.environ.get("BACKTICK_GUARD") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "body_backtick_guard.py")
pytestmark = pytest.mark.skipif(not os.path.exists(GUARD), reason="guard entrypoint absent")

BT = chr(96)          # a real backtick, kept out of the source lines below on purpose
SUB = "$(" + "id)"


def run(cmd: str, tool: str = "Bash") -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}})
    return subprocess.run(["python3", GUARD], input=payload, capture_output=True,
                          text=True).returncode


SEND = "python3 msg_store.py send --to gm"


# ---- must BLOCK: the shell rewrites the argument before msg_store sees it -----------------

def test_a_backtick_in_an_inline_subject_is_blocked():
    """g57's real case, 2026-09-22: it executed and ate a word from a delivered mail."""
    assert run(f'{SEND} --subject "note {BT}date{BT}" --body-file f.txt') == 2


def test_a_backtick_in_an_inline_body_is_blocked():
    """The 09-20 case the first version caught; it must still be caught."""
    assert run(f'{SEND} --body "ran {BT}git worktree remove x{BT}"') == 2


def test_an_unquoted_backtick_is_blocked():
    assert run(f"{SEND} --subject {BT}whoami{BT}") == 2


def test_a_command_substitution_is_blocked():
    assert run(f'{SEND} --subject "hi {SUB}"') == 2


def test_a_future_flag_nobody_allow_listed_is_blocked():
    """The whole point of inverting: no flag is exempt, including ones not invented yet."""
    assert run(f'{SEND} --headline "x {BT}id{BT}"') == 2


def test_an_apostrophe_inside_a_double_quoted_argument_does_not_hide_a_backtick():
    """Naive single-quote stripping would swallow the rest of the line and ALLOW this."""
    assert run(f'{SEND} --subject "it\'s {BT}id{BT}"') == 2


# ---- must ALLOW: safe carriers, and anything that is not a send invocation ----------------

def test_a_single_quoted_argument_is_allowed():
    """bash expands nothing inside single quotes; blocking it would teach distrust of the guard."""
    assert run(f"{SEND} --subject 'literal {BT}not executed{BT}' --body-file f.txt") == 0


def test_a_quoted_heredoc_body_is_allowed():
    assert run(f"cat > f.txt <<'EOF'\nprose with {BT}backticks{BT} in it\nEOF\n{SEND} --body-file f.txt") == 0


def test_a_backslash_escaped_backtick_is_allowed():
    assert run(f'{SEND} --subject "literal \\{BT}"') == 0


def test_a_plain_send_is_allowed():
    assert run(f'{SEND} --subject "census false-DEAD BUILT defd3864d4" --body-file f.txt') == 0


def test_a_backtick_in_an_unrelated_command_is_not_our_business():
    assert run(f"echo {BT}date{BT}") == 0


def test_a_backtick_after_the_send_command_ends_is_not_attributed_to_it():
    assert run(f"{SEND} --body-file f.txt ; echo {BT}date{BT}") == 0


def test_the_phrase_inside_a_heredoc_is_not_an_invocation():
    assert run(f"cat > doc.md <<'EOF'\nrun msg_store.py send --body \"{BT}x{BT}\" (do not)\nEOF") == 0


def test_a_non_bash_tool_is_ignored():
    assert run(f'{SEND} --subject "{BT}id{BT}"', tool="Edit") == 0


# ---- the SHIM seam: the deploy is a forwarder, and forwarding is the thing that can break ----

SHIM = os.path.expanduser("~/.claude/hooks/body-backtick-guard.py")


def _is_shim() -> bool:
    return os.path.exists(SHIM) and "BBG_ENTRY" in open(SHIM).read()


@pytest.mark.skipif(not _is_shim(), reason="shim not deployed yet")
def test_the_installed_shim_forwards_a_block():
    """stdin, and the exit code that does the blocking, must survive the exec."""
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": f'{SEND} --subject "x {BT}id{BT}"'}})
    assert subprocess.run(["python3", SHIM], input=payload, capture_output=True,
                          text=True).returncode == 2


@pytest.mark.skipif(not _is_shim(), reason="shim not deployed yet")
def test_the_installed_shim_forwards_an_allow():
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": f"{SEND} --body-file f.txt"}})
    assert subprocess.run(["python3", SHIM], input=payload, capture_output=True,
                          text=True).returncode == 0


def test_a_missing_entrypoint_fails_OPEN_and_says_so(tmp_path):
    """A guard that cannot run must not stop the fleet sending mail — but it must never be
    silent about it, which is the defect class this guard exists to catch."""
    if not _is_shim():
        pytest.skip("shim not deployed yet")
    env = dict(os.environ, BBG_ENTRY=str(tmp_path / "gone.py"))
    r = subprocess.run(["python3", SHIM], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": f'{SEND} --subject "x {BT}id{BT}"'}}),
        capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "NOT protecting" in r.stderr


# ---- the two boundaries devex was asked to attack and could NOT drive (msg_dc269b33) ---------
# Their probe produced no output and exited 0 three ways, because main() ran at import. The
# module is importable now, so these drive the scan DIRECTLY rather than through a subprocess —
# a boundary bug hides easily behind an exit code.

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "bbg", os.path.join(os.path.dirname(os.path.abspath(__file__)), "body_backtick_guard.py"))
_bbg = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_bbg)


def _scan(cmd: str):
    """Drive the scan over the whole string, as if the send invocation started at position 0."""
    return _bbg.danger_in_invocation(cmd, 0)


class TestQuoteStateBoundary:
    def test_apostrophe_inside_double_quotes_does_not_open_a_safe_span(self):
        assert _scan(f'--subject "it\'s {BT}id{BT}"') == "a backtick"

    def test_a_genuine_single_quoted_span_is_safe(self):
        assert _scan(f"--subject 'it is {BT}id{BT} literal'") is None

    def test_double_quotes_inside_single_quotes_do_not_confuse_it(self):
        assert _scan(f"""--subject 'he said "hi" {BT}id{BT}'""") is None

    def test_escaped_backtick_in_double_quotes_is_literal(self):
        assert _scan(f'--subject "literal \\{BT} here"') is None

    def test_escaped_dollar_paren_is_literal(self):
        assert _scan('--subject "literal \\$(id) here"') is None

    def test_substitution_is_named_distinctly_from_a_backtick(self):
        assert _scan('--subject "x $(id)"') == "a $( ) command substitution"

    def test_unquoted_substitution_is_caught(self):
        assert _scan("--subject $(id)") == "a $( ) command substitution"


class TestInvocationScopeBoundary:
    """gm ruling msg_97e7ae1e: the guard owns the SEND's own arguments and stops there."""

    def test_the_scan_stops_at_a_semicolon(self):
        assert _scan(f"--body-file f.txt ; echo {BT}date{BT}") is None

    def test_the_scan_stops_at_a_double_ampersand(self):
        assert _scan(f"--body-file f.txt && echo {BT}date{BT}") is None

    def test_the_scan_stops_at_a_pipe(self):
        assert _scan(f"--body-file f.txt | tee {BT}f{BT}") is None

    def test_the_scan_stops_at_a_newline(self):
        assert _scan(f"--body-file f.txt\necho {BT}date{BT}") is None

    def test_danger_BEFORE_a_separator_is_still_caught(self):
        assert _scan(f'--subject "x {BT}id{BT}" ; echo done') == "a backtick"

    def test_a_separator_inside_quotes_does_not_end_the_invocation(self):
        """'; ' inside a quoted subject is text, not a command boundary."""
        assert _scan(f'--subject "a; b {BT}id{BT}"') == "a backtick"


# ---- EVERY send, not just the first (orchestra-builder g61; hit by g60, characterised by -------
# ---- devex g6 msg_3c5ecfb9, routed by gm msg_29fa8096) -----------------------------------------

class TestEverySendNotJustTheFirst:
    """FIRST-SEND-ONLY was the hole: main() called SEND_RE.search, so only the first send in a
    command was ever scanned and a backtick in the SECOND executed and was eaten silently.

    NOT "compound defeats the guard" — devex's correction to the way g60 first filed it. Any
    number of NON-send commands before or after a send are handled correctly and always were.
    The defect is per-SEND, and three separators reach it: `;`, `&&`, and a NEWLINE.

    THE NEWLINE IS THE ONE THAT MATTERS. Every multi-line Bash block on this fleet is written
    that way, so the hole is reachable without the author ever thinking of the command as
    compound — which is how g60 hit it while writing about the rule that forbids it.

    The per-invocation scan stays NARROW (gm ruling msg_97e7ae1e): each send is scanned only
    across its OWN arguments. Widening the SCAN is what that ruling forbids; scanning EVERY
    send is what it always assumed. test_a_backtick_after_the_send_command_ends_is_not_
    attributed_to_it above is the boundary and must stay green.
    """

    def test_a_backtick_in_a_second_send_after_a_semicolon_is_blocked(self):
        assert run(f'{SEND} --body-file a.txt ; {SEND} --subject "cc {BT}clue{BT}" '
                   f'--body-file b.txt') == 2

    def test_a_backtick_in_a_second_send_after_double_ampersand_is_blocked(self):
        assert run(f'{SEND} --body-file a.txt && {SEND} --subject "cc {BT}clue{BT}" '
                   f'--body-file b.txt') == 2

    def test_a_backtick_in_a_second_send_after_a_NEWLINE_is_blocked(self):
        """g60's real command shape, 2026-09-23: the send reported {"sent": true} and the
        delivered subject was missing the word bash had executed."""
        assert run(f'{SEND} --subject-file s.txt --body-file b.txt\n'
                   f'{SEND} --subject "cc: LANDED and {BT}clue{BT} is the strongest line" '
                   f'--body-file b2.txt') == 2

    def test_a_command_substitution_in_a_second_send_is_blocked(self):
        assert run(f'{SEND} --body-file a.txt\n{SEND} --subject "{SUB}" --body-file b.txt') == 2

    def test_a_THIRD_send_is_reached_too(self):
        """finditer, not 'check two' — the count is not the fix, the iteration is."""
        assert run(f'{SEND} --body-file a.txt\n{SEND} --body-file b.txt\n'
                   f'{SEND} --subject "x {BT}id{BT}" --body-file c.txt') == 2

    def test_a_dangerous_FIRST_send_still_blocks_when_a_clean_send_follows(self):
        assert run(f'{SEND} --subject "x {BT}id{BT}" --body-file a.txt\n'
                   f'{SEND} --body-file b.txt') == 2

    # ---- and the ruled narrowness, restated at the new seam: still NOT our business ----

    def test_a_backtick_between_two_clean_sends_is_still_not_attributed(self):
        """A non-send command carrying a backtick, sandwiched between two clean sends, is the
        hazard gm ruled belongs to someone else. Scanning every SEND must not turn this guard
        into a general shell linter."""
        assert run(f'{SEND} --body-file a.txt\necho {BT}date{BT}\n{SEND} --body-file b.txt') == 0

    def test_two_clean_sends_are_allowed(self):
        assert run(f'{SEND} --body-file a.txt\n{SEND} --body-file b.txt') == 0

    def test_a_second_send_with_a_single_quoted_argument_is_allowed(self):
        assert run(f"{SEND} --body-file a.txt\n{SEND} --subject 'cc {BT}clue{BT}' "
                   f"--body-file b.txt") == 0
