"""A rename reaches what the assistant is told about itself, on the next turn, with no restart: the main
instructions, the live-voice session, the helpers and onboarding, transcript labels. The "[Arturo Voice]:" pane
tag and "the Arturo tab" (the phone app's own label) stay as written."""
import importlib.util
import pathlib

import pytest

from services.arturo import assistant_name as an


@pytest.fixture
def store(monkeypatch, tmp_path):
    st = an.AssistantNameStore(path=tmp_path / "n.json", log_path=tmp_path / "n.log")
    monkeypatch.setattr(an, "_default", st)
    return st


@pytest.fixture
def mod(store, monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    spec = importlib.util.spec_from_file_location("arturo_proxy_names", pathlib.Path("services/arturo/arturo-proxy.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.brain = m._brain.RuntimeBrain("claude", "claude", model="", runner=lambda s, t: "")
    return m


def test_named_keeps_the_pane_tag_and_the_app_tab(store):
    text = "You are Arturo. ARTURO: hi. [Arturo Voice]: relay. In the app, the Arturo tab. Arturos and arturo.json."
    assert an.named(text) == text                       # the default changes nothing
    store.set("Example")
    assert an.named(text) == ("You are Example. EXAMPLE: hi. [Arturo Voice]: relay. In the app, the Arturo tab. "
                              "Arturos and arturo.json.")


def test_the_main_instructions_say_the_new_name_on_the_next_turn(mod, store):
    assert "Your name is Arturo; if asked who you are, you are Arturo." in mod.build_context(calling_channel="text")
    store.set("Example")
    ctx = mod.build_context(calling_channel="text")
    assert "You are EXAMPLE" in ctx and "Your name is Example; if asked who you are, you are Example." in ctx
    assert "Your name is Arturo" not in ctx and "(the Example home / pill)" in ctx


def test_call_notes_and_refusals_use_the_name(mod, store):
    store.set("Example")
    assert "the Example chat on the dashboard" in mod._non_fleet_prompt("x")
    assert "the Example chat on the dashboard" in mod._browser_voice_prompt("x")
    assert mod._promise_or_refusal("NOT RUN", "ok") == "I can't do that from a voice call. Ask me in the Example chat on your dashboard and I'll do it there."
    assert "Example, their assistant" in mod._named(mod._SUMMARY_SYSTEM)
    assert "you are Example" in mod._named(mod._DEEP_DIRECTIVE)


def test_live_voice_onboarding_and_transcripts_use_the_name(store):
    from services.arturo import endcall, onboarding, openai_live
    store.set("Example")
    assert openai_live.session_start()["session"]["instructions"].startswith("You are Example, the operator's voice assistant.")
    text = onboarding.playbook({"devices": ["iPhone"]})
    assert "Arturo" not in text.replace("the Arturo tab", "")
    assert "Example: hello" in endcall.build_full_transcript([{"role": "arturo", "text": "hello"}])
