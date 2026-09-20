"""RED-first (#86): gemini_live_bridge.py pinned ORCHESTRA_DIR to the checkout, ignoring the
ORCHESTRA_DIR env every sibling honours — so GEMINI_API_KEY was looked up in <repo>/.env.secrets
and live-call journals landed in <repo>/state/voice-calls while the gateway serves them from the
data dir: every browser live call's transcript card 404'd unless data dir == repo root."""
import importlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent.parent


def _fresh_import(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("ORCHESTRA_DIR", raising=False)
    else:
        monkeypatch.setenv("ORCHESTRA_DIR", env_value)
    sys.modules.pop("gemini_live_bridge", None)
    monkeypatch.syspath_prepend(str(HERE))
    return importlib.import_module("gemini_live_bridge")


def test_orchestra_dir_env_wins(monkeypatch, tmp_path):
    mod = _fresh_import(monkeypatch, str(tmp_path))
    assert mod.ORCHESTRA_DIR == tmp_path


def test_checkout_is_the_fallback_when_env_unset(monkeypatch):
    mod = _fresh_import(monkeypatch, None)
    assert mod.ORCHESTRA_DIR == CHECKOUT
