"""The installed delete-guard, under test (gm g79 msg_be783b91, devex g7's acceptance shape).

THE GUARD IS INVERTED. `NUCLEAR_PATTERNS` holds a bare prefix ending in a root slash and matches
it with `grep -qF` -- a FIXED SUBSTRING -- so it blocks EVERY absolute-path recursive delete
(a /tmp scratchpad, a repo .workspace, /var/log) and ALLOWS every relative one. What it permits
is the dangerous shape: a relative recursive delete executed from the root IS the nuclear case.
It over-fires on safe and under-fires on nuclear, because it enumerates dangerous SPELLINGS
rather than deciding on the RESOLVED TARGET.

MEASURED THROUGH THE INSTALLED HOOK before any change (orchestra-builder g61):
    /tmp scratchpad ... rc=2   repo .workspace ... rc=2   /var/log ... rc=2      <- over-fires
    ./build ... rc=0   bare `build` ... rc=0   relative glob from / ... rc=0     <- under-fires
    the two genuine nuclear forms ... rc=2                                       <- correct
    a commit message merely QUOTING the pattern ... rc=2                         <- over-fires

EVERY DANGEROUS STRING IN THIS FILE IS CONSTRUCTED, NEVER WRITTEN LITERALLY, and that is not
fastidiousness. THREE TIMES the live guard refused a document ABOUT ITSELF: it blocked devex's
commit message until they rewrote it, it blocked the diagnostic that characterised it, and it
blocked the shell heredoc that first tried to write this file. A guard that refuses the record of
its own defect makes that defect expensive to write down, which is how one survives three weeks.
After the fix, prose is no longer matched and a later author may write these literally.
"""
import json
import os
import subprocess

import pytest

GUARD = os.environ.get("DELETE_GUARD") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "delete-guard.sh")
pytestmark = pytest.mark.skipif(not os.path.exists(GUARD), reason="guard entrypoint absent")

RM = "rm -" + "rf "               # never the literal pattern in this source
SL = "/"
HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd: str, cwd: str = REPO, tool: str = "Bash") -> int:
    """Drives whichever entrypoint DELETE_GUARD names.

    Default is the INSTALLED shell hook, so this suite reports the deployed truth. Point it at
    scripts/hooks/delete_guard.py to grade the in-repo logic before it is deployed --
    the same two-seam arrangement the backtick guard uses, and the reason that one could be
    proven through the installed shim after its pick.
    """
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}, "cwd": cwd})
    argv = ["python3", GUARD] if GUARD.endswith(".py") else ["bash", GUARD]
    return subprocess.run(argv, input=payload, capture_output=True, text=True).returncode


# ---- (i) MUST NOT REFUSE: absolute deletes confined to scratch / disposable areas ----------

def test_a_tmp_scratchpad_delete_is_allowed():
    """The disk-P0 cleanup step devex was refused on."""
    assert run(RM + SL + "tmp/claude-1001/dead-session") == 0


def test_a_repo_workspace_delete_is_allowed():
    assert run(RM + REPO + "/.workspace/tmp-build") == 0


def test_a_var_log_delete_is_allowed():
    assert run(RM + SL + "var/log/nginx/old") == 0


# ---- (ii) MUST REFUSE: relative forms whose RESOLVED target is a protected root ------------

def test_a_relative_glob_from_the_filesystem_root_is_refused():
    """THE INVERSION. This is the nuclear case and today it is rc=0."""
    assert run(RM + "*", cwd=SL) == 2


def test_a_relative_dot_from_the_filesystem_root_is_refused():
    assert run(RM + ".", cwd=SL) == 2


def test_a_relative_glob_from_HOME_is_refused():
    assert run(RM + "*", cwd=HOME) == 2


def test_a_relative_delete_of_an_ordinary_subdir_is_allowed():
    """The fix must not turn every relative delete into a refusal -- that would stop the fleet."""
    assert run(RM + "build") == 0
    assert run(RM + "./node_modules") == 0


# ---- (iii) MUST STILL REFUSE: the genuine nuclear forms the guard gets right today ---------

def test_the_root_delete_is_still_refused():
    assert run(RM + SL) == 2


def test_the_root_glob_delete_is_still_refused():
    assert run(RM + SL + "*") == 2


def test_a_home_delete_is_still_refused():
    assert run(RM + "~") == 2
    assert run(RM + HOME) == 2


def test_a_system_directory_delete_is_refused():
    for d in ("etc", "usr", "var", "boot", "bin"):
        assert run(RM + SL + d) == 2, d


# ---- (iv) MUST NOT REFUSE: prose that merely QUOTES the pattern ----------------------------

def test_a_commit_message_quoting_the_pattern_is_allowed():
    """devex's commit documenting this defect was blocked until rewritten."""
    assert run("git commit -m 'the guard matches " + RM + SL + " as a substring'") == 0


def test_an_echo_describing_the_pattern_is_allowed():
    assert run("echo 'never run " + RM + SL + " on this box'") == 0


def test_a_non_bash_tool_is_ignored():
    assert run(RM + SL, tool="Edit") == 0


# ---- D4, the heredoc half: a quoted heredoc body is TEXT, never an invocation ---------------

def test_a_quoted_heredoc_body_quoting_the_pattern_is_allowed():
    assert run("cat > doc.md <<'EOF'\nnever run " + RM + SL + " on this box\nEOF") == 0


