"""boot_inject: a boot prompt counts as typed only when it SUBMITTED (DEC-1791347425871474).

Hermetic: a fake pane models the composer, the scrollback, async ingestion (Enters swallowed
until the paste is ingested), and chips. No real tmux.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boot_inject as B  # noqa: E402

TEXT = "You are pm-x. Read /tmp/agent-init-pm-x.md and follow all instructions in it."
LONG = "\n".join(f"line {i} of a long handoff" for i in range(16))


class Pane:
    """swallow: how many Enters are eaten before the paste is ingested. chip: render the paste as
    a chip. never_lands: pastes vanish. preexisting: composer content before we start."""

    def __init__(self, swallow=0, chip=False, never_lands=False, preexisting="", in_mode=False):
        self.scroll = ["╭ Claude Code", "  some earlier output"]
        self.composer = preexisting
        self.swallow, self.chip, self.never_lands, self.in_mode = swallow, chip, never_lands, in_mode
        self.buffer = ""
        self.pastes = 0
        self.enters = 0
        self.calls = []

    def render(self):
        return "\n".join(self.scroll + [f"❯ {self.composer}", "──────", "  status bar"])

    def __call__(self, *args, timeout=10):
        self.calls.append(args)
        cmd = args[0]
        if cmd == "capture-pane":
            return 0, self.render()
        if cmd == "display-message":
            return 0, ("1" if self.in_mode else "0") if "#{pane_in_mode}" in args else "0"
        if cmd == "set-buffer":
            self.buffer = args[-1]
            return 0, ""
        if cmd == "paste-buffer":
            self.pastes += 1
            if not self.never_lands:
                self.composer += ("[Pasted text #1 +16 lines]" if self.chip else self.buffer)
            return 0, ""
        if cmd == "send-keys":
            if args[-1] == "q":
                self.in_mode = False
            elif args[-1] == "Enter":
                self.enters += 1
                if self.composer and self.enters > self.swallow:
                    self.scroll.append(f"> {self.composer}")
                    self.composer = ""
            return 0, ""
        raise AssertionError(args)


def run(pane, text=TEXT, **kw):
    kw.setdefault("canonical_live_fn", lambda s, tmux_fn=None: False)
    return B.boot_inject("pm-x", text, tmux_fn=pane, sleep_fn=lambda s: None,
                         log_fn=lambda m: None, **kw)


def test_submits_on_the_first_enter():
    p = Pane()
    assert run(p) == ("submitted", "enter-1")
    assert p.pastes == 1


def test_swallowed_enters_are_retried_until_it_submits():
    p = Pane(swallow=2)
    assert run(p) == ("submitted", "enter-3")


def test_SLOW_ingestion_is_rescued_by_a_late_bare_enter_without_repasting():
    """The 10-07 case: the strand outlived 3 quick Enters; a bare Enter minutes later worked."""
    p = Pane(swallow=5, chip=True)
    out, stage = run(p, LONG)
    assert out == "submitted" and stage.startswith("late-poll-"), stage
    assert p.pastes == 1                     # NEVER re-pasted


def test_a_strand_that_never_submits_is_STUCK_with_bounded_enters():
    p = Pane(swallow=10 ** 6)
    assert run(p) == ("stuck", "stuck")
    assert p.enters == B.FIRST_ENTERS + B.LATE_ENTERS_MAX
    assert p.pastes == 1


def test_a_paste_that_never_lands_is_FAILED():
    assert run(Pane(never_lands=True)) == ("failed", "no-receipt")


def test_a_preexisting_chip_is_never_pasted_onto_or_entered():
    p = Pane(preexisting="[Pasted text #3 +40 lines]")
    assert run(p) == ("stuck", "stranded-chip")
    assert p.pastes == 0 and p.enters == 0


def test_our_own_earlier_strand_is_submitted_not_repasted():
    p = Pane(preexisting=TEXT)
    assert run(p) == ("submitted", "enter-1")
    assert p.pastes == 0


def test_copy_mode_is_exited_first():
    p = Pane(in_mode=True)
    assert run(p)[0] == "submitted"
    assert ("send-keys", "-t", "=pm-x:", "q") in p.calls


def test_every_tmux_target_is_EXACT():
    p = Pane(swallow=1)
    run(p)
    targets = [a[a.index("-t") + 1] for a in p.calls if "-t" in a]
    assert targets and all(t == "=pm-x:" for t in targets), targets


# ---- the two OLD false positives, pinned so neither can come back ----------------------------

def test_REGRESSION_visible_but_unsubmitted_is_not_success():
    """spawn-agent.sh's old test: 'first 60 chars visible in the last 15 lines' = success."""
    p = Pane(swallow=10 ** 6)
    assert run(p)[0] == "stuck"
    assert TEXT[:60] in p.render()            # visible the whole time, and still not success


