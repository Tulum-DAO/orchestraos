import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_config as AC  # noqa: E402


def test_defaults_when_no_section():
    c = AC.load({})
    assert c.detector_interval_s == 60 and c.clear_after_ticks == 2
    assert c.legacy_completion_cards is True and c.detector_enabled is True
    assert c.failed_reasons == ("crashed", "usage_limit", "signed_out")


def test_section_overrides_and_bad_values_fall_back():
    c = AC.load({"attention": {"detector_interval_s": "x", "clear_after_ticks": 0}})
    assert not hasattr(c, "stuck_no_output_s")      # timeframes belong to the person's rules (§4a), not config
    assert c.detector_interval_s == 60           # not an int -> default
    assert c.clear_after_ticks == 1              # floor 1


def test_good_values_are_read_and_intervals_have_a_floor():
    c = AC.load({"attention": {"detector_interval_s": 3, "detector_enabled": False,
                               "legacy_completion_cards": False, "failed_reasons": ["crashed"]}})
    assert c.detector_interval_s == 10 and c.detector_enabled is False
    assert c.legacy_completion_cards is False and c.failed_reasons == ("crashed",)


def test_unknown_failed_reasons_are_dropped():
    assert AC.load({"attention": {"failed_reasons": ["crashed", "bogus"]}}).failed_reasons == ("crashed",)


def test_an_unreadable_config_is_the_defaults(monkeypatch):
    import notify_policy
    monkeypatch.setattr(notify_policy, "_config", lambda: 1 / 0)
    assert AC.load() == AC.load({})


def test_disarm_file_turns_the_detector_off(tmp_path):
    assert AC.detector_on(AC.load({}), data_dir=tmp_path) is True
    (tmp_path / "state" / "attention").mkdir(parents=True)
    (tmp_path / "state" / "attention" / "DISARM").write_text("")
    assert AC.detector_on(AC.load({}), data_dir=tmp_path) is False


def test_legacy_completion_cards_is_marked_temporary():
    """It exists only until the phone and watch read /attention (part 5); the doc says so."""
    assert "part 5" in (AC.__doc__ or "") and "legacy_completion_cards" in AC.__doc__
