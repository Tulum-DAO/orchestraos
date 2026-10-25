"""Router Stage 0.5/0.6/1/2 vs a DIM ghost suggestion.

After a delivery Claude suggests the last submitted message back as a dim ghost. Automated
senders often start every message with the same 30 characters, so the plain read "found" the
next delivery's probe in the composer: Stage 0.6 skipped the paste and pressed Enter on an empty
composer forever. A ghost must count as EMPTY. Drives the REAL inject() with a fake tmux (plain
and `-e` captures); nothing touches a real pane.
"""
import importlib.util
import os
from types import SimpleNamespace

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_ghost", os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

PREV = "[MSG from sync-service | high] [DELTA] refresh from source blue — Read /tmp/agent-msg-a1111.md"
NEXT = "[MSG from sync-service | high] [DELTA] refresh from source blue — Read /tmp/agent-msg-a2222.md"
RULE = "─" * 40
assert PREV[:30] == NEXT[:30]


def screen(composer_plain, composer_ansi, scroll=("● earlier work",)):
    plain = "\n".join([*scroll, RULE, f"❯\xa0{composer_plain}", RULE, "  ⏵⏵ bypass permissions on"])
    ansi = "\n".join([*scroll, RULE, f"❯\xa0{composer_ansi}", RULE, "  ⏵⏵ bypass permissions on"])
    return plain, ansi


GHOST = screen(PREV, f"\x1b[7m \x1b[27m\x1b[2m{PREV}\x1b[22m")       # dim = ghost suggestion
TYPED = screen(NEXT, f"\x1b[0m{NEXT}\x1b[7m \x1b[27m")                # default colour = typed


class FakeTmux:
    """Plain/ANSI pane. paste-buffer types the buffer (default colour); Enter submits whatever
    is TYPED (moves it to scrollback, empties the composer); Enter on a ghost does nothing."""

    def __init__(self, start):
        self.plain, self.ansi = start
        self.typed = None if start is GHOST else NEXT
        self.buf, self.calls, self.scroll = None, [], ["● earlier work"]

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "set-buffer":
            self.buf = args[-1]
        elif args[0] == "paste-buffer":
            self.typed = self.buf
            self.plain, self.ansi = screen(self.buf, f"\x1b[0m{self.buf}", self.scroll)
        elif args[0] == "send-keys" and args[-1] == "Enter" and self.typed:
            self.scroll = [*self.scroll, f"❯ {self.typed}"]
            self.typed = None
            self.plain, self.ansi = screen("", "", self.scroll)
        elif args[0] == "display-message":
            return SimpleNamespace(returncode=0, stdout="0\n")
        if args[0] == "capture-pane":
            return SimpleNamespace(returncode=0, stdout=self.ansi if "-e" in args else self.plain)
        return SimpleNamespace(returncode=0, stdout="")


def _recorded(monkeypatch, tmp_path):
    """Our earlier attempt is recorded (a typed journal row for this very text): only then is a
    typed copy on the line ours to submit (an unrecorded copy is held)."""
    import sys
    sys.path.insert(0, _here)
    import inject_journal
    monkeypatch.setenv("WATCH_GATEWAY_INJECT_JOURNAL_DIR", str(tmp_path / "j"))
    inject_journal.record("agent-pane", NEXT, NEXT[:40]); inject_journal.mark_typed("agent-pane")


def _run(monkeypatch, start):
    t = FakeTmux(start)
    monkeypatch.setattr(mr, "tmux", t)
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    return mr.inject("agent-pane", NEXT), t


def test_classifier_sees_the_dim_line_as_ghost(monkeypatch):
    monkeypatch.setattr(mr, "tmux", lambda *a: SimpleNamespace(returncode=0, stdout=GHOST[1]))
    assert mr.composer_is_ghost("agent-pane") is True
    monkeypatch.setattr(mr, "tmux", lambda *a: SimpleNamespace(returncode=0, stdout=TYPED[1]))
    assert mr.composer_is_ghost("agent-pane") is False


def test_ghost_with_matching_prefix_is_treated_as_empty_and_the_message_is_delivered(monkeypatch):
    res, t = _run(monkeypatch, GHOST)
    assert res == "submitted"
    assert any(c[0] == "paste-buffer" for c in t.calls), "must PASTE: a ghost is not our text"
    assert any(NEXT in line for line in t.scroll)


def test_real_typed_duplicate_still_takes_the_dedup_path(monkeypatch, tmp_path):
    """A TYPED (non-dim) copy of our probe is a stranded earlier paste: no second paste."""
    _recorded(monkeypatch, tmp_path)
    res, t = _run(monkeypatch, TYPED)
    assert res == "submitted"
    assert not any(c[0] == "paste-buffer" for c in t.calls), "must NOT paste a duplicate"


