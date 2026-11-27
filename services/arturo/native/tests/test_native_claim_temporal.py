"""Claim gate: a state word inside a temporal clause ("I'll let you know when it's done") says when something WILL
happen, not that it did, so it is no claim (codex round 8 on #698; parent msg_00c4361e). A bare "it's done" / "it's
saved" stays a claim, in both directions, and the gate decides on the words streamed so far."""
import pytest

from services.arturo.native import claim_gate

TEMPORAL = [
    "I'll let you know when it's done.",
    "I'll tell you when it's done.",
    "I'll let you know once it's saved.",
    "I'll ping you as soon as it's done!",
    "After it's done I'll read you the summary.",
    "Hang on until it's finished.",
    "I'll check back when that's all done.",
    "Once the task is done, I'll tell you.",
    "When it has been sent I'll say so.",
    "I'll let you know when done.",
    "I'll come back to you once it's taken care of.",
    "Whenever it's ready or when it's up, I'll say.",
]

BARE = [
    "It's done.",
    "It's saved.",
    "That's done.",
    "All done!",
    "Done.",
    "It's done, I'll let you know when the next one is.",
    "When you asked, it's done.",
    "I'll tell you when the next one starts. It's saved.",
    "Once again, it's done.",
    "After all, that's handled.",
    "Just like before it is saved.",
    "As before it is done.",
    "For once it's done on time.",
    "It went through all at once it's done.",
    "Why restart it when it's running fine?",
    "There's nothing to do when it's done already.",
    "No need to redo it once it's saved anyway.",
]


@pytest.mark.parametrize("said", TEMPORAL)
def test_a_state_word_in_a_temporal_clause_is_no_claim(said):
    assert claim_gate.claim_kind(said) is None, said


@pytest.mark.parametrize("said", BARE)
def test_a_bare_state_claim_stays_a_claim(said):
    assert claim_gate.claim_kind(said) == ("state", None), said


def _stream(text, backed=False):
    g = claim_gate.ClaimGate(is_backed=lambda kind: backed)
    for word in text.split(" "):
        g.feed_transcript(word + " ")
    return g


@pytest.mark.parametrize("said", TEMPORAL)
def test_streamed_word_by_word_a_temporal_clause_is_never_cut_off(said):
    g = _stream(said)
    assert not g.blocked and g.blocks == [], said


@pytest.mark.parametrize("said", ["It's done.", "That's done, I'll tell you when the next one is."])
def test_streamed_word_by_word_a_bare_claim_is_still_cut_off(said):
    g = _stream(said)
    assert g.blocked and g.correction_owed, said


@pytest.mark.parametrize("said", TEMPORAL)
def test_no_prefix_of_a_temporal_clause_is_a_claim(said):
    """However the transcript deltas split the words, no partial text makes the clause a claim."""
    assert [said[:i] for i in range(len(said) + 1) if claim_gate.claim_kind(said[:i])] == [], said


def test_a_temporal_clause_does_not_hide_an_action_claim_beside_it():
    assert claim_gate.claim_kind("I sent it to gm, I'll tell you when it's done.") == ("action", "message")
