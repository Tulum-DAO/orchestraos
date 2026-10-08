"""onboarding marker + directive — the one server-side place a step's instruction lives."""
from services.arturo import onboarding as O


def test_the_onboarding_marker_is_stripped_and_carries_the_playbook():
    step, text = O.split_marker("[Onboarding: step=onboarding]\nhi my name is Shaw nice to meet you")
    assert step == "onboarding"
    assert text == "hi my name is Shaw nice to meet you"
    d = O.directive(step)
    assert "set_operator_fact" in d and "Never invent" in d     # the name rules ride in the playbook


def test_the_old_scripted_steps_carry_nothing_now():
    # the operator, 2026-10-08: no hardcoded questions; one playbook drives the first run
    for old in ("name", "team", "team_open", "devices", "hierarchy"):
        assert O.directive(old) == "", old


def test_ordinary_turns_are_untouched():
    assert O.split_marker("what can you do?") == (None, "what can you do?")
    assert O.directive(None) == ""


def test_unknown_step_is_reported_but_has_no_directive():
    step, text = O.split_marker("[Onboarding: step=shoe_size]\n11")
    assert step == "shoe_size" and text == "11"
    assert O.directive(step) == ""
