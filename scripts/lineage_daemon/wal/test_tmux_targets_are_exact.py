"""Every tmux session target in the reap/kill paths must be EXACT-match ('=NAME').

`tmux -t NAME` resolves a session target by PREFIX. The normal mid-swap state of a seat is
"bare root ABSENT, `<root>-g<N>` present", so a bare `-t root` resolves to the GREEN; and
`root-g1` prefix-matches `root-g10..g19` on a long-lived seat. Measured on live tmux: with
only `zzpidprobe-g9` alive, a bare query for the absent `zzpidprobe` returned the green's pid
while the '=' form returned nothing. `=NAME:0.0` is valid tmux syntax and still resolves
window 0 pane 0 of the exact session (measured: `=zzsuffix:0.0` -> 2331484 and
`=zzsuffix-g3:0.0` -> 2331495, distinct).

These tests assert the ARGV, because the defect IS the argument. A live-tmux test would be
flaky in CI and would not pin the intent.
"""
import subprocess

from lineage_daemon import executors
from lineage_daemon.wal import green_liveness, spawn_green


class _Spy:
    """Records every tmux argv; returns a canned result."""

    def __init__(self, stdout="4242\n", returncode=0):
        self.calls = []
        self._stdout, self._rc = stdout, returncode

    def __call__(self, argv, *a, **kw):
        self.calls.append(list(argv))
        spy = self

        class R:
            stdout = spy._stdout
            stderr = ""
            returncode = spy._rc
        return R()

    def targets(self):
        return [c[c.index("-t") + 1] for c in self.calls if "-t" in c]


def _assert_all_exact(targets, where):
    assert targets, f"{where}: no tmux target was captured — the test proves nothing"
    bare = [t for t in targets if not t.startswith("=")]
    assert not bare, (
        f"{where}: bare PREFIX-matching tmux target(s) {bare}. For a seat whose bare root is "
        f"absent these resolve to <root>-g<N>, i.e. the GREEN.")


# --- spawn_green: the three that feed green_pane_pid / pane_id -----------------------
def test_spawn_green_has_session_target_is_exact(monkeypatch):
    spy = _Spy(returncode=0)
    monkeypatch.setattr(spawn_green.subprocess, "run", spy)
    spawn_green._tmux_has_session("seat")
    _assert_all_exact(spy.targets(), "_tmux_has_session")


def test_spawn_green_pane_pid_target_is_exact(monkeypatch):
    """This value is recorded as green_pane_pid, which is the COMPARAND of
    reaper._reap_recorded's never-reap-green guard. A prefix-resolved comparand does not
    merely degrade a reading — it defeats the only check standing between a reap and a
    live green."""
    spy = _Spy(stdout="4242\n")
    monkeypatch.setattr(spawn_green.subprocess, "run", spy)
    spawn_green._tmux_pane_pid("seat")
    _assert_all_exact(spy.targets(), "_tmux_pane_pid")


def test_spawn_green_pane_id_target_is_exact(monkeypatch):
    """A prefix-resolved pane id unlinks the WRONG panes/<N>.json — the %N reuse hazard
    already documented at _unlink_stale_pane_event (AGY DEC-1788655588)."""
    spy = _Spy(stdout="%7\n")
    monkeypatch.setattr(spawn_green.subprocess, "run", spy)
    spawn_green._tmux_pane_id("seat")
    _assert_all_exact(spy.targets(), "_tmux_pane_id")


# --- CONTROLS: the sweep must not be bought by always answering None/False -----------
def test_control_present_pane_still_yields_its_pid(monkeypatch):
    monkeypatch.setattr(spawn_green.subprocess, "run", _Spy(stdout="4242\n"))
    assert spawn_green._tmux_pane_pid("seat") == 4242


def test_control_present_pane_still_yields_its_id(monkeypatch):
    monkeypatch.setattr(spawn_green.subprocess, "run", _Spy(stdout="%7\n"))
    assert spawn_green._tmux_pane_id("seat") == "%7"


def test_control_present_session_still_reads_True(monkeypatch):
    monkeypatch.setattr(spawn_green.subprocess, "run", _Spy(returncode=0))
    assert spawn_green._tmux_has_session("seat") is True


def test_an_absent_session_reads_False_not_a_neighbour(monkeypatch):
    monkeypatch.setattr(spawn_green.subprocess, "run", _Spy(returncode=1))
    assert spawn_green._tmux_has_session("seat") is False


def test_an_absent_pane_yields_None(monkeypatch):
    """tmux answers rc=0 with EMPTY stdout for an absent '=' target, so int() raises and the
    existing except returns None. None is correct for 'no such pane'; a confident wrong pid
    is what deadlocks a swap."""
    monkeypatch.setattr(spawn_green.subprocess, "run", _Spy(stdout="\n", returncode=0))
    assert spawn_green._tmux_pane_pid("seat") is None


