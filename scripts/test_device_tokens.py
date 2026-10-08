"""Tests for per-device gateway bearers.

Every token here is synthetic. The point of the module under test is that a real token is
never stored anywhere, so these tests assert that property directly rather than trusting it.
"""
import json
import os
import stat

import pytest

from scripts.device_tokens import (ALL_SCOPES, DeviceStore, LEGACY_LABEL, ScopeError,
                                  normalize_scopes, scopes_allow)


def _store(tmp_path):
    return DeviceStore(tmp_path / "devices")


# ---------------------------------------------------------------- no silent default

def test_minting_without_scopes_is_refused():
    """gm ruling 2026-10-05: no silent default, so nobody inherits `approve` by accident."""
    with pytest.raises(ScopeError):
        normalize_scopes([])
    with pytest.raises(ScopeError):
        normalize_scopes("")
    with pytest.raises(ScopeError):
        normalize_scopes(None)


def test_an_unknown_verb_is_refused_loudly(tmp_path):
    """A typo that silently granted nothing yields a device that mysteriously 403s; one
    that silently granted everything is the bug this module removes."""
    with pytest.raises(ScopeError):
        normalize_scopes(["read", "destroy"])
    with pytest.raises(ScopeError):
        _store(tmp_path).mint("x", ["reed"])


def test_scopes_accept_a_comma_or_space_string(tmp_path):
    assert normalize_scopes("read,approve") == ("read", "approve")
    assert normalize_scopes("read approve") == ("read", "approve")


def test_duplicate_scopes_collapse():
    assert normalize_scopes(["read", "read", "approve"]) == ("read", "approve")


# ---------------------------------------------------------------- the token is never stored

def test_the_plaintext_token_is_never_written_to_disk(tmp_path):
    st = _store(tmp_path)
    dev_id, token = st.mint("quest", ["read", "approve", "message"])
    blob = "".join(p.read_text() for p in (tmp_path / "devices").glob("*.json"))
    assert token not in blob, "the device file must hold a hash, never the token"
    assert "token_sha256" in blob


def test_the_device_file_is_0600(tmp_path):
    st = _store(tmp_path)
    dev_id, _ = st.mint("quest", ["read"])
    mode = stat.S_IMODE(os.stat(tmp_path / "devices" / f"{dev_id}.json").st_mode)
    assert mode == 0o600, oct(mode)


def test_a_listing_does_not_carry_even_the_hash(tmp_path):
    st = _store(tmp_path)
    st.mint("quest", ["read"])
    rows = st.list()
    assert rows and all("token_sha256" not in r for r in rows)
    assert rows[0]["label"] == "quest" and rows[0]["scopes"] == ["read"]


# ---------------------------------------------------------------- resolve

def test_a_minted_token_resolves_to_its_device(tmp_path):
    st = _store(tmp_path)
    dev_id, token = st.mint("quest", ["read", "approve"])
    rec = st.resolve(token)
    assert rec and rec["id"] == dev_id and rec["scopes"] == ["read", "approve"]


def test_an_unknown_token_resolves_to_nothing(tmp_path):
    st = _store(tmp_path)
    st.mint("quest", ["read"])
    assert st.resolve("not-a-real-token") is None
    assert st.resolve("") is None


def test_two_devices_do_not_collide_and_each_keeps_its_own_scopes(tmp_path):
    st = _store(tmp_path)
    _, t_read = st.mint("watch", ["read"])
    _, t_inject = st.mint("laptop", ["inject"])
    assert st.resolve(t_read)["scopes"] == ["read"]
    assert st.resolve(t_inject)["scopes"] == ["inject"]


# ---------------------------------------------------------------- revocation

def test_a_revoked_device_stops_resolving(tmp_path):
    st = _store(tmp_path)
    dev_id, token = st.mint("quest", ["read", "approve"])
    assert st.resolve(token) is not None
    assert st.revoke(dev_id) is True
    assert st.resolve(token) is None, "a revoked token must not authenticate"


def test_revoking_keeps_the_record_so_past_answers_stay_attributable(tmp_path):
    st = _store(tmp_path)
    dev_id, _ = st.mint("quest", ["read"])
    st.revoke(dev_id)
    rows = st.list()
    assert len(rows) == 1 and rows[0]["id"] == dev_id and rows[0]["revoked_at"]


def test_revoking_twice_reports_that_there_was_nothing_to_do(tmp_path):
    st = _store(tmp_path)
    dev_id, _ = st.mint("quest", ["read"])
    assert st.revoke(dev_id) is True
    assert st.revoke(dev_id) is False
    assert st.revoke("no-such-device") is False


# ---------------------------------------------------------------- scope checks

def test_a_public_route_needs_no_scope():
    assert scopes_allow(("read",), None) is True
    assert scopes_allow((), None) is True


def test_a_verb_must_be_named_explicitly_no_implication_between_verbs():
    """Holding `inject` does NOT imply `read`. An implication graph is a second policy
    nobody reviews."""
    assert scopes_allow(("inject",), "read") is False
    assert scopes_allow(("read",), "inject") is False
    assert scopes_allow(("read", "approve"), "approve") is True


def test_the_legacy_fleet_token_scope_allows_everything():
    assert ALL_SCOPES == ("*",)
    for verb in ("read", "approve", "message", "inject", "voice", "admin", "usage"):
        assert scopes_allow(ALL_SCOPES, verb) is True


def test_an_empty_scope_set_allows_no_verb():
    assert scopes_allow((), "read") is False
    assert scopes_allow(None, "read") is False


def test_star_in_a_mint_is_honoured_for_the_legacy_credential():
    assert normalize_scopes(["*"]) == ALL_SCOPES
    assert LEGACY_LABEL == "legacy-fleet-token"


# ---------------------------------------------------------------- robustness

def test_a_half_written_device_file_is_not_a_usable_credential(tmp_path):
    st = _store(tmp_path)
    _, token = st.mint("quest", ["read"])
    (tmp_path / "devices" / "corrupt.json").write_text("{not json")
    assert st.resolve(token) is not None       # the good one still works
    assert len(st.list()) == 1                 # the corrupt one is simply not a device


def test_resolve_on_an_absent_directory_is_not_an_error(tmp_path):
    assert DeviceStore(tmp_path / "nope").resolve("x") is None
    assert DeviceStore(tmp_path / "nope").list() == []


def test_touch_records_last_seen_without_failing_a_request(tmp_path):
    st = _store(tmp_path)
    dev_id, token = st.mint("quest", ["read"])
    assert st.resolve(token)["last_seen_at"] is None
    st.touch(dev_id)
    assert st.resolve(token)["last_seen_at"] is not None
    st.touch("no-such-device")                 # must not raise


def test_usage_is_a_known_verb_that_a_phone_mint_can_carry(tmp_path):
    """A phone minted before the usage-read route exists must be able to hold `usage` already;
    otherwise adding it later means re-pairing the phone and every watch that inherits it."""
    store = DeviceStore(tmp_path)
    device_id, token = store.mint("phone", "read,approve,message,ptt,usage")
    rec = store.resolve(token)
    assert rec is not None and "usage" in rec["scopes"]
    assert scopes_allow(tuple(rec["scopes"]), "usage") is True
    assert scopes_allow(("read",), "usage") is False
