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


# --- one bare token to paste (Shaw 2026-10-08: "a bare code that users can paste directly into
# the pairing box"). The phone still needs WHERE and the CODE, so the token carries both: it is
# the compact JSON above, base64url'd behind a version prefix. Clients strip, decode, and reuse
# their legacy JSON branch.

from scripts.pairing import PAIR_TOKEN_PREFIX, pair_token, parse_pair_input, valid_base_url


def test_the_token_is_one_paste_safe_word(store):
    code = store.mint(base_url="https://box.tail1234.ts.net:8443", token="t")
    tok = pair_token(code, base_url="https://box.tail1234.ts.net:8443")
    assert tok.startswith(PAIR_TOKEN_PREFIX) and PAIR_TOKEN_PREFIX == "orc1_"
    body = tok[len(PAIR_TOKEN_PREFIX):]
    # nothing a keyboard autocorrects or a paste splits: urlsafe alphabet, no padding, no spaces
    assert body and all(ch.isalnum() or ch in "-_" for ch in body)
    assert "=" not in tok and "{" not in tok and "token" not in tok


def test_the_token_round_trips_to_the_qr_json(store):
    code = store.mint(base_url="https://box:8443", token="t")
    got = parse_pair_input(pair_token(code, base_url="https://box:8443"))
    assert got == {"code": code, "base_url": "https://box:8443"}


@pytest.mark.parametrize("wrap", [lambda s: s, lambda s: "  " + s + "\n", lambda s: s + "\r\n"])
def test_a_pasted_token_survives_surrounding_whitespace(store, wrap):
    tok = pair_token("abcDEF-123_x", base_url="https://box:8443")
    assert parse_pair_input(wrap(tok)) == {"code": "abcDEF-123_x", "base_url": "https://box:8443"}


def test_the_legacy_json_line_still_parses(store):
    line = store.qr_payload("abc", base_url="https://box:8443")
    assert parse_pair_input(line) == {"code": "abc", "base_url": "https://box:8443"}


def test_a_bare_legacy_code_parses_with_no_address():
    assert parse_pair_input("abcDEF-123_x") == {"code": "abcDEF-123_x", "base_url": None}


@pytest.mark.parametrize("bad", [
    "orc1_" + "e30",                              # {} : no code
    "orc1_" + "eyJjb2RlIjoiYyIsImJhc2VfdXJsIjoiaHR0cDovL2JveCJ9",   # http://, not https  # pragma: allowlist secret
    "orc1_" + "WyJhIl0",                          # ["a"] : not an object
    "orc1_abcde",                                 # length % 4 == 1 : not base64
])
def test_an_undecodable_token_falls_through_to_a_bare_code(bad):
    """A broken orc1_ never errors; it is tried as a bare code (which the gateway then
    refuses like any wrong code). A token_urlsafe code may itself begin with orc1_."""
    assert parse_pair_input(bad) == {"code": bad, "base_url": None}


@pytest.mark.parametrize("junk", ["abc def", "orc1_abc!def", "c\u00f3digo",
                                  '{"code":"c","base_url":"http://box"}', "orc1_" + "A" * 5000])
def test_what_cannot_be_a_code_is_none(junk):
    assert parse_pair_input(junk) is None


def test_a_token_wrapped_by_the_terminal_still_parses():
    tok = pair_token("abcDEF-123_x", base_url="https://box.tail1234.ts.net:8443")
    wrapped = tok[:40] + "\n" + tok[40:80] + "\r\n  " + tok[80:]
    assert parse_pair_input(wrapped) == {"code": "abcDEF-123_x",
                                         "base_url": "https://box.tail1234.ts.net:8443"}


@pytest.mark.parametrize("url,ok", [
    ("https://box:8443", True), ("https://box.tail1234.ts.net", True), ("HTTPS://box", True),
    ("http://box:8443", False), ("box.tail1234.ts.net:8445", False), ("https://", False),
    ("https://:443", False), ("https://box:99999", False), ("", False),
])
def test_valid_base_url(url, ok):
    assert valid_base_url(url) is ok


def test_empty_input_is_none():
    assert parse_pair_input("   ") is None


def test_redeem_accepts_the_whole_token_or_json_line(store):
    """An app released before the token existed sends whatever was pasted as the code. The
    gateway unwraps it, so the new CLI still pairs an old app whose address was typed in."""
    c1 = store.mint(base_url="https://box:8443", token="t1")
    assert store.redeem(pair_token(c1, "https://box:8443"))["token"] == "t1"
    c2 = store.mint(base_url="https://box:8443", token="t2")
    assert store.redeem(store.qr_payload(c2, "https://box:8443"))["token"] == "t2"
    assert store.redeem(pair_token(c1, "https://box:8443")) is None    # still single-use


def test_the_client_fixture_is_generated_from_this_module():
    """Mac and iOS tests read contract/pair-token-cases.json. It must be byte-for-byte what
    scripts/gen_pair_token_cases.py writes TODAY, or the clients are tested against a guess."""
    from pathlib import Path
    from scripts.gen_pair_token_cases import OUT, render
    assert OUT.read_text() == render(), "run: python3 scripts/gen_pair_token_cases.py"
    fx = json.loads(OUT.read_text())
    produced = [c for c in fx["cases"] if c.get("produced_by") == "pair_token"]
    assert produced and all(pair_token(c["code"], c["base_url"]) == c["input"] for c in produced)
