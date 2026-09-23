"""RED-first (#86): gemini_live_bridge.py pinned ORCHESTRA_DIR to the checkout, ignoring the
ORCHESTRA_DIR env every sibling honours — so GEMINI_API_KEY was looked up in <repo>/.env.secrets
and live-call journals landed in <repo>/state/voice-calls while the gateway serves them from the
data dir: every browser live call's transcript card 404'd unless data dir == repo root."""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # services/config.py

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent.parent


def _fresh_import(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("ORCHESTRA_DIR", raising=False)
    else:
        monkeypatch.setenv("ORCHESTRA_DIR", env_value)
    # services/config.load() is @lru_cache(maxsize=1), so a config read by an EARLIER test in
    # the same session survives a monkeypatched ORCHESTRA_CONFIG. Without this the last-resort
    # test passes alone and fails in the suite — an order-dependent green I would have shipped.
    import config as _c
    _c.load.cache_clear()
    sys.modules.pop("gemini_live_bridge", None)
    monkeypatch.syspath_prepend(str(HERE))
    return importlib.import_module("gemini_live_bridge")


def test_orchestra_dir_env_wins(monkeypatch, tmp_path):
    mod = _fresh_import(monkeypatch, str(tmp_path))
    assert mod.ORCHESTRA_DIR == tmp_path


def test_configured_data_dir_is_the_default_when_env_unset(monkeypatch, configured_install):
    """SUPERSEDED CONTRACT. This asserted the CHECKOUT as the fallback, which is the half of #86
    that was never actually fixed: honouring the env was, but an install that sets no env still
    journalled to the repo root while the gateway read the data dir, so every transcript card
    404'd. The default is now the CONFIGURED data dir.

    Takes `configured_install` rather than reading the host's config. Without it this test
    needs the MACHINE to have an orchestra.toml: green on a configured box, ConfigError on CI
    and on any fresh clone -- red in the PR whose whole subject is the fresh install. It also
    asserted against whatever data_dir the host declared, so its meaning moved with the host.
    """
    mod = _fresh_import(monkeypatch, None)
    assert mod.ORCHESTRA_DIR == configured_install


def test_checkout_is_the_LAST_resort_when_no_config_can_be_read(monkeypatch):
    """The fallback the old test pinned still exists — it is now reached only when the config is
    unreadable, which keeps a bare checkout with no orchestra.toml working as before."""
    monkeypatch.setenv("ORCHESTRA_CONFIG", "/nonexistent/orchestra.toml")
    mod = _fresh_import(monkeypatch, None)
    assert mod.ORCHESTRA_DIR == CHECKOUT