# --- executors.execute_tmux_repin: the SCENARIO, not just the argv ------------------
def test_repin_does_NOT_kill_the_successor_it_is_repinning(monkeypatch):
    """THE FAILURE THIS PINS: the repin destroys the successor it is repinning.

    Bare root `canonical` ABSENT, exactly one successor at `canonical-g5`:
      1. `has-session -t canonical` returns 0 BY PREFIX MATCH on canonical-g5
      2. so the guard fires and `kill-session -t canonical` KILLS canonical-g5 -- the very
         successor this function exists to repin
    With exact targets step 1 does not fire, so nothing is killed.

    CORRECTED SCOPE (reviewer, against real tmux): the outcome is NOT silent success. An
    AMBIGUOUS prefix makes `has-session` return NON-ZERO, so the destructive kill requires
    EXACTLY ONE `canonical-*` to exist; once it is killed nothing survives the final verify,
    so `verified` is False and the caller holds with `repin-failed`. An earlier version of
    this test also asserted "must not report verified: True" -- that is False both before
    and after the fix, so it was a VACUOUS gate and has been removed. The kill is the defect;
    the reporting is not.

    NOTE ON REACHABILITY, so this is not oversold: `execute_tmux_repin` is UNWIRED --
    `complete.py` defaults `execute_tmux_repin_fn=None` and `build_completion_provider` never
    passes it, so only tests reach it. The REACHABLE instance of this shape is the retire
    consolidation in `complete.py`, covered separately.
    """
    live = {"canonical-g5"}          # the bare root does NOT exist
    killed = []

    def fake_run(argv, *a, **kw):
        rc = 0
        if "has-session" in argv:
            target = argv[argv.index("-t") + 1]
            if target.startswith("="):
                rc = 0 if target[1:] in live else 1          # exact
            else:
                # Real tmux: an exact hit wins; a SINGLE prefix hit resolves; AMBIGUOUS
                # (2+) prefix hits are an ERROR. Measured on a private socket in
                # test_tmux_exact_match_semantics.py.
                if target in live:
                    rc = 0
                else:
                    hits = [s for s in live if s.startswith(target)]
                    rc = 0 if len(hits) == 1 else 1
        elif "kill-session" in argv:
            target = argv[argv.index("-t") + 1]
            name = target[1:] if target.startswith("=") else target
            victims = ([name] if name in live
                       else [s for s in live if s.startswith(name)])
            for v in victims:
                live.discard(v)
                killed.append(v)
        elif "rename-session" in argv:
            pass

        class R:
            stdout = ""
            stderr = ""
            returncode = rc
        return R()

    monkeypatch.setattr(executors.subprocess, "run", fake_run)
    try:
        executors.execute_tmux_repin("canonical", "canonical-g5", "sid-x", armed=True)
    except Exception:
        pass        # a downstream status/receipt step may fail; the kill is what is on trial

    assert "canonical-g5" not in killed, (
        "the repin killed the successor it was repinning — a bare kill-session target "
        "prefix-matched canonical-g5 when the bare root did not exist")
    assert "canonical-g5" in live, "the successor must survive the repin"


def test_repin_targets_are_all_exact(monkeypatch):
    spy = _Spy(returncode=1)         # nothing exists -> no kill, no rename
    monkeypatch.setattr(executors.subprocess, "run", spy)
    try:
        executors.execute_tmux_repin("canonical", "canonical-g5", "sid-x", armed=True)
    except Exception:
        pass
    _assert_all_exact(spy.targets(), "execute_tmux_repin")


def test_control_repin_still_refuses_when_not_armed(monkeypatch):
    """The arm gate is the thing that keeps this canary-only. It must survive the sweep."""
    spy = _Spy()
    monkeypatch.setattr(executors.subprocess, "run", spy)
    assert executors.execute_tmux_repin("c", "a", "sid", armed=False) == {"executed": False}
    assert spy.calls == [], "an unarmed repin must touch tmux zero times"


# --- green_liveness: the GreenDead detector -----------------------------------------
def test_green_liveness_pane_targets_are_exact(monkeypatch):
    import inspect as _inspect
    src = _inspect.getsource(green_liveness)
    bad = [ln.strip() for ln in src.splitlines()
           if '"-t"' in ln and '"=' not in ln and 'f"={' not in ln]
    assert not bad, f"bare tmux target(s) in green_liveness: {bad}"


# --- the '=' prefix does NOT bind the same way for every tmux subcommand ------------
# MEASURED on live tmux, with only `zzlp2-g2` alive and `zzlp2` ABSENT:
#   display-message -t '=zzlp2:0.0'          -> EMPTY            (exact: fails closed)
#   has-session     -t '=zzlp2'              -> rc 1             (exact)
#   kill-session    -t '=zzk'                -> rc 1, zzk-g2 SURVIVES (exact)
#   rename-session  -t '=zzk'                -> rc 1, name kept  (exact)
#   list-panes      -t '=zzlp2'              -> rc 0, sess=zzlp2-g2  <-- NOT EXACT
#   list-panes      -t '=zzlp2:'             -> rc 1 "can't find session"  (exact)
# For `list-panes` the target is a WINDOW target, so without an explicit window separator
# the '=' does not bind to the session name and the prefix match survives. The trailing
# colon is REQUIRED and is not cosmetic -- dropping it silently restores the bug.

