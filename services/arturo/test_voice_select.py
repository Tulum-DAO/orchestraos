"""RED-first: voice SELECTION for every vendor (the operator via ios-watch-dev msg_d484bf1f; contract v2 msg_a7cf1484,
confirmed msg_db125483). The operator (macos-dev msg_2a772f64): "one change on any surface regarding the voice ... applies
to all surfaces" -> ONE global record that remembers one pick PER ENGINE (per-vendor, never per-device).
Rows are server-rendered display copy (title <= 20 chars for the Quest) and strictly typed for lenient clients."""
import importlib.util
import json
import pathlib

import pytest

from services.arturo import openai_live as ol
from services.arturo import voice_choice as vc

ROW_KEYS = {"id", "name", "title", "subtitle", "recommended", "order", "sample_url", "group", "provider"}
HUME_RAW = [{"id": f"h{i}", "name": n, "provider": "hume_library"} for i, n in enumerate(
    ["Serene Assistant", "Warm Female Assistant Voice", "Warm American Female", "Comforting Male Conversationalist",
     "Soft Male Conversationalist", "Deep Male Conversational Voice", "Conversational English Guy",
     "Demure Conversationalist", "Casual Podcast Host", "Ito", "Extraordinarily Theatrical Narrator Of Tales"])]


@pytest.fixture
def store(tmp_path):
    return vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log")


# --- one global record, one pick per engine ---
def test_a_legacy_single_hume_record_is_read_as_the_hume_pick(store):
    store.path.write_text(json.dumps({"vendor": "hume", "voice_id": "v-legacy-pick", "changed_at": "2026-09-14T05:00:35Z",
                                      "changed_by": "settings-ios", "source": "settings"}))
    assert store.get("hume") == "v-legacy-pick", "the operator's existing pick must survive the migration"
    assert store.get("openai") is None
    assert store.state("hume")["changed_by"] == "settings-ios"


def test_each_engine_keeps_its_own_pick(store):
    store.set("hume", "h5")
    store.set("openai", "cedar")
    assert store.get("hume") == "h5" and store.get("openai") == "cedar"
    store.set("openai", "marin")
    assert store.get("hume") == "h5", "switching the other engine's voice must not reset this one"
    on_disk = json.loads(store.path.read_text())
    assert set(on_disk) == {"hume", "openai"}


def test_the_store_refuses_an_unknown_vendor(store):
    with pytest.raises(ValueError):
        store.set("elevenlabs", "v1")


# --- server-rendered rows ---
def test_rows_are_strictly_typed_with_short_titles():
    for vendor, raw in (("hume", HUME_RAW), ("openai", None)):
        rows = vc.voice_rows(vendor, raw=raw)
        assert rows
        for r in rows:
            assert set(r) == ROW_KEYS, r
            assert isinstance(r["id"], str) and r["id"]
            assert isinstance(r["name"], str) and r["name"]
            assert isinstance(r["title"], str) and 0 < len(r["title"]) <= 20, r["title"]
            assert r["subtitle"] is None or (isinstance(r["subtitle"], str) and r["subtitle"])
            assert type(r["recommended"]) is bool
            assert type(r["order"]) is int
            assert r["sample_url"] is None
            assert r["group"] is None or (isinstance(r["group"], str) and r["group"])


def test_hume_curation_is_the_server_list_in_order():
    rows = vc.voice_rows("hume", raw=HUME_RAW)
    rec = [r for r in rows if r["recommended"]]
    assert [r["name"] for r in rec] == [v["name"] for v in HUME_RAW[:10]]
    assert [r["order"] for r in rec] == sorted(r["order"] for r in rec)
    deep = next(r for r in rows if r["name"] == "Deep Male Conversational Voice")
    assert deep["title"] == "Deep Male" and deep["subtitle"] == "Deep Male Conversational Voice"
    ito = next(r for r in rows if r["name"] == "Ito")
    assert ito["title"] == "Ito" and ito["subtitle"] is None
    other = next(r for r in rows if r["name"].startswith("Extraordinarily"))
    assert other["recommended"] is False and len(other["title"]) <= 20


def test_a_row_the_server_cannot_fill_is_dropped_not_sent_half_filled():
    raw = HUME_RAW[:2] + [{"id": None, "name": "No Id"}, {"id": "x", "name": None}, {"id": "y", "name": ""}]
    rows = vc.voice_rows("hume", raw=raw)
    assert [r["id"] for r in rows] == ["h0", "h1"]


def test_openai_rows_are_the_ten_probed_voices_all_recommended_marin_first():
    rows = vc.voice_rows("openai")
    assert {r["id"] for r in rows} == {"marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage",
                                       "shimmer", "verse"}
    assert rows[0]["id"] == "marin" and all(r["recommended"] for r in rows)
    assert rows[0]["title"] == "Marin"


# --- the PUT cannot be fooled ---
def test_set_voice_validates_against_the_named_vendor(monkeypatch, store):
    monkeypatch.setattr(vc, "_default", store)
    monkeypatch.setattr(vc, "cached_hume_voices", lambda: HUME_RAW)
    with pytest.raises(ValueError, match="not a hume voice"):
        vc.set_voice("hume", "marin")
    with pytest.raises(ValueError, match="not an openai voice"):
        vc.set_voice("openai", "h5")
    assert vc.set_voice("openai", "cedar")["voice_id"] == "cedar"
    assert vc.set_voice("hume", "h5")["voice_id"] == "h5"


