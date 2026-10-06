"""The recorded blue pid must be read with an EXACT tmux target, not a prefix match.

`tmux -t foo:0.0` resolves a session by PREFIX. A blue-green swap's normal mid-flight state
is "bare root absent, `<root>-g<N>` present", so a BARE `-t <root>:0.0` resolves to the GREEN
and `_default_pane_pid_fn` records the GREEN'S pid as `blue_pane_pid`.

Why that single character is load-bearing. The recorded pid is the authority for two separate
decisions, and a green pid poisons both in the same direction:
  * `reaper._reap_recorded` -- its never-reap-green guard correctly refuses, so blue is NEVER
    KILLED.
  * `bg_beat._complete_unobservable` -- its "recorded blue pid is PROVABLY DEAD" test consults
    a pid belonging to the live green, so it reads ALIVE and refuses, every beat, forever.
Together those are the deadlock: reap is gated on completion, completion on observability,
observability on the rename inside completion, and the unobservable-fallback on a blue that
only the blocked reap would kill. The seat then only unsticks if blue happens to exit on its
own, which is why it presents as an intermittent ~28-minute stall rather than a hang.

MEASURED on live tmux before this fix: with only `zzpidprobe-g9` alive, the bare query for the
absent `zzpidprobe` returned the green's pid 1869870; the `=` form returned nothing. The `=`
form also returns the IDENTICAL pid for a session that IS present (1875101 both ways), so this
is not a behaviour change for the healthy case -- which `test_control_*` below pins.
"""
from lineage_daemon.wal import reaper


class _Run:
    """Capture argv and return a canned tmux result."""
    def __init__(self, stdout="4242\n", returncode=0):
        self.argv = None
        self._stdout, self._rc = stdout, returncode

    def __call__(self, argv, **kw):
        self.argv = argv

        class R:
            stdout, returncode = self._stdout, self._rc
        return R()


def _target(argv):
    return argv[argv.index("-t") + 1]


def test_the_pane_pid_target_is_EXACT_match(monkeypatch):
    run = _Run()
    monkeypatch.setattr(reaper.subprocess, "run", run)
    reaper._default_pane_pid_fn("orchestraos-builder")
    target = _target(run.argv)
    assert target.startswith("="), (
        f"target {target!r} is a PREFIX match: for a seat whose bare root is absent this "
        f"resolves to <root>-g<N> and records the GREEN's pid as blue_pane_pid")
    assert target == "=orchestraos-builder:0.0", target


def test_control_a_present_pane_still_yields_its_pid(monkeypatch):
    """The guard must not have been bought by breaking the healthy path. Without this, a
    function that always returned None would pass the test above."""
    run = _Run(stdout="4242\n")
    monkeypatch.setattr(reaper.subprocess, "run", run)
    assert reaper._default_pane_pid_fn("seat") == 4242


def test_an_absent_pane_fails_CLOSED_rather_than_guessing(monkeypatch):
    """tmux answers rc=0 with EMPTY stdout for an absent '=' target, so the int() raises and
    we return None. None is the correct answer for 'no such pane'; a confident wrong pid is
    the thing that deadlocks a swap."""
    monkeypatch.setattr(reaper.subprocess, "run", _Run(stdout="\n", returncode=0))
    assert reaper._default_pane_pid_fn("seat") is None


def test_a_nonzero_tmux_exit_is_None(monkeypatch):
    monkeypatch.setattr(reaper.subprocess, "run", _Run(stdout="", returncode=1))
    assert reaper._default_pane_pid_fn("seat") is None
