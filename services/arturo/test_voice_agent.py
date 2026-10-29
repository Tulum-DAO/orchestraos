"""Each agent can have its own voice; with none it speaks with the install's voice."""
import json

import pytest

from services.arturo.voice_choice import VoiceChoiceStore


def _store(tmp_path):
    return VoiceChoiceStore(path=tmp_path / "voice-choice.json", log_path=tmp_path / "voice-choice.log")


def test_an_agent_with_no_voice_of_its_own_uses_the_installs(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-install")
    assert st.get("hume") == "voice-install"
    assert st.get("hume", agent="dev-demo") == "voice-install"
    assert st.state("hume", agent="dev-demo")["scope"] == "install"


def test_an_agents_own_voice_is_its_alone(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-install")
    st.set("hume", "voice-dev", agent="dev-demo")
    assert st.get("hume", agent="dev-demo") == "voice-dev"
    assert st.get("hume", agent="pm-demo") == "voice-install"
    assert st.get("hume") == "voice-install"                       # the install's voice is untouched
    s = st.state("hume", agent="dev-demo")
    assert s["scope"] == "agent" and s["agent"] == "dev-demo"


def test_picks_stay_per_engine_for_an_agent(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-dev", agent="dev-demo")
    st.set("openai", "alloy", agent="dev-demo")
    assert (st.get("hume", agent="dev-demo"), st.get("openai", agent="dev-demo")) == ("voice-dev", "alloy")


def test_clearing_returns_the_agent_to_the_installs_voice(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-install")
    st.set("hume", "voice-dev", agent="dev-demo")
    st.clear("hume", "dev-demo")
    assert st.get("hume", agent="dev-demo") == "voice-install"
    assert "agents" not in json.loads((tmp_path / "voice-choice.json").read_text()) or \
        "dev-demo" not in json.loads((tmp_path / "voice-choice.json").read_text())["agents"]
    with pytest.raises(ValueError):
        st.clear("hume", None)                                     # the install's voice is never "cleared"


def test_setting_the_installs_voice_keeps_every_agents_own(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-dev", agent="dev-demo")
    st.set("hume", "voice-install-2")
    assert st.get("hume", agent="dev-demo") == "voice-dev"


def test_a_malformed_agent_id_is_refused(tmp_path):
    st = _store(tmp_path)
    for bad in ("../x", "Dev Demo", "a" * 80):
        with pytest.raises(ValueError):
            st.set("hume", "voice-x", agent=bad)


def test_a_legacy_single_record_file_still_reads(tmp_path):
    (tmp_path / "voice-choice.json").write_text(json.dumps({"voice_id": "voice-old", "vendor": "hume"}))
    st = _store(tmp_path)
    assert st.get("hume") == "voice-old" and st.get("hume", agent="dev-demo") == "voice-old"


def test_every_write_is_audited_with_the_agent(tmp_path):
    st = _store(tmp_path)
    st.set("hume", "voice-dev", agent="dev-demo", by="settings", device="sample-device")
    line = json.loads((tmp_path / "voice-choice.log").read_text().splitlines()[-1])
    assert line["agent"] == "dev-demo" and line["voice_id"] == "voice-dev" and line["device"] == "sample-device"