def test_a_hume_pick_fails_closed_when_hume_has_never_answered(monkeypatch, store):
    monkeypatch.setattr(vc, "_default", store)

    def down():
        raise RuntimeError("hume unreachable")
    monkeypatch.setattr(vc, "cached_hume_voices", down)
    with pytest.raises(ValueError, match="unavailable"):
        vc.set_voice("hume", "h5")
    assert store.get("hume") is None


# --- the pick reaches GPT-Live at session start (the voice is fixed for the session) ---
def test_openai_session_start_carries_the_voice():
    assert ol.session_start(voice="cedar")["session"]["audio"]["output"] == {"voice": "cedar"}
    assert ol.session_start()["session"]["audio"]["output"] == {"voice": "marin"}


def test_openai_factory_uses_the_pick_else_marin(monkeypatch):
    sent = []

    class FakeSock:
        def __init__(self, url, headers):
            pass

        def send(self, p):
            sent.append(json.loads(p))
    monkeypatch.setattr(ol, "AsyncWsSocket", FakeSock)
    monkeypatch.setattr(ol, "api_key", lambda: "k")
    monkeypatch.setattr(vc, "get_voice", lambda vendor: "ash" if vendor == "openai" else "h5")
    ol.openai_socket_factory("c1")
    assert sent[-1]["session"]["audio"]["output"]["voice"] == "ash"
    monkeypatch.setattr(vc, "get_voice", lambda vendor: None)
    ol.openai_socket_factory("c2")
    assert sent[-1]["session"]["audio"]["output"]["voice"] == "marin"


# --- routes ---
def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_voice_select", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_routes_serve_every_vendor_and_default_to_the_live_one(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_vendor as vv
    monkeypatch.setattr(vc, "_default", vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log"))
    monkeypatch.setattr(vc, "cached_hume_voices", lambda: HUME_RAW)
    monkeypatch.setattr(vv, "get_vendor", lambda: "openai")
    c = mod.app.test_client()
    loop = {"REMOTE_ADDR": "127.0.0.1"}
    try:
        j = c.get("/ptt/voices", environ_base=loop).get_json()
        assert j["ok"] and j["vendor"] == "openai" and j["current"] == "marin" and j["error"] is None
        assert len(j["voices"]) == 10
        j = c.get("/ptt/voices?vendor=hume", environ_base=loop).get_json()
        assert j["vendor"] == "hume" and j["current"] is None and len(j["voices"]) == len(HUME_RAW)
        assert c.get("/ptt/voices?vendor=elevenlabs", environ_base=loop).status_code == 400
        r = c.put("/ptt/voice", json={"vendor": "hume", "voice_id": "marin", "by": "settings-ios"}, environ_base=loop)
        assert r.status_code == 400 and "not a hume voice" in r.get_json()["error"]
        r = c.put("/ptt/voice", json={"vendor": "openai", "voice_id": "cedar", "by": "quest"}, environ_base=loop)
        assert r.status_code == 200 and r.get_json()["voice_id"] == "cedar"
        assert c.get("/ptt/voices", environ_base=loop).get_json()["current"] == "cedar"
        g = c.get("/ptt/voice", environ_base=loop).get_json()
        assert g["vendor"] == "openai" and g["voice_id"] == "cedar" and g["title"] == "Cedar"
        assert c.get("/ptt/voice?vendor=hume", environ_base=loop).get_json()["voice_id"] is None
    finally:
        mod._STREAM_RELAY.shutdown()


def test_a_cloned_voice_keeps_its_own_server_named_group_first():
    """ios-watch-dev msg_0aa40402 option (c): today's screen splits YOUR VOICES / HUME VOICES on `provider`. A voice
    the operator made themselves is categorically theirs; the SERVER names the heading so no client hardcodes Hume's vocabulary."""
    raw = [{"id": "c1", "name": "Orla", "provider": "custom"}] + HUME_RAW
    rows = vc.voice_rows("hume", raw=raw)
    assert rows[0]["id"] == "c1" and rows[0]["group"] == "Your voices" and rows[0]["recommended"] is True
    assert {r["group"] for r in rows[1:]} == {"Hume voices"}
    assert all(r["group"] is None for r in vc.voice_rows("openai"))


def test_the_shipped_client_still_decodes_every_row():
    """The App Store build decodes Hume rows as {id, name, provider}. Until the v2 client ships, every row keeps
    those v1 fields with their v1 values, or the shipped Hume picker could decode to an empty list."""
    raw = [{"id": "c1", "name": "Orla", "provider": "custom"}] + HUME_RAW
    for r in vc.voice_rows("hume", raw=raw):
        assert r["provider"] in ("custom", "hume_library")
    assert vc.voice_rows("hume", raw=raw)[0]["provider"] == "custom"
    assert {r["provider"] for r in vc.voice_rows("openai")} == {"openai"}
