"""Runtime voice-vendor preference: file-backed, read per call, validated against creds.
RED-first (Hume Task 3, frozen artifact d3cbafa9). +S9 corrupt-file default."""
import json
from services.arturo import voice_vendor as vv


def test_default_is_elevenlabs_when_no_file(tmp_path):
    s = vv.VendorStore(path=tmp_path / "voice-vendor.json", log_path=tmp_path / "voice-vendor.log",
                       creds=lambda k: "x")
    assert s.get() == "elevenlabs"


def test_set_persists_and_logs(tmp_path):
    s = vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=lambda k: "x")
    st = s.set("hume", by="operator", source="settings")
    assert st["vendor"] == "hume" and st["changed_by"] == "operator"
    assert json.loads((tmp_path / "v.json").read_text())["vendor"] == "hume"
    assert "hume" in (tmp_path / "v.log").read_text()
    assert vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=lambda k: "x").get() == "hume"


def test_rejects_unknown_vendor(tmp_path):
    s = vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=lambda k: "x")
    try:
        s.set("gemini", by="t")
        assert False, "should reject"
    except ValueError as e:
        assert "unknown" in str(e)


def test_allowed_requires_credentials(tmp_path):
    have = {"ELEVENLABS_API_KEY": "e"}
    s = vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log", creds=lambda k: have.get(k, ""))
    st = s.state()
    assert st["allowed"] == ["elevenlabs"]
    assert st["unavailable"]["hume"].startswith("missing")
    try:
        s.set("hume", by="t")
        assert False
    except ValueError as e:
        assert "unavailable" in str(e)


def test_corrupt_file_defaults_to_elevenlabs(tmp_path):
    # S9: a CORRUPT (not just missing) file must default to elevenlabs, no exception.
    p = tmp_path / "v.json"
    p.write_text("{not json")
    s = vv.VendorStore(path=p, log_path=tmp_path / "v.log", creds=lambda k: "x")
    assert s.get() == "elevenlabs"