def test_REGRESSION_chip_hidden_text_is_not_success():
    """rotate_agent's old test: 'text no longer visible' = committed. A chip hides it."""
    p = Pane(swallow=10 ** 6, chip=True)
    out, _ = run(p, LONG)
    assert LONG[:60] not in p.render()        # text invisible ...
    assert out == "stuck"                     # ... and still correctly not submitted


# ---- the target guard ------------------------------------------------------------------------

def _db(tmp_path, status="online"):
    db = tmp_path / "reg.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE canonical (root, generation_id, tmux_session, status)")
    con.execute("INSERT INTO canonical VALUES ('pm-x', 1, 'pm-x', ?)", (status,))
    con.commit()
    con.close()
    return str(db)


def _created(age):
    return lambda *a, timeout=10: (0, f"{1_000_000 - age}\n")


def test_guard_live_canonical_is_refused_and_nothing_is_typed(tmp_path):
    db = _db(tmp_path)
    assert B._canonical_live("pm-x", tmux_fn=_created(3600), now=1_000_000, db_path=db)
    p = Pane()
    out = B.boot_inject("pm-x", TEXT, tmux_fn=p, sleep_fn=lambda s: None, log_fn=lambda m: None,
                        canonical_live_fn=lambda s, tmux_fn=None: True)
    assert out == ("failed", "refused-canonical")
    assert p.pastes == 0 and p.enters == 0


def test_guard_allow_canonical_types_the_notice():
    p = Pane()
    out = B.boot_inject("pm-x", TEXT, tmux_fn=p, sleep_fn=lambda s: None, log_fn=lambda m: None,
                        allow_canonical=True, canonical_live_fn=lambda s, tmux_fn=None: True)
    assert out[0] == "submitted"


def test_guard_a_just_created_canonical_session_is_its_own_boot(tmp_path):
    db = _db(tmp_path)
    assert not B._canonical_live("pm-x", tmux_fn=_created(20), now=1_000_000, db_path=db)


def test_guard_non_canonical_and_offline_are_not_live(tmp_path):
    assert not B._canonical_live("pm-x-g2", tmux_fn=_created(3600), now=1_000_000,
                                 db_path=_db(tmp_path))
    (tmp_path / "off").mkdir()
    assert not B._canonical_live("pm-x", tmux_fn=_created(3600), now=1_000_000,
                                 db_path=_db(tmp_path / "off", status="retired"))


def test_guard_cannot_tell_refuses(tmp_path):
    db = _db(tmp_path)
    assert B._canonical_live("pm-x", tmux_fn=lambda *a, timeout=10: (1, ""), now=1, db_path=db)
    assert B._canonical_live("pm-x", tmux_fn=_created(1), now=1, db_path=str(tmp_path / "nope" / "x.db"))


def test_cli_exit_codes(monkeypatch, tmp_path):
    for outcome, code in (("submitted", 0), ("stuck", 2), ("failed", 3)):
        monkeypatch.setattr(B, "boot_inject", lambda *a, _o=outcome, **k: (_o, "s"))
        assert B.main(["pm-x", "--text", "hi"]) == code


