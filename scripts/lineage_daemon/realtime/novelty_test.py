"""RED acceptance tests for B1 content-novelty (DEC-1788493111, spec §9.1 + §9.3).

Ground truth = the REAL captured claude idle screen
(contract/transcript/fixtures/claude/b1-idle-screen.ansi — pipe-pane capture of a
live idle seat). Bursts are DERIVED from it: a redraw re-emits it (cursor-home +
repaint), generation appends new text after it, a chrome tick rewrites the
spinner/elapsed line in place. Each case asserts BOTH the classify_burst verdict
AND the end-to-end status the verdict drives (novel_content -> bytes_flowing ->
streaming; everything else -> not streaming).

Against the PRE-FIX bytes axis (any non-empty burst => streaming) every
non-'novel_content' case here is RED: a redraw / chrome tick read `streaming`.
"""
import os

from lineage_daemon.realtime.novelty import classify_burst
from lineage_daemon.realtime.provider_profiles import profile_for
from lineage_daemon.realtime.status_deriver import derive_status

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "contract", "transcript", "fixtures")
_CLAUDE = profile_for("claude")


def _real_idle_screen():
    with open(os.path.join(_FIX, "claude", "b1-idle-screen.ansi"), "rb") as fh:
        return fh.read()


def _seeded_tail():
    """The rolling tail after the daemon first renders the real idle screen."""
    _, tail = classify_burst(_real_idle_screen(), "", profile=_CLAUDE)
    return tail


def _status_for(verdict):
    """Map a burst verdict to the status derive_status yields for a seat whose
    only signal is that burst (no cpu/io/hooks)."""
    sample = {"bytes_flowing": verdict == "novel_content", "live_pids": 3}
    return derive_status(sample, profile=_CLAUDE)


# --- #1 fleet redraw (the RED centerpiece) ----------------------------------

def test_redraw_of_known_screen_is_repaint_not_streaming():
    tail = _seeded_tail()
    screen = _real_idle_screen()
    # a SIGWINCH redraw: cursor-home + clear + re-emit the SAME screen content
    redraw = b"\x1b[H\x1b[2J" + screen
    verdict, _ = classify_burst(redraw, tail, profile=_CLAUDE)
    assert verdict == "repaint", f"redraw misread as {verdict}"
    assert _status_for(verdict) != "streaming"


# --- #5 real generation -> streaming in one tick ----------------------------

def test_generation_appends_novel_text_is_streaming():
    tail = _seeded_tail()
    # forward append of new assistant text (no cursor-up / no CR rewrite)
    burst = b"\nThe root cause is the deriver counting redraw bytes as output.\n"
    verdict, _ = classify_burst(burst, tail, profile=_CLAUDE)
    assert verdict == "novel_content"
    assert _status_for(verdict) == "streaming"


def test_per_token_region_repaint_still_reads_novel():
    # claude's TUI repaints the current line per token (cursor-position + grow).
    tail = "assistant: the answer is"
    b1 = b"\x1b[40;1Hassistant: the answer is 4"
    v1, t1 = classify_burst(b1, tail, profile=_CLAUDE)
    assert v1 == "novel_content"          # a new token beyond the tail wins over ESC
    b2 = b"\x1b[40;1Hassistant: the answer is 42"
    v2, _ = classify_burst(b2, t1, profile=_CLAUDE)
    assert v2 == "novel_content"


# --- #6 repetitive-token generation -> streaming (agy counter) --------------

def test_repeated_separators_forward_append_is_novel():
    tail = "some table rows"
    v, t = classify_burst(b"|---|---|---|\n", tail, profile=_CLAUDE)
    assert v == "novel_content"           # a real append, even if it repeats
    v2, _ = classify_burst(b"\n\n\n", t, profile=_CLAUDE)
    assert v2 == "novel_content"


# --- #7 sustained idle with ticking chrome -> idle the whole window ----------

def test_ticking_spinner_elapsed_counter_is_never_streaming():
    tail = _seeded_tail()
    # 60 one-second ticks: the spinner/elapsed line rewritten in place (CR)
    for sec in range(43, 103):
        tick = ("\r✻ Brewed for %ds" % sec).encode()
        verdict, tail = classify_burst(tick, tail, profile=_CLAUDE)
        assert verdict in ("repaint", "chrome"), \
            f"tick {sec}s misread as {verdict}"
        assert _status_for(verdict) != "streaming"


# --- pure chrome / ANSI-only burst -> chrome --------------------------------

def test_ansi_only_burst_is_chrome():
    tail = _seeded_tail()
    burst = b"\x1b[2K\x1b[38;5;240m\x1b[0m\x1b[39m"
    verdict, _ = classify_burst(burst, tail, profile=_CLAUDE)
    assert verdict == "chrome"
    assert _status_for(verdict) != "streaming"


def test_context_percent_footer_tick_is_not_streaming():
    # the status-bar block-bar + context% repainting (38% -> 39%) is chrome
    tail = _seeded_tail()
    burst = b"\x1b[52;1H  claude-opus-4-8[1m] | agent-orchestra \xe2\x96\x88\xe2\x96\x88\xe2\x96\x91 39%"
    verdict, _ = classify_burst(burst, tail, profile=_CLAUDE)
    assert verdict != "novel_content"
    assert _status_for(verdict) != "streaming"


# --- 2.1.260 stale post-turn spinner must NOT read streaming (gm gate) -------
def test_stale_completion_spinner_not_streaming():
    tail = _seeded_tail()
    # real 2.1.260 completion line re-emitted forward (no cursor motion)
    v, _ = classify_burst("✻ Brewed for 40s · done 1:13 AM\n".encode(), tail, profile=_CLAUDE)
    assert v != "novel_content", f"stale completion spinner misread as {v}"


def test_active_gerund_spinner_not_streaming():
    tail = _seeded_tail()
    v, _ = classify_burst("Cooking… (39s · ↓1.2k tokens)\n".encode(), tail, profile=_CLAUDE)
    assert v != "novel_content", f"active spinner misread as {v}"


def test_genuine_content_after_spinner_fix_still_streams():
    tail = _seeded_tail()
    v, _ = classify_burst(b"\nHere is the actual answer to your question in full.\n",
                          tail, profile=_CLAUDE)
    assert v == "novel_content"
