"""Vendor picker display contract (gm msg_1935a92a; port of the live catalog): the vendor-state
response gains `vendors: [{id, title, subtitle, available, reason}]` with plain-English copy served
by the box, so a new vendor never needs an app build. `allowed` + `unavailable` stay EXACTLY as
before (old clients unaffected). One predicate decides selectability for both the rows and set(),
so a row can never say selectable and then be refused."""
import importlib.util
import pathlib

import pytest

from services.arturo import voice_vendor as vv


def _store(tmp_path, creds=lambda k: "x"):
    return vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=creds)


def _by_id(st):
    return {v["id"]: v for v in st["vendors"]}


def test_every_vendor_has_a_row_with_plain_copy(tmp_path):
    v = _by_id(_store(tmp_path).state())
    assert set(v) == set(vv.REQUIRED)
    for row in v.values():
        assert set(row) == {"id", "title", "subtitle", "available", "reason"}
        assert row["title"] and row["subtitle"] and row["available"] is True and row["reason"] == ""
    assert v["hume"]["title"] == "Hume" and v["elevenlabs"]["title"] == "ElevenLabs"


def test_a_vendor_without_its_keys_is_listed_unavailable_with_the_reason(tmp_path):
    no_hume = lambda k: "" if k.startswith("HUME_") else "x"
    v = _by_id(_store(tmp_path, no_hume).state())
    assert v["hume"]["available"] is False
    assert v["hume"]["reason"] == "missing HUME_API_KEY, HUME_CONFIG_ID, HUME_CONFIG_VERSION"
    assert v["elevenlabs"]["available"] is True


def test_old_fields_exactly_as_before(tmp_path):
    no_eleven = lambda k: "" if k == "ELEVENLABS_API_KEY" else "x"
    st = _store(tmp_path, no_eleven).state()
    assert st["allowed"] == ["hume"]
    assert st["unavailable"] == {"elevenlabs": "missing ELEVENLABS_API_KEY"}


def test_row_available_is_exactly_what_set_accepts(tmp_path):
    """ONE predicate. For every row, set() succeeds iff the row says available."""
    for creds in (lambda k: "x", lambda k: "", lambda k: "" if k.startswith("HUME_") else "x"):
        s = _store(tmp_path, creds)
        for row in s.state()["vendors"]:
            try:
                s.set(row["id"], by="t")
                ok = True
            except ValueError:
                ok = False
            assert ok == row["available"], row


def test_an_unavailable_vendor_is_refused_and_nothing_is_written(tmp_path):
    s = _store(tmp_path, lambda k: "")
    with pytest.raises(ValueError, match="missing ELEVENLABS_API_KEY"):
        s.set("elevenlabs", by="test")
    assert not (tmp_path / "v.json").exists(), "a refused set must not write the preference"


def test_route_serves_the_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_vcat", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(vv, "_default", _store(tmp_path))
    c = mod.app.test_client()
    j = c.get("/ptt/vendor", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    assert j["ok"] and set(_by_id(j)) == set(vv.REQUIRED)
    assert set(j["allowed"]) == set(vv.REQUIRED)
