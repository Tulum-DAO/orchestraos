"""A seat with delegated work in flight is still reachable — count it SEPARATELY from state.

Shaw, 2026-10-06: "we cannot submit a message in chat mode to an agent that has a sub-agent
running because its agent status is 'working' even though the CLI is available to be entered
into ... an agent's sub-agent is running but it's available to receive a message and it can't."

Measured on the live fleet the same day: 17 seats carried the status-line counter, and three of
them reported `thinking` -> `working` while their composer was open and queueing text. The
counter is therefore reported as its OWN field. Folding it into `state` would destroy the only
distinction the operator needs here: "busy" versus "busy, but able to take a message".
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _mod():
    spec = importlib.util.spec_from_file_location("agent_status_mod", os.path.join(_HERE, "agent-status.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_STATUS = "  ⏵⏵ bypass permissions on (shift+tab to cycle)"


def test_counts_one_and_many():
    c = _mod().count_subagents
    assert c([_STATUS + " · ← 1 agent"]) == 1
    assert c([_STATUS + " · ← 3 agents"]) == 3, "the plural form must parse too"


def test_absent_counter_is_zero_not_unknown():
    # The status line only renders the counter while something is running, so "no counter"
    # is a real answer, not a missing one.
    assert _mod().count_subagents([_STATUS]) == 0
    assert _mod().count_subagents([]) == 0


def test_the_counter_is_read_from_the_STATUS_LINE_not_from_transcript_text():
    """The control that matters. This seat's own pane contained the literal string
    '\u2190 1 agent' inside a sentence it had just written, while the real counter said the
    same thing on the status line. A whole-screen scan is self-confusing by construction.

    NOTE the fixture shape. An earlier version of this test put the prose ABOVE the status
    line and was VACUOUS: scanning bottom-up reaches the real counter first either way, so it
    passed against a deliberately unanchored implementation. The prose must sit BELOW the
    status line (or the status line must be absent) for the anchor to be the thing under test.
    """
    c = _mod().count_subagents

    # prose BELOW the real counter: an unanchored bottom-up scan reads the prose first.
    assert c([
        _STATUS + " \u00b7 \u2190 1 agent",
        "\u25cf I quoted \u2190 9 agents in this sentence",
    ]) == 1, "transcript prose below the status line was read as the counter"

    # no status line at all: the only match on screen is prose, and it must be ignored.
    assert c([
        "\u25cf and a status-line counter \u2014 \u2190 4 agents",
        "\u25cf more prose",
    ]) == 0, "prose was read as a counter when no status line was present"


def test_a_bare_hyphen_is_not_the_arrow():
    # An early draft used [←<-] as a character class, which matches a literal '-' and
    # would read an ordinary dashed list item as a subagent counter.
    assert _mod().count_subagents([_STATUS + " - 2 agents"]) == 0


def test_parse_status_reports_subagents_alongside_state():
    """It is an ADDITIVE field: state keeps whatever it already said."""
    m = _mod()
    screen = "\n".join([
        "● did a thing",
        "✻ Beaming… (8m 3s · ↓ 21.6k tokens)",
        _STATUS + " · ← 1 agent",
    ])
    r = m.parse_status(screen)
    assert r["subagents"] == 1
    # The parent really is mid-turn here, and that is still true and still reported.
    # NOTE the two vocabularies: the DETECTOR emits `thinking`; the web's
    # normalizeAgentState() aliases that to `working`. This asserts the detector's own
    # word, so the test cannot silently pass against the wrong layer.
    assert r["state"] == "thinking", r["state"]


def test_a_working_seat_with_NO_delegate_reports_zero():
    # Positive control: the field must distinguish, not just always answer 1.
    m = _mod()
    screen = "\n".join([
        "✻ Beaming… (8m 3s · ↓ 21.6k tokens)",
        _STATUS,
    ])
    r = m.parse_status(screen)
    assert r["subagents"] == 0
    assert r["state"] == "thinking", r["state"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} passed")
