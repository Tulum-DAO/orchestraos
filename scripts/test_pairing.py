"""RED-first for `orchestra pair` + POST /pair/exchange.

Contract (devex-review msg_b1135ce2 via ios-watch-dev): the QR carries a SHORT-LIVED,
SINGLE-USE pairing code; the phone POSTs /pair/exchange {code} and receives
{base_url, token}; the code expires ON USE or after 10 minutes.

The security shape matters more than the convenience: this code is a bearer for the
operator's whole gateway. So it is single-use (a screenshot cannot be replayed), short-lived
(a leaked photo goes stale), and a wrong or spent code must be indistinguishable in the
response from one that never existed — never a hint about which half was wrong.
"""
import json
import time

import pytest

from scripts.pairing import PairingStore


@pytest.fixture()
def store(tmp_path):
    return PairingStore(tmp_path / "pairing", ttl_s=600)


def test_a_minted_code_can_be_exchanged_once(store):
    code = store.mint(base_url="https://box.example:8443", token="secret-token")
    got = store.redeem(code)
    assert got == {"base_url": "https://box.example:8443", "token": "secret-token"}


def test_the_same_code_cannot_be_used_twice(store):
    """A screenshot of the QR must not be replayable."""
    code = store.mint(base_url="u", token="t")
    assert store.redeem(code) is not None
    assert store.redeem(code) is None


def test_an_expired_code_is_refused(store, monkeypatch):
    code = store.mint(base_url="u", token="t")
    monkeypatch.setattr(store, "_now", lambda: time.time() + 601)
    assert store.redeem(code) is None


def test_a_code_that_never_existed_is_refused_the_same_way(store):
    """Indistinguishable from spent/expired: no oracle telling an attacker which half of a
    guess was right."""
    assert store.redeem("nope-this-was-never-minted") is None


def test_codes_are_unguessable_and_distinct(store):
    codes = {store.mint(base_url="u", token="t") for _ in range(50)}
    assert len(codes) == 50
    for c in codes:
        assert len(c) >= 12          # enough entropy that guessing inside 10 minutes is hopeless


def test_the_code_is_not_the_token(store):
    """The QR must not simply BE the bearer: that is the difference between a code that
    expires and a password in a photograph."""
    code = store.mint(base_url="u", token="the-real-bearer")
    assert "the-real-bearer" not in code


def test_expired_codes_are_swept_so_the_directory_does_not_grow(store, monkeypatch):
    for _ in range(3):
        store.mint(base_url="u", token="t")
    monkeypatch.setattr(store, "_now", lambda: time.time() + 601)
    store.sweep()
    assert store.count() == 0


def test_payload_round_trips_through_the_qr_string(store):
    """What the QR encodes is what the phone posts back — one string, no side channel."""
    code = store.mint(base_url="https://box:8443", token="t")
    payload = store.qr_payload(code, base_url="https://box:8443")
    parsed = json.loads(payload)
    assert parsed["code"] == code
    assert parsed["base_url"] == "https://box:8443"
    assert "token" not in parsed          # the token is EXCHANGED for, never carried in the QR
