"""RED tests for the attach/resize full-screen-repaint false-streaming
(incident qnr_d49f5401 follow-on, gm commission msg_3f856349).

Ground truth = TWO REAL captures from the live fleet (2026-09-14):
  * b1-resize-repaint.ansi — the FULL pipe-pane burst an idle claude pane
    (close-crm-integration) emitted for a single 1-column tmux resize:
    15,554 bytes, 4x ESC[H cursor-home + 108x ESC[K erase-line + 106
    CR-rewritten lines = a full-screen in-place redraw. ZERO model activity.
  * b1-generation-perchar.ansi — real generation bytes sampled from a live
    streaming seat's sink (alex-call-prep): per-char CR/cursor-up rewrites,
    ESC[H 0 / ESC[K 2.

The incident mechanism: the rolling tail learns ONLY novel-classed content, and
the composer/status-bar UI text appears ONLY inside repaint-classed bursts (a
generation-time footer rewrite cleans to a char or two, trivially contained,
verdict repaint, tail NOT extended). So a full-screen repaint's bottom-2KB
always carries text the tail has never seen -> containment fails ->
novel_content -> bytes_flowing -> derive_status 'streaming' on an IDLE pane ->
verified_inject refuses Shaw's card-answer as busy. The trigger (Shaw attaching
to watch the seat he just answered) is CORRELATED with submit, so attempt #1 is
exactly the one refused.

Contract under test (the fix): a structurally-full-screen in-place redraw
(cursor-home present + erase-line ops at screen scale) is 'repaint' even when
tail containment fails — and its content EXTENDS the tail so later partial
repaints contain. Real generation and the existing derived-burst suite must be
byte-identically classified (novelty_test.py stays green).
"""
import os

from lineage_daemon.realtime.novelty import classify_burst
from lineage_daemon.realtime.provider_profiles import profile_for
from lineage_daemon.realtime.status_deriver import derive_status
from lineage_daemon.realtime.ansi import strip_ansi

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "contract", "transcript", "fixtures")
_CLAUDE = profile_for("claude")


def _fixture(name):
    with open(os.path.join(_FIX, "claude", name), "rb") as fh:
        return fh.read()


def _status_for(verdict):
    sample = {"bytes_flowing": verdict == "novel_content", "live_pids": 3}
    return derive_status(sample, profile=_CLAUDE)


def _end_of_turn_tail():
    """The rolling tail as steady-state operation actually builds it: the
    turn's APPENDED body text (what forward bursts contributed), WITHOUT the
    composer/status-bar UI lines — those only ever ride repaint-classed bursts
    and never extend the tail. Derived from the capture itself: the screen body
    minus the footer region below the last separator rule."""
    text = strip_ansi(_fixture("b1-resize-repaint.ansi").decode("utf-8", "replace"))
    lines = text.splitlines()
    body = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("───") or s.startswith("❯") or s.startswith("⬆") \
                or s.startswith("⏵⏵"):
            continue
        body.append(s)
    tail = " ".join(" ".join(body).split())
    return tail[-2048:]


def test_resize_full_screen_repaint_is_not_novel():
    """THE INCIDENT: full-screen resize repaint against a steady-state tail
    must class repaint — today it classes novel_content (containment fails on
    the never-learned UI text) and flips an idle seat to streaming."""
    verdict, _ = classify_burst(_fixture("b1-resize-repaint.ansi"),
                                _end_of_turn_tail(), profile=_CLAUDE)
    assert verdict == "repaint", (
        f"resize repaint classed {verdict!r}: an idle pane reads streaming the "
        f"moment a client attaches/resizes — Shaw watching the seat he just "
        f"answered blocks his own card-answer inject (qnr_d49f5401)")


def test_resize_repaint_does_not_drive_streaming():
    verdict, _ = classify_burst(_fixture("b1-resize-repaint.ansi"),
                                _end_of_turn_tail(), profile=_CLAUDE)
    assert _status_for(verdict) == "idle"


def test_resize_repaint_extends_tail_so_next_partial_contains():
    """The fix must LEARN the repaint's content: after one full-screen repaint,
    the same burst (and any partial re-emission of it) is contained."""
    burst = _fixture("b1-resize-repaint.ansi")
    v1, tail1 = classify_burst(burst, _end_of_turn_tail(), profile=_CLAUDE)
    assert v1 == "repaint"
    v2, _ = classify_burst(burst, tail1, profile=_CLAUDE)
    assert v2 == "repaint"


def test_real_generation_still_novel():
    """Guard (asymmetry doctrine: never buy idle-truth with false-idle): the
    REAL per-char generation capture must keep classing novel_content against
    a steady-state tail (empty tail is the daemon-startup seed branch and
    deliberately classes repaint — not this test's subject)."""
    verdict, _ = classify_burst(_fixture("b1-generation-perchar.ansi"),
                                "prior turn content already learned",
                                profile=_CLAUDE)
    assert verdict == "novel_content"
    assert _status_for(verdict) == "streaming"


def test_generation_after_resize_tail_still_novel():
    """A model turn STARTING right after an attach repaint must still stream:
    new forward-appended text beyond the learned tail is novel."""
    _, tail = classify_burst(_fixture("b1-resize-repaint.ansi"),
                             _end_of_turn_tail(), profile=_CLAUDE)
    fresh = b"\x1b[1mNow beginning the acceptance flow: step one, apply the " \
            b"migrations to prod CloudSQL and verify counts.\n"
    verdict, _ = classify_burst(fresh, tail, profile=_CLAUDE)
    assert verdict == "novel_content"


def test_false_idle_bound_one_tick_mid_generation_resize():
    """Bound B by effect (gm land condition): a resize landing MID-GENERATION
    may cost at most ONE tick — the full-redraw burst (even one carrying new
    text inside the repaint) classes repaint, and the very next per-char
    generation burst reclasses novel_content -> streaming again."""
    _, tail = classify_burst(_fixture("b1-resize-repaint.ansi"),
                             _end_of_turn_tail(), profile=_CLAUDE)
    # tick N: another full-screen redraw arrives mid-turn (worst case) -> repaint
    v_mid, tail = classify_burst(_fixture("b1-resize-repaint.ansi"), tail,
                                 profile=_CLAUDE)
    assert v_mid == "repaint"
    # tick N+1: real generation bytes (the live per-char capture) -> novel again
    v_next, _ = classify_burst(_fixture("b1-generation-perchar.ansi"), tail,
                               profile=_CLAUDE)
    assert v_next == "novel_content"
    assert _status_for(v_next) == "streaming"
