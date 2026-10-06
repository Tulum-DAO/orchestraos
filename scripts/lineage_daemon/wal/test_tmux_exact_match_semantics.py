"""Pin REAL tmux target semantics on a PRIVATE socket, never the fleet server.

A mock cannot reproduce these, and one of them contradicted my own written assumption:
I had claimed an absent target always yields a NON-ZERO return. It does not -- an
AMBIGUOUS prefix yields rc=0 with EMPTY stdout, and several call sites survive that only
via pre-existing `except ValueError` / `len(parts)==2` guards rather than by design.

`tmux -L <socket>` runs a separate server, so nothing here can touch a fleet seat.
"""
import os
import pathlib
import shutil
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


@pytest.fixture
def tm():
    sock = "zz-exact-" + uuid.uuid4().hex[:8]

    def run(*args):
        return subprocess.run(["tmux", "-L", sock, *args],
                              capture_output=True, text=True, timeout=10)
    try:
        yield run
    finally:
        # kill-server ends the server but LEAVES THE SOCKET FILE, so the first version of
        # this fixture littered 7 stale sockets in /tmp/tmux-<uid>/ per run (83 found after
        # a dozen runs -- all dead, so not a resource leak, but still litter I created).
        # Unlink it too. In `finally` so a failing assertion cannot skip the cleanup.
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
        for base in (os.environ.get("TMUX_TMPDIR"), "/tmp"):
            if not base:
                continue
            try:
                pathlib.Path(base, f"tmux-{os.getuid()}", sock).unlink(missing_ok=True)
            except OSError:
                pass


def _new(run, name):
    assert run("new-session", "-d", "-s", name).returncode == 0


def test_a_bare_target_resolves_an_ABSENT_root_to_its_g_suffixed_sibling(tm):
    """The whole defect, on real tmux. Only `root-g5` exists; `root` does not."""
    _new(tm, "root-g5")
    green = tm("display-message", "-p", "-t", "=root-g5:0.0", "#{pane_pid}").stdout.strip()
    bare = tm("display-message", "-p", "-t", "root:0.0", "#{pane_pid}").stdout.strip()
    assert bare == green != "", (
        "a bare target for the ABSENT bare root must resolve to the green -- if this ever "
        "stops being true, the bug this sweep fixes has changed shape")
    assert tm("has-session", "-t", "root").returncode == 0, "bare has-session prefix-hits"


def test_the_exact_form_fails_closed_on_the_same_shape(tm):
    _new(tm, "root-g5")
    assert tm("has-session", "-t", "=root").returncode != 0
    assert tm("display-message", "-p", "-t", "=root:0.0", "#{pane_pid}").stdout.strip() == ""


def test_control_the_exact_form_still_resolves_a_PRESENT_session(tm):
    """Without this, a sweep that broke every lookup would pass the tests above."""
    _new(tm, "root-g5")
    bare = tm("display-message", "-p", "-t", "root-g5:0.0", "#{pane_pid}").stdout.strip()
    exact = tm("display-message", "-p", "-t", "=root-g5:0.0", "#{pane_pid}").stdout.strip()
    assert exact == bare != "", "the '=' form must be behaviour-preserving when present"


def test_the_g1_vs_g10_hazard_is_real(tm):
    """`root-g1` prefix-matches `root-g10` -- reachable on a long-lived seat."""
    _new(tm, "root-g10")
    assert tm("has-session", "-t", "root-g1").returncode == 0, "g1 prefix-hits g10"
    assert tm("has-session", "-t", "=root-g1").returncode != 0


def test_an_AMBIGUOUS_prefix_returns_rc0_with_EMPTY_stdout(tm):
    """THE BRANCH I GOT WRONG IN WRITING. Two matches is not an error for
    display-message: rc is 0 and stdout is EMPTY. So `int(r.stdout.strip())` raises
    ValueError and only a pre-existing except saves the caller. An absent target does NOT
    always mean a non-zero return."""
    _new(tm, "root-g4")
    _new(tm, "root-g5")
    r = tm("display-message", "-p", "-t", "root:0.0", "#{pane_pid}")
    assert r.returncode == 0 and r.stdout.strip() == "", (
        f"expected the ambiguous branch (rc 0, empty stdout), got "
        f"rc={r.returncode} out={r.stdout!r}")
    # has-session, by contrast, DOES refuse an ambiguous prefix
    assert tm("has-session", "-t", "root").returncode != 0


def test_list_panes_needs_the_window_separator_for_the_equals_to_bind(tm):
    """`list-panes -t '=NAME'` is a WINDOW target, so the '=' does NOT bind to the session
    name and the prefix match SURVIVES. The trailing colon is load-bearing."""
    _new(tm, "root-g5")
    leaky = tm("list-panes", "-t", "=root", "-F", "#{session_name}")
    assert leaky.returncode == 0 and leaky.stdout.strip() == "root-g5", (
        "if this fails tmux changed; the trailing-colon requirement may no longer hold")
    fixed = tm("list-panes", "-t", "=root:", "-F", "#{session_name}")
    assert fixed.returncode != 0, "'=root:' must fail closed for an absent session"
    ok = tm("list-panes", "-t", "=root-g5:", "-F", "#{session_name}")
    assert ok.returncode == 0 and ok.stdout.strip() == "root-g5"


def test_an_exact_kill_cannot_reach_the_g_suffixed_sibling(tm):
    """The one that matters most: the bare form would have killed the successor."""
    _new(tm, "root-g5")
    assert tm("kill-session", "-t", "=root").returncode != 0
    assert tm("has-session", "-t", "=root-g5").returncode == 0, "the successor must survive"
    # and the bare form, for contrast, destroys it
    assert tm("kill-session", "-t", "root").returncode == 0
    assert tm("has-session", "-t", "=root-g5").returncode != 0, (
        "bare kill-session did NOT destroy the sibling -- the premise of this sweep")
