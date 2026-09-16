"""RED (DEC-1789517918317482 §2 L3 + G1/G2/G3, OB g42): finished-and-waiting swap leg.
L3: Blue idle (turn complete) for >= CARD_WAIT_MIN_IDLE_S AND >= 1 pending own card => swap
`card:wait-swap`, no ctx floor. Guards on L2/L3 only: composer non-empty/unreadable =>
suppress:composer; the operator attached => suppress:attached. L1 (ctx ceiling) and death are never
gated. In the lull band a tripped guard suppresses the SWAP but leaves prewarm intact
(a prewarm cannot lose work; noop there would starve the green boot)."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.decide_bg import (  # noqa: E402
    decide_bg, CARD_WAIT_MIN_IDLE_S, LULL_MIN_IDLE_S)


def _obs(ctx_pct=0.30, state="idle", age=61, cards=1, composer="", attached=False,
         turn_complete=None, death=None, calibrated=True):
    return {"root": "x", "runtime": "codex", "death": death, "ceiling_calibrated": calibrated,
            "ctx_pct": ctx_pct, "state": state, "state_age_s": age,
            "blue_pending_cards": cards, "blue_composer_text": composer,
            "blue_attached": attached,
            "blue_turn_complete": (state == "idle") if turn_complete is None else turn_complete}


def test_min_idle_is_one_full_beat():
    assert CARD_WAIT_MIN_IDLE_S == 60


def test_l3_swaps_when_finished_and_waiting_on_own_card():
    d = decide_bg(_obs())
    assert (d["action"], d["reason"]) == ("swap", "card:wait-swap")
    assert d["never_gated"] is False and d["alarm"] is False


def test_l3_composer_text_suppresses():
    d = decide_bg(_obs(composer="deploy it"))
    assert (d["action"], d["reason"]) == ("noop", "suppress:composer")


def test_l3_unreadable_composer_suppresses():
    d = decide_bg(_obs(composer=None))
    assert (d["action"], d["reason"]) == ("noop", "suppress:composer")


def test_l3_attached_suppresses():
    d = decide_bg(_obs(attached=True))
    assert (d["action"], d["reason"]) == ("noop", "suppress:attached")


def test_l3_no_cards_is_plain_ctx_noop():
    d = decide_bg(_obs(cards=0))
    assert (d["action"], d["reason"]) == ("noop", "ctx:0.30")


def test_l3_not_idle_or_turn_incomplete_is_noop():
    assert decide_bg(_obs(state="working"))["action"] == "noop"
    assert decide_bg(_obs(turn_complete=False))["action"] == "noop"


def test_l3_needs_observed_idle_age():
    d = decide_bg(_obs(age=CARD_WAIT_MIN_IDLE_S - 1))
    assert (d["action"], d["reason"]) == ("noop", "ctx:0.30")


def test_l3_never_fires_uncalibrated():
    d = decide_bg(_obs(calibrated=False))
    assert d["reason"] == "uncalibrated:solo-alarm"


def test_l1_ceiling_ignores_guards():
    d = decide_bg(_obs(ctx_pct=0.85, composer="x", attached=True, cards=0))
    assert (d["action"], d["reason"]) == ("swap", "ctx:swap")


def test_death_ignores_guards():
    d = decide_bg(_obs(death="exhausted", composer="x", attached=True))
    assert d["action"] == "swap" and d["reason"].startswith("death:")


def test_l2_lull_guarded_falls_to_prewarm_not_swap():
    d = decide_bg(_obs(ctx_pct=0.74, age=LULL_MIN_IDLE_S, cards=0, attached=True))
    assert (d["action"], d["reason"]) == ("prewarm", "ctx:prewarm")
    d = decide_bg(_obs(ctx_pct=0.74, age=LULL_MIN_IDLE_S, cards=0, composer="typed"))
    assert (d["action"], d["reason"]) == ("prewarm", "ctx:prewarm")


def test_l2_lull_unguarded_still_swaps():
    d = decide_bg(_obs(ctx_pct=0.74, age=LULL_MIN_IDLE_S, cards=0))
    assert (d["action"], d["reason"]) == ("swap", "ctx:lull-swap")


def test_l3_precedes_l2_in_band_when_card_pending():
    d = decide_bg(_obs(ctx_pct=0.74, age=61, cards=1))
    assert (d["action"], d["reason"]) == ("swap", "card:wait-swap")