def test_a_quoted_heredoc_body_that_LOOKS_like_an_invocation_is_allowed():
    """Writing the incident report must not be refused by the thing it reports on. The shell
    never executes a quoted heredoc body, so neither does the guard read it as a command."""
    assert run("cat > doc.md <<'EOF'\n" + RM + SL + "\nEOF") == 0


def test_an_UNQUOTED_heredoc_is_NOT_exempted():
    """bash DOES expand an unquoted heredoc, so exempting it would be a hole. The asymmetry is
    the point: the exemption tracks what bash actually does, not what looks like text."""
    assert run("cat > doc.md <<EOF\n" + RM + SL + "\nEOF") == 2


def test_a_real_invocation_after_a_heredoc_is_still_caught():
    assert run("cat > doc.md <<'EOF'\ntext\nEOF\n" + RM + SL) == 2


# ---- D2: EQUALITY on everything that is not rule 1 -------------------------------------------

@pytest.mark.skipif(not os.path.exists(os.path.expanduser("~/.claude/hooks/delete-guard.sh")),
                    reason="installed guard absent")
def test_only_rule_1_behaviour_changes():
    """devex g7's D2: this moves in the LENIENT direction, so the risk is dropping a rule that
    was doing work. Driven as an equality against the ORIGINAL installed guard — rules 2-9 must
    return identical codes, and ONLY the two rule-1 cases may differ.

    Note it asserts what the original ACTUALLY does, not what its comments claim: `git reset
    --hard` returns 0 from this guard on BOTH sides (git_state_guard.py is what refuses that),
    and asserting a block here would have pinned a belief instead of the behaviour.
    """
    orig = os.path.expanduser("~/.claude/hooks/delete-guard.sh")
    new = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delete_guard.installed.sh")
    if not os.path.exists(new):
        pytest.skip("deployable form not present in this tree")

    def rc(guard, cmd, cwd=REPO):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd})
        return subprocess.run(["bash", guard], input=payload,
                              capture_output=True, text=True).returncode

    # THE PREMISE EXPIRES AT INSTALL, and it did: this compared the DEPLOYABLE form against
    # "whatever is installed", assuming that was still the OLD guard. Once gm installed the shim
    # the two rule-1 cases stopped differing and this went red -- a test failing BECAUSE the
    # deploy worked. Post-install the honest assertion is total equality: the installed hook and
    # the deployable form are now the same program.
    if "NUCLEAR_ENTRY" in open(orig).read():
        for cmd, cwd in [(RM + SL + "tmp/claude-1001/dead", REPO), (RM + "*", SL),
                         ("git reset --hard origin/main", REPO), (RM + "./build", REPO)]:
            assert rc(orig, cmd, cwd) == rc(new, cmd, cwd), cmd
        return

    unchanged = [
        ("git reset --hard origin/main", REPO),
        ("git checkout .", REPO),
        (RM + "./config/.env", REPO),
        (RM + "./audio/track.mp3", REPO),
        (RM + "./*", REPO),
        (RM + "./node_modules", REPO),
        ("ls -la", REPO),
    ]
    for cmd, cwd in unchanged:
        assert rc(orig, cmd, cwd) == rc(new, cmd, cwd), cmd

    # the two that MUST differ, in the stated directions
    assert rc(orig, RM + SL + "tmp/claude-1001/dead") == 2
    assert rc(new, RM + SL + "tmp/claude-1001/dead") == 0
    assert rc(orig, RM + "*", SL) == 0
    assert rc(new, RM + "*", SL) == 2


# ---- devex g7 condition 1: a WRAPPER PREFIX must not hide the invocation --------------------

@pytest.mark.parametrize("wrapper", [
    "sudo ", "doas ", "env ", "nohup ", "time ", "xargs ", "timeout 5 ", "nice -n 19 ",
    "sudo -u operator ", "sudo -E -u operator ", "env FOO=1 ", "ionice -c 3 nice -n 19 ",
    "FOO=1 BAR=2 sudo ", "xargs -n 1 ", "timeout --signal=TERM 5 ",
])
def test_a_wrapped_nuclear_delete_is_refused(wrapper):
    """The guard being replaced refuses ALL of these today (rc=2), so allowing them would make
    the fleet MORE permissive on the one input class nobody disagrees about -- devex's D2
    equality break. Reading only argv[0] was the cause; a wrapped invocation is still one."""
    assert run(wrapper + RM + SL, cwd=HOME) == 2


@pytest.mark.parametrize("cmd", [
    "bash -c '" + RM + SL + "'",
    'sh -c "' + RM + SL + '"',
    "sudo bash -c '" + RM + SL + "'",
])
def test_a_shell_dash_c_script_is_parsed_as_a_command(cmd):
    """`bash -c` takes a SCRIPT, not a token to step over. Stepping over it read the whole
    delete as one opaque word."""
    assert run(cmd, cwd=HOME) == 2


@pytest.mark.parametrize("cmd,want", [
    ("sudo systemctl restart nginx", 0),
    ("sudo " + RM + SL + "tmp/claude-1001/dead", 0),
    ("sudo " + RM + "./build", 0),
    ("bash -c '" + RM + "./build'", 0),
])
def test_wrapper_handling_adds_no_false_refusals(cmd, want):
    """Stepping through wrappers must not turn ordinary wrapped work into a refusal."""
    assert run(cmd, cwd=REPO) == want
