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
