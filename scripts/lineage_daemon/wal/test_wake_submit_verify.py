"""RED (gm msg_e7d7f734 queue (a), by effect 2026-09-16 01:30Z): the promoted codex green's
composer held the ingest wake UNSUBMITTED ('› ingest your hydrate digest ...') while the beat
believed the wake had started. Cause: _default_green_wake_fn's probe_started was
`resolve_cid_any(green_alias) is not None` — proof the green took its FIRST (boot) turn, not
that THIS send was submitted — so confirm-started was vacuously true and the bounded
Enter-resend never fired. Fix: started-by-effect = the composer reads EMPTY after the send
(composer_read over a pane capture); text still sitting => one bare Enter (bounded)."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_beat  # noqa: E402

ROOT = "seat-x"
GREEN = "seat-x-g2"
TEXT = ("ingest your hydrate digest (your predecessor's handoff + delivered context), "
        "then hold — do not start new work until told.")


class _Pane:
    """A fake codex pane: send-keys -l puts text in the composer; Enter submits it."""
    def __init__(self, swallow_first_enter=False):
        self.composer = ""
        self.sent = []
        self.swallow = swallow_first_enter
        self.enters = 0

    def send(self, args):
        self.sent.append(args)
        if "-l" in args:
            self.composer += args[-1]       # a real composer APPENDS typed text
        elif args[-1] == "BSpace":
            self.composer = self.composer[:-1]
        elif args[-1] == "Enter":
            self.enters += 1
            if self.swallow and self.enters == 1:
                return                      # the Ink/TUI submit race swallows the first Enter
            self.composer = ""

    def capture(self, _target):
        return ["• Initialized successfully.", f"› {self.composer}" if self.composer else
                "› Ask Codex to do anything", "  gpt-5.6-terra medium fast"]


def _wake(tmp_path, pane):
    return bg_beat._default_green_wake_fn(str(tmp_path), str(tmp_path), ROOT,
                                          capture_fn=pane.capture, send_fn=pane.send,
                                          sleep_s=0)


def test_swallowed_enter_is_detected_by_composer_and_resent_once(tmp_path):
    pane = _Pane(swallow_first_enter=True)
    assert _wake(tmp_path, pane)(GREEN) is True
    assert pane.composer == ""                       # submitted by effect
    injects = [a for a in pane.sent if "-l" in a]
    assert len(injects) == 1                         # text sent exactly once (no duplicate)
    assert pane.enters == 2                          # original Enter + ONE resend


def test_clean_submit_needs_no_resend(tmp_path):
    pane = _Pane()
    assert _wake(tmp_path, pane)(GREEN) is True
    assert pane.enters == 1


def test_text_that_never_submits_is_reported_not_started(tmp_path):
    class Stuck(_Pane):
        def send(self, args):
            self.sent.append(args)
            if "-l" in args:
                self.composer += args[-1]
            elif args[-1] == "BSpace":
                self.composer = self.composer[:-1]
            else:
                self.enters += 1               # Enter never clears the composer
    pane = Stuck()
    assert _wake(tmp_path, pane)(GREEN) is False
    assert pane.composer == TEXT
    assert pane.enters <= 4                    # bounded (1 + max_retries)


# --- stale-screen TUI (gm msg_86bcc168 item 1, by effect 2026-09-16 00:00-00:40 ET) -------
# semantic-recall-wiring-dev-g5 (claude) sat PREWARMING 2.5h: the TUI stopped repainting after
# Enter / C-u, so capture-pane kept showing the OLD composer text while the turn had in fact
# started. One printable key (space) forced the repaint. The probe must not trust two
# byte-identical captures: kick the repaint (printable + BSpace) before judging.
class _StalePane(_Pane):
    """Enter submits (composer really empties) but the SCREEN does not repaint until a
    printable key arrives; capture returns the stale frame until then."""
    def __init__(self):
        super().__init__()
        self.frame = None            # last painted frame (list of lines)
        self.printables = 0

    def _paint(self):
        self.frame = super().capture(None)

    def send(self, args):
        super().send(args)
        if "-l" in args and args[-1] == TEXT:
            self._paint()            # the inject itself paints (text visible)
        elif "-l" in args:
            self.printables += 1     # a benign printable repaints the screen
            self._paint()
        # Enter / BSpace: state changes, screen does NOT repaint

    def capture(self, _target):
        if self.frame is None:
            self._paint()
        return list(self.frame)


def test_stale_screen_is_kicked_with_a_printable_and_reads_started(tmp_path):
    pane = _StalePane()
    assert _wake(tmp_path, pane)(GREEN) is True
    assert pane.composer == ""                       # the turn really did start
    injects = [a for a in pane.sent if "-l" in a and a[-1] == TEXT]
    assert len(injects) == 1                         # ingest text never duplicated
    assert pane.printables >= 1                      # the repaint kick was sent
    kicks = [a for a in pane.sent if "-l" in a and a[-1] == " "]
    bsp = [a for a in pane.sent if a[-1] == "BSpace"]
    assert len(kicks) == len(bsp) >= 1               # every kick is undone by a backspace
    assert pane.enters <= 3                          # bounded: not one Enter per beat for 2.5h


def test_identical_captures_do_not_kick_when_already_started(tmp_path):
    """A clean submit (composer empty on the first read) needs no kick at all."""
    pane = _Pane()
    assert _wake(tmp_path, pane)(GREEN) is True
    assert not [a for a in pane.sent if "-l" in a and a[-1] == " "]
