# test_vq6.py — supersede-on-arrival (pause-duplication). DEC-1786425204.
import threading
import pytest

from services.arturo import vq6


# ---------------------------------------------------------------- is_partial_of
def test_partial_prefix_is_superseded():
    assert vq6.is_partial_of("what's the status of", "what's the status of acme dev")


def test_partial_with_asr_filler_noise():
    # ebc3c66 normalization drops filler; the partial is still a prefix of the full.
    assert vq6.is_partial_of("hey so what is the", "hey so what is the update on globex")


def test_full_not_partial_of_shorter_returns_false():
    # directional: the longer turn is NOT a partial of the shorter one (no reverse cancel)
    assert not vq6.is_partial_of("what's the status of acme dev", "what's the status of")


def test_two_distinct_full_turns_do_not_match():
    assert not vq6.is_partial_of("how is acme doing", "what's the weather in tulum")


def test_identical_resend_is_superseded():
    # BUG-3 correction: an IDENTICAL re-send IS a duplicate that should supersede (cancel the first
    # in-flight answer, answer once) — the old "equal length never matches" rule let ElevenLabs'
    # exact re-emits through and Arturo double-answered.
    assert vq6.is_partial_of("check the deploy status", "check the deploy status")


def test_empty_turns_false():
    assert not vq6.is_partial_of("", "anything at all here")
    assert not vq6.is_partial_of("something", "")


def test_ratio_branch_catches_high_similarity_superset():
    # non-clean-prefix but >=0.9 similar AND strictly longer -> still superseded (safety net)
    assert vq6.is_partial_of("check the acme deploy statis",
                             "check the acme deploy status now")


def test_same_length_retranscription_superseded():
    # BUG-3: same-length re-transcription (punctuation-only diff) must now supersede (was missed
    # because the old guard required the full turn to be strictly longer)
    assert vq6.is_partial_of(
        "I'm curious where you got those from. And why did you just send a bunch of messages",
        "I'm curious where you got those from, and why did you just send a bunch of messages")


def test_same_length_distinct_turns_not_superseded():
    # two DISTINCT equal-length utterances must NOT cancel each other (directional safety held)
    assert not vq6.is_partial_of("call the acme dev agent now please",
                                 "kill the globex build agent right away")


def test_conservative_below_threshold_non_prefix_does_not_match():
    # a non-prefix pair that lands just under 0.9 is intentionally NOT superseded: prefer a clean
    # prefix match; worst case is one duplicate response (== today), never a false cancel.
    assert not vq6.is_partial_of("hows the acme deploy going",
                                 "how's the acme deploy going today")


# ---------------------------------------------------------------- fuzzy_repeat
def test_fuzzy_repeat_catches_near_identical():
    assert vq6.fuzzy_repeat(["Still on it.", "Still on it. ", "Still on it"])


def test_fuzzy_repeat_ignores_distinct():
    assert not vq6.fuzzy_repeat(["Got it, checking.", "Here is the answer.", "Anything else?"])


def test_fuzzy_repeat_needs_three():
    assert not vq6.fuzzy_repeat(["Still on it.", "Still on it."])


# ---------------------------------------------- registry: supersede + commit gate
def test_register_supersedes_prior_partial():
    r = vq6.InflightRegistry()
    a = vq6.new_entry("what's the status of")
    r.register_and_supersede("k", a, "what's the status of")
    b = vq6.new_entry("what's the status of acme")
    superseded = r.register_and_supersede("k", b, "what's the status of acme")
    assert superseded is a
    assert a["cancelled"] is True and a["cancel"].is_set()
    assert r.current("k") is b            # B now owns the slot


def test_register_does_not_supersede_distinct_turn():
    r = vq6.InflightRegistry()
    a = vq6.new_entry("how is acme doing")
    r.register_and_supersede("k", a, "how is acme doing")
    b = vq6.new_entry("what's the weather in tulum")
    superseded = r.register_and_supersede("k", b, "what's the weather in tulum")
    assert superseded is None
    assert a["cancelled"] is False and not a["cancel"].is_set()


def test_commit_before_cancel_latches_and_blocks_supersede():
    r = vq6.InflightRegistry()
    a = vq6.new_entry("what's the status of")
    r.register_and_supersede("k", a, "what's the status of")
    # A commits to a dispatch FIRST
    aborted = r.commit_or_abort(a)
    assert aborted is False and a["tool_dispatched"] is True
    # now B arrives with the full utterance -> must NOT cancel A (already latched)
    b = vq6.new_entry("what's the status of acme")
    superseded = r.register_and_supersede("k", b, "what's the status of acme")
    assert superseded is None
    assert a["cancelled"] is False and not a["cancel"].is_set()


def test_commit_after_cancel_aborts_without_latching():
    r = vq6.InflightRegistry()
    a = vq6.new_entry("what's the status of")
    r.register_and_supersede("k", a, "what's the status of")
    b = vq6.new_entry("what's the status of acme")
    r.register_and_supersede("k", b, "what's the status of acme")   # cancels A
    # A now reaches its commit gate AFTER being cancelled -> abort, NO dispatch
    aborted = r.commit_or_abort(a)
    assert aborted is True
    assert a["tool_dispatched"] is False       # never latched -> never dispatched
    assert a["cancelled"] is True


def test_cleanup_identity_gated_does_not_clobber_successor():
    r = vq6.InflightRegistry()
    a = vq6.new_entry("what's the status of")
    r.register_and_supersede("k", a, "what's the status of")
    b = vq6.new_entry("what's the status of acme")
    r.register_and_supersede("k", b, "what's the status of acme")
    # A's finally runs AFTER B registered -> must NOT delete B's live slot
    r.cleanup("k", a)
    assert r.current("k") is b
    # B's own cleanup does remove it
    r.cleanup("k", b)
    assert r.current("k") is None


def test_commit_or_abort_race_single_winner():
    # Hammer commit_or_abort vs a cancel from another thread: never both latch AND report abort=False
    # after a cancel; the gate is atomic.
    for _ in range(200):
        r = vq6.InflightRegistry()
        a = vq6.new_entry("partial turn here")
        r.register_and_supersede("k", a, "partial turn here")
        results = {}
        barrier = threading.Barrier(2)

        def canceller():
            barrier.wait()
            b = vq6.new_entry("partial turn here that is much longer now")
            r.register_and_supersede("k", b, "partial turn here that is much longer now")

        def committer():
            barrier.wait()
            results["aborted"] = r.commit_or_abort(a)

        t1 = threading.Thread(target=canceller)
        t2 = threading.Thread(target=committer)
        t1.start(); t2.start(); t1.join(); t2.join()
        # Invariant: if A latched (aborted False) it must NOT be cancelled; if it aborted it must be
        # cancelled and never latched. Never both.
        if results["aborted"]:
            assert a["cancelled"] is True and a["tool_dispatched"] is False
        else:
            assert a["tool_dispatched"] is True
