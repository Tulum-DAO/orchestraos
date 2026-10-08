"""RED-first for `orchestra pair` — the terminal half of onboarding."""
import json

import pytest

from orchestra_cli.pair_cmd import build_pair_output, qr_text_or_none


def test_output_carries_the_raw_string_beneath_whatever_is_drawn():
    """The contract: a QR plus THE RAW STRING beneath it. The raw string is what makes this
    work when the QR cannot be drawn, or photographed, or scanned."""
    payload = json.dumps({"code": "abc123", "base_url": "https://box:8443"})
    out = build_pair_output(payload)
    assert payload in out


def test_output_warns_in_PLAIN_WORDS_that_this_is_a_password():
    """Operator-facing text, not jargon: someone screensharing a terminal must understand
    what they are showing before they show it."""
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}))
    low = out.lower()
    assert "password" in low
    assert "screenshare" in low or "screen share" in low or "share your screen" in low


def test_output_says_how_long_it_lasts():
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}), ttl_s=600)
    assert "10 minutes" in out or "10 min" in out


def test_qr_is_absent_rather_than_faked_when_no_encoder_exists():
    """If nothing on the box can draw a QR we print no QR — we do NOT draw ASCII art that
    is not a scannable code. A fake QR is worse than none: it fails at the moment someone
    points a phone at it, in front of a room."""
    assert qr_text_or_none("payload", encoder=None) is None


def test_qr_is_drawn_when_an_encoder_IS_available():
    class FakeEncoder:
        def render(self, payload):
            return "##QR##"
    assert qr_text_or_none("payload", encoder=FakeEncoder()) == "##QR##"


def test_a_missing_qr_still_produces_usable_output():
    out = build_pair_output(json.dumps({"code": "c", "base_url": "u"}), qr=None)
    assert "c" in out and "password" in out.lower()
    # and it tells the operator what to do instead of showing a QR
    assert "type" in out.lower() or "paste" in out.lower() or "enter" in out.lower()


# --- pairing hands over a SCOPED device token, never the fleet bearer -------------------

class _Args:
    def __init__(self, **kw):
        self.base_url = kw.pop("base_url", "https://box:8443")
        self.scopes = kw.pop("scopes", None)
        self.label = kw.pop("label", None)
        self.revoke = kw.pop("revoke", None)
        for k, v in kw.items():
            setattr(self, k, v)


def _lines(monkeypatch, tmp_path, args, fn=None):
    from orchestra_cli.pair_cmd import run_devices, run_pair
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    said = []
    rc = (fn or run_pair)(args, out=said.append) if fn else run_pair(args, out=said.append,
                                                                    clear_after_s=0)
    return rc, "\n".join(said)


def test_pairing_without_scopes_is_refused_and_explains_each_verb(monkeypatch, tmp_path):
    """gm ruling: no silent default. Nobody inherits `approve` by accident."""
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes=None))
    assert rc == 2
    assert "Refusing to pair without --scopes" in said
    for verb in ("read", "approve", "message", "inject", "voice", "admin"):
        assert verb in said
    assert "ANSWER approvals" in said, "the operator must be told what `approve` really means"


def test_pairing_with_a_bad_verb_is_refused(monkeypatch, tmp_path):
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read,destroy"))
    assert rc == 2 and "not a usable scope list" in said.lower()


def test_pairing_mints_a_device_and_the_code_does_not_carry_the_fleet_token(monkeypatch, tmp_path):
    """The QR/code payload must carry a CODE only — and the token it is redeemed for must be
    the device's, not the gateway's own bearer."""
    import json as _json
    from pathlib import Path

    fleet = tmp_path / "watch-gateway-token"
    fleet.write_text("THE-FLEET-BEARER")        # pragma: allowlist secret
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(fleet))

    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read,approve,message",
                                                  label="quest-headset"))
    assert rc == 0, said
    assert "THE-FLEET-BEARER" not in said
    assert "Minted device" in said and "quest-headset" in said
    assert "read, approve, message" in said
    assert "orchestra devices --revoke" in said, "the operator is told how to undo it"

    devs = list((tmp_path / "state" / "devices").glob("*.json"))
    assert len(devs) == 1
    rec = _json.loads(devs[0].read_text())
    assert rec["scopes"] == ["read", "approve", "message"]
    assert "token_sha256" in rec and "token" not in rec


