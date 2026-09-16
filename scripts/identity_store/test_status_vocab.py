"""RED-first — canonical status vocabulary (Identity Layer v1 item (b))."""
import pytest

from scripts.identity_store import status_vocab as sv


def test_enum_is_exactly_the_four():
    assert sv.STATUS_ENUM == {"online", "parked", "provisional", "retired"}


def test_assert_valid_accepts_enum_and_rejects_legacy():
    for s in ("online", "parked", "provisional", "retired"):
        assert sv.assert_valid(s) == s
    for bad in ("quiescent", "stopped", "active", "ready", "spawning", "n/a", ""):
        with pytest.raises(sv.InvalidStatus):
            sv.assert_valid(bad)


def test_static_legacy_maps_to_parked_regardless_of_liveness():
    for s in ("quiescent", "stopped", "killed", "offline", "n/a", "retiring", "registered"):
        assert sv.resolve(s, is_live=True) == "parked"
        assert sv.resolve(s, is_live=False) == "parked"


def test_active_and_ready_are_liveness_gated():
    for s in ("active", "ready"):
        assert sv.resolve(s, is_live=True) == "online"
        assert sv.resolve(s, is_live=False) == "parked"


def test_online_parked_follow_liveness():
    assert sv.resolve("online", is_live=False) == "parked"
    assert sv.resolve("parked", is_live=True) == "online"
    assert sv.resolve("online", is_live=True) == "online"


def test_spawning_is_provisional_until_promoted_then_liveness():
    assert sv.resolve("spawning", is_live=False, gen_promoted=False) == "provisional"
    assert sv.resolve("spawning", is_live=True, gen_promoted=False) == "provisional"
    assert sv.resolve("spawning", is_live=True, gen_promoted=True) == "online"
    assert sv.resolve("spawning", is_live=False, gen_promoted=True) == "parked"


def test_provisional_and_retired_pass_through():
    assert sv.resolve("provisional", is_live=False) == "provisional"
    assert sv.resolve("retired", is_live=True) == "retired"


def test_unknown_label_fails_safe_to_parked():
    assert sv.resolve("banana", is_live=True) == "parked"