def test_read_failure_keeps_the_conservative_guard(monkeypatch):
    monkeypatch.setattr(mr, "tmux", lambda *a: SimpleNamespace(returncode=1, stdout=""))
    assert mr.composer_is_ghost("agent-pane") is False


# ---- Stage 1 receipt + Stage 2 submit ----------------------------------------------------------
class FakeTmux2(FakeTmux):
    """paste_lands=False: the paste never reaches the composer (the ghost stays).
    ghost_after_submit=True: after a real submit, Claude shows the JUST-SENT message back as a dim
    ghost, which shares the probe prefix. The previous same-prefix message is already in scrollback."""

    def __init__(self, start, *, paste_lands=True, ghost_after_submit=False):
        super().__init__(start)
        self.paste_lands, self.ghost_after_submit = paste_lands, ghost_after_submit
        self.scroll = ["● earlier work", f"❯ {PREV}"]
        self.plain, self.ansi = screen(PREV, f"\x1b[7m \x1b[27m\x1b[2m{PREV}\x1b[22m", self.scroll)

    def __call__(self, *args):
        if args[0] == "paste-buffer" and not self.paste_lands:
            self.calls.append(args)
            return SimpleNamespace(returncode=0, stdout="")
        if args[0] == "send-keys" and args[-1] == "Enter" and self.typed and self.ghost_after_submit:
            self.calls.append(args)
            sent = self.typed
            self.scroll = [*self.scroll, f"❯ {sent}"]
            self.typed = None
            self.plain, self.ansi = screen(sent, f"\x1b[7m \x1b[27m\x1b[2m{sent}\x1b[22m", self.scroll)
            return SimpleNamespace(returncode=0, stdout="")
        return super().__call__(*args)


def _run2(monkeypatch, **kw):
    t = FakeTmux2(GHOST, **kw)
    monkeypatch.setattr(mr, "tmux", t)
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    return mr.inject("agent-pane", NEXT), t


def test_stage1_paste_that_never_landed_over_a_matching_ghost_is_NOT_acked(monkeypatch):
    """Silent lost mail: the ghost matches the probe, the paste never arrives. Must not be
    'submitted' (nor even 'landed'): it must report failure so the row is retried."""
    res, t = _run2(monkeypatch, paste_lands=False)
    assert res == "failed"
    assert not any(c[0] == "send-keys" and c[-1] == "Enter" for c in t.calls), "no Enter on an unlanded paste"


def test_stage2_ghost_of_the_just_sent_message_still_counts_as_submitted(monkeypatch):
    res, t = _run2(monkeypatch, ghost_after_submit=True)
    assert res == "submitted"
    assert any(NEXT in line for line in t.scroll)
    assert sum(1 for c in t.calls if c[0] == "send-keys" and c[-1] == "Enter") == 1, "no extra Enters"


# ---- GHOST-PATH log line: the only by-effect view of the ghost branches ----------------------
def _logged(monkeypatch, tmux_fake):
    lines = []
    monkeypatch.setattr(mr, "log", lines.append)
    monkeypatch.setattr(mr, "tmux", tmux_fake)
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    res = mr.inject("agent-pane", NEXT, msg_id="m-ghost-1")
    return res, [l for l in lines if l.startswith("GHOST-PATH")]


def test_ghost_path_logged_when_stage06_treats_ghost_as_empty(monkeypatch):
    res, g = _logged(monkeypatch, FakeTmux(GHOST))
    assert res == "submitted"
    assert g == ["GHOST-PATH agent-pane stage=0.6 msg=m-ghost-1 -> ghost is not our text, pasting"]


def test_ghost_path_logged_when_stage1_refuses_a_receipt_over_a_ghost(monkeypatch):
    res, g = _logged(monkeypatch, FakeTmux2(GHOST, paste_lands=False))
    assert res == "failed"
    assert "GHOST-PATH agent-pane stage=1 msg=m-ghost-1 -> receipt refused (composer is a ghost)" in g


def test_ghost_path_logged_when_stage2_accepts_the_just_sent_ghost(monkeypatch):
    res, g = _logged(monkeypatch, FakeTmux2(GHOST, ghost_after_submit=True))
    assert res == "submitted"
    assert g[-1] == "GHOST-PATH agent-pane stage=2 msg=m-ghost-1 -> submitted (ghost of the sent text)"


def test_no_ghost_path_line_on_the_typed_duplicate_path(monkeypatch, tmp_path):
    _recorded(monkeypatch, tmp_path)
    res, g = _logged(monkeypatch, FakeTmux(TYPED))
    assert res == "submitted" and g == []


def test_delivery_loop_passes_the_message_id_to_inject():
    # Structural: the delivery loop is not unit-drivable; without msg_id every line says msg=?
    src = open(os.path.join(_here, "message-router.py")).read()
    assert 'inject(session, text, _runtime, msg_id=msg["id"])' in src