def test_devices_listing_shows_scopes_and_names_the_unrevocable_fleet_bearer(monkeypatch, tmp_path):
    from orchestra_cli.pair_cmd import run_devices
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _lines(monkeypatch, tmp_path, _Args(scopes="read", label="watch"))
    said = []
    rc = run_devices(_Args(), out=said.append)
    body = "\n".join(said)
    assert rc == 0
    assert "watch" in body and "read" in body and "active" in body
    assert "legacy-fleet-token" in body and "cannot be revoked here" in body


def test_devices_revoke_works_and_says_so(monkeypatch, tmp_path):
    import json as _json
    from orchestra_cli.pair_cmd import run_devices
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    _lines(monkeypatch, tmp_path, _Args(scopes="read", label="watch"))
    dev_id = _json.loads(next((tmp_path / "state" / "devices").glob("*.json")).read_text())["id"]

    said = []
    assert run_devices(_Args(revoke=dev_id), out=said.append) == 0
    assert "Revoked" in "\n".join(said)

    said2 = []
    assert run_devices(_Args(revoke=dev_id), out=said2.append) == 2      # already revoked
    said3 = []
    run_devices(_Args(), out=said3.append)
    assert "REVOKED" in "\n".join(said3)


def test_revoking_something_that_is_not_a_device_is_refused(monkeypatch, tmp_path):
    from orchestra_cli.pair_cmd import run_devices
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    said = []
    assert run_devices(_Args(revoke="no-such-id"), out=said.append) == 2
    assert "Nothing to revoke" in "\n".join(said)


def test_pair_prints_the_one_token_not_the_json_line(monkeypatch, tmp_path):
    """Shaw 2026-10-08: one bare code to paste. The screen shows the orc1_ token on a line by
    itself, and the JSON line no longer appears."""
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read", label="phone"))
    assert rc == 0, said
    lines = said.splitlines()
    toks = [l for l in lines if l.startswith("orc1_")]
    assert len(toks) == 1 and " " not in toks[0], said
    assert '"code"' not in said
    from scripts.pairing import parse_pair_input
    got = parse_pair_input(toks[0])
    assert got["base_url"] and got["code"]


@pytest.mark.parametrize("url", ["box.tail1234.ts.net:8445", "http://box:8445", "https://"])
def test_pair_refuses_an_address_the_app_would_reject(monkeypatch, tmp_path, url):
    """A token whose address is not https-with-a-host parses as a bare code in every client,
    and its inner code is never shown. So refuse to mint it at all."""
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read", base_url=url))
    assert rc == 2 and "orc1_" not in said and "--base-url https://" in said
    assert not list((tmp_path / "state" / "devices").glob("*.json")), "no device minted"


# --- the address: one resolver with Arturo (scripts/public_url.py) -------------------------------

def test_pair_with_no_address_uses_what_tailscale_serves(monkeypatch, tmp_path):
    from scripts import public_url
    monkeypatch.delenv("ORCHESTRA_PUBLIC_URL", raising=False)
    monkeypatch.setattr(public_url, "detect", lambda port, run=None: ["https://box.tn.ts.net:8445"])
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read", base_url=None, label="phone"))
    assert rc == 0
    from scripts.pairing import parse_pair_input
    tok = next(w for w in said.split() if w.startswith("orc1_"))
    assert parse_pair_input(tok)["base_url"] == "https://box.tn.ts.net:8445"


def test_pair_with_no_address_anywhere_says_what_to_run(monkeypatch, tmp_path):
    from scripts import public_url
    monkeypatch.delenv("ORCHESTRA_PUBLIC_URL", raising=False)
    monkeypatch.setattr(public_url, "detect", lambda port, run=None: [])
    rc, said = _lines(monkeypatch, tmp_path, _Args(scopes="read", base_url=None))
    assert rc == 2 and "tailscale serve" in said and "--base-url https://" in said and "orc1_" not in said
    assert not list((tmp_path / "state" / "devices").glob("*.json")), "no device minted"