def test_list_panes_targets_keep_the_trailing_colon(monkeypatch):
    """A `list-panes -t '=NAME'` WITHOUT the window separator still prefix-matches."""
    import inspect as _inspect
    from lineage_daemon.wal import ctx_adapters

    for mod in (ctx_adapters,):
        for line in _inspect.getsource(mod).splitlines():
            if "list-panes" not in line:
                continue
            assert 'f"={' in line, f"bare list-panes target: {line.strip()}"
            assert ':"' in line or ':0.0"' in line or ':\\"' in line, (
                f"list-panes '=' target without a window separator — the '=' does not bind "
                f"and the prefix match survives: {line.strip()}")


# --- the residuals #180 left standing, closed here ---------------------------------
# #180 made every target exact but left TWO things open, both reported by its reviewer:
#   (a) `execute_tmux_repin` kept the UNCONDITIONAL kill-then-rename shape, so in the
#       already-repinned state an EXACT kill destroys the promoted successor. Exactness
#       alone is not the fix; conditionality is.
#   (b) the `send-keys` and `tmux_consolidate` fixes had NO PIN, so nothing stopped them
#       regressing.

def _tmux_sim(live, killed=None, exact_only=True):
    """A faithful tmux fake: ONE name per session, exact-vs-prefix honoured, and an absent
    exact target answers rc 1 for has-session / EMPTY for display-message -- the shapes
    measured on a private socket in test_tmux_exact_match_semantics.py."""
    killed = killed if killed is not None else []

    def run(argv, *a, **kw):
        target = argv[argv.index("-t") + 1] if "-t" in argv else ""
        exact = target.startswith("=")
        name = (target[1:] if exact else target).split(":")[0]
        out, rc = "", 0
        if not exact and not exact_only:
            hits = [s for s in live if s == name] or [s for s in live if s.startswith(name)]
            name = hits[0] if len(hits) == 1 else name
        if "has-session" in argv:
            rc = 0 if name in live else 1
        elif "display-message" in argv:
            out = live.get(name, "")
        elif "kill-session" in argv:
            if live.pop(name, None) is not None:
                killed.append(name)
            else:
                rc = 1
        elif "rename-session" in argv:
            if name in live:
                live[argv[-1]] = live.pop(name)
            else:
                rc = 1

        class R:
            returncode = rc
            stdout = out
            stderr = ""
        return R()
    return run, killed


def test_repin_does_NOT_kill_an_ALREADY_REPINNED_successor(monkeypatch):
    """(a) The canonical name is already held by the successor and the alias is gone. An
    EXACT but UNCONDITIONAL kill destroys the thing being installed."""
    live = {"canonical": "4242"}            # alias already renamed onto the canonical name
    run, killed = _tmux_sim(live)
    monkeypatch.setattr(executors.subprocess, "run", run)
    try:
        executors.execute_tmux_repin("canonical", "canonical-g5", "sid-x", armed=True)
    except Exception:
        pass
    assert killed == [], f"killed the already-repinned successor: {killed}"
    assert live == {"canonical": "4242"}, f"the seat must be left intact: {live}"


def test_control_repin_STILL_kills_a_stranger_holding_the_canonical_name(monkeypatch):
    """CONTROL: the kill must still happen when the name is held by something that is NOT
    the alias, or the rename target stays occupied. Without this, a change that simply
    disabled the kill would pass the test above."""
    live = {"canonical": "999", "canonical-g5": "4242"}
    run, killed = _tmux_sim(live)
    monkeypatch.setattr(executors.subprocess, "run", run)
    try:
        executors.execute_tmux_repin("canonical", "canonical-g5", "sid-x", armed=True)
    except Exception:
        pass
    assert killed == ["canonical"], f"the stranger must be killed: {killed}"
    assert live.get("canonical") == "4242", f"the successor should hold the name now: {live}"


def test_the_auto_resume_send_keys_target_is_exact():
    """(b) A WRITE. A bare target mid-swap types the resume into the GREEN's pane, i.e. into
    a session nobody chose. The target is bound to a VARIABLE, so a line-wise grep for
    `"-t"` misses it -- this asserts the binding itself."""
    import inspect as _inspect

    from lineage_daemon import complete as _complete
    src = _inspect.getsource(_complete)
    assert 'pane_target = f"={canary}:0"' in src, (
        "the auto-resume send-keys target is no longer exact-bound; a bare "
        '`f"{canary}:0"` types the resume into a prefix-matched pane')


def test_tmux_consolidate_targets_are_exact():
    """(b) The REAL DEFAULT consolidation seam. Its rename is the destructive form: with
    `old` gone, a bare `-t old` renames a `<old>-g<N>` SIBLING onto `new`."""
    import inspect as _inspect

    from lineage_daemon import tmux_consolidate as _tc
    src = _inspect.getsource(_tc)
    for frag in ('"has-session", "-t", f"={name}"', '"rename-session", "-t", f"={old}"'):
        assert frag in src, f"tmux_consolidate lost its exact target: expected {frag}"
