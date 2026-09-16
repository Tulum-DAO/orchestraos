"""Pin the conftest belt (gm msg_645a8355): under pytest, ARTURO_VOICE_CALLS_DIR must
never resolve to the live state/voice-calls/, and a freshly loaded proxy module must
inherit the scratch dir. If this fails, some future change re-opened the journal-leak
landmine that tripped the 2026-09-10 arm guard (vc_wdtest3)."""
import importlib.util
import os
import pathlib


def test_env_is_forced_off_the_live_dir():
    live = os.path.realpath(os.path.expanduser("~/scripts/agent-orchestra/state/voice-calls"))
    cur = os.environ.get("ARTURO_VOICE_CALLS_DIR", "")
    assert cur, "conftest belt missing: ARTURO_VOICE_CALLS_DIR unset under pytest"
    assert os.path.realpath(cur) != live, "pytest is pointed at LIVE state/voice-calls/"


def test_fresh_proxy_module_inherits_scratch_dir():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_beltcheck", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    live = os.path.realpath(os.path.expanduser("~/scripts/agent-orchestra/state/voice-calls"))
    assert os.path.realpath(str(mod.VOICE_CALLS_DIR)) != live
    if getattr(mod, "_STREAM_RELAY", None) is not None:
        mod._STREAM_RELAY.shutdown()