def test_guard_an_install_without_an_identity_db_has_no_canonical_seat(tmp_path, monkeypatch):
    """A fresh public install never creates state/orchestra-registry.db. "Cannot tell" there would
    refuse every spawn the install makes; there is simply no canonical seat to protect."""
    monkeypatch.setattr(B, "ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(B, "_HERE", str(tmp_path / "scripts"))
    assert B._registry_db() is None
    assert not B._canonical_live("gm", tmux_fn=_created(3600), now=1_000_000)


# ---------------------------------------------------------------- by effect, on a real tmux pane
# The Pane fake above models the composer; these run boot_inject against a real terminal program
# that LOSES the first N Enters (scripts/fixtures/lossy_enter_tui.py), on a PRIVATE tmux server
# (own TMUX_TMPDIR, $TMUX unset), never the operator's. Timings are shortened in-process; the code
# path is the shipped one. Ran unshortened against the live module: drop 0/1/4/99 -> enter-1 /
# enter-2 / late-poll-30s / stuck, never two copies.

import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_TUI = os.path.join(_HERE, "fixtures", "lossy_enter_tui.py")
REAL = "You are lab-seat. Read /tmp/agent-init-lab-seat.md and follow all instructions in it."


@pytest.fixture
def real_pane(tmp_path):
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")
    sock_dir = tempfile.mkdtemp(prefix="ibl.", dir="/tmp")    # short: a socket path has a size limit
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.update(TMUX_TMPDIR=sock_dir, ORCHESTRA_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")

    def tmux(*args):
        return subprocess.run(["tmux", *args], env=env, capture_output=True, text=True)

    def run(drop):
        tmux("new-session", "-d", "-s", "lab-seat", "-x", "200", "-y", "40", f"python3 {FAKE_TUI} {drop}")
        code = (f"import sys; sys.path.insert(0, {_HERE!r}); import boot_inject as B; "
                "B.FIRST_ENTER_GAP_S = 0.5; B.POLL_EVERY_S = 0.5; B.POLL_FOR_S = 3.0; "
                f"print(B.boot_inject('lab-seat', {REAL!r}, 'claude', log_fn=lambda m: None))")
        subprocess.run(["sleep", "0.5"])
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        return r.stdout.strip(), tmux("capture-pane", "-p", "-t", "=lab-seat:").stdout
    yield run
    tmux("kill-session", "-t", "=lab-seat")
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.mark.parametrize("drop,stage", [(0, "enter-1"), (1, "enter-2"), (4, "late-poll")])
def test_BY_EFFECT_lost_enters_are_retried_until_the_prompt_submits(real_pane, drop, stage):
    out, screen = real_pane(drop)
    assert out.startswith("('submitted', '" + stage), out
    assert "● working on it" in screen
    assert screen.count("You are lab-seat.") == 1      # one paste, never a second copy


def test_BY_EFFECT_a_prompt_that_never_submits_is_stuck_with_one_copy(real_pane):
    out, screen = real_pane(99)
    assert out == "('stuck', 'stuck')", out
    assert screen.count("You are lab-seat.") == 1


# --- codex: a freshly started CLI is busy for a moment on its own (PR 0) -----------------------
# Gate container, codex 0.153.4 with an MCP server configured: for ~2 s after start the pane shows
# "• Starting MCP servers (1/2): … (0s • esc to interrupt)" (real capture below). Boot used to give
# up at once ("stuck (codex-busy)") and the seat sat idle with no instructions.
_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "codex")
_STARTING = open(os.path.join(_FIX, "starting_mcp_0.153.4.pane.txt")).read()


class CodexPane(Pane):
    def __init__(self, busy_captures):
        super().__init__()
        self.busy_left = busy_captures
        self.scroll = ["╭ OpenAI Codex", "  some earlier output"]

    def render(self):
        return "\n".join(self.scroll + [f"› {self.composer or 'Ask Codex to do anything'}", "",
                                        "  gpt-5.6-terra default · ~/x"])

    def __call__(self, *args, timeout=10):
        if args[0] == "capture-pane" and "-S" not in args and self.busy_left > 0:
            self.busy_left -= 1
            self.calls.append(args)
            return 0, _STARTING
        return super().__call__(*args, timeout=timeout)


def run_codex(pane):
    slept = []
    out = B.boot_inject("cx", TEXT, "codex", tmux_fn=pane, sleep_fn=slept.append, log_fn=lambda m: None,
                        canonical_live_fn=lambda s, tmux_fn=None: False)
    return out, slept


def test_the_fixture_is_codex_starting_its_mcp_servers():
    assert "Starting MCP servers" in _STARTING and "esc to interrupt" in _STARTING
    assert "◦ Working" not in _STARTING, "the shared Working read alone would miss this screen"


def test_codex_boot_waits_out_mcp_startup_then_submits():
    pane = CodexPane(busy_captures=3)
    (outcome, stage), slept = run_codex(pane)
    assert outcome == "submitted", stage
    assert pane.pastes == 1 and sum(1 for s in slept if s == 1.0) >= 3


def test_codex_boot_never_types_into_a_pane_busy_past_the_bound():
    pane = CodexPane(busy_captures=10_000)
    (outcome, stage), slept = run_codex(pane)
    assert (outcome, stage) == ("stuck", "codex-busy")
    assert pane.pastes == 0 and not any(c[0] in ("paste-buffer", "send-keys") for c in pane.calls)
    assert sum(slept) <= B.CODEX_BOOT_BUSY_WAIT_S + 1


def test_codex_seats_launch_with_the_update_menu_off():
    """codex 0.153.4 can open with "Update available! … › 1. Update now" (real capture), and Enter
    picks UPDATE: the boot prompt's Enter would run `npm install -g @openai/codex` and move the seat
    off its pinned version. spawn-agent.sh launches codex with the startup check off."""
    upd = open(os.path.join(_FIX, "update_prompt_0.153.4.pane.txt")).read()
    assert "Update available" in upd and "› 1. Update now" in upd
    src = open(os.path.join(os.path.dirname(_FIX), "..", "..", "spawn-agent.sh")).read()
    line = [l for l in src.splitlines() if 'runtime" == "codex" ]] && bypass_flag=' in l]
    assert len(line) == 1 and "-c check_for_update_on_startup=false" in line[0]
