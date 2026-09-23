"""Test-session belt (gm msg_645a8355): NO voice test may ever touch the LIVE
state/voice-calls/ — a leaked live-looking journal can false-trip the no-active-call
arm guard or read as a real call (vc_wdtest3 tripped a real arm guard on 2026-09-10).

ARTURO_VOICE_CALLS_DIR is forced to a per-session scratch dir AT CONFTEST IMPORT,
i.e. before any test exec's arturo-proxy.py (which reads the env at module load).
A test that pins its own scratch via monkeypatch still overrides this afterwards;
what no test can do any more is fall through to the live directory."""
import os
import tempfile

_LIVE = os.path.expanduser("~/scripts/agent-orchestra/state/voice-calls")
_current = os.environ.get("ARTURO_VOICE_CALLS_DIR", "")
if not _current or os.path.realpath(_current) == os.path.realpath(_LIVE):
    os.environ["ARTURO_VOICE_CALLS_DIR"] = tempfile.mkdtemp(prefix="voice-calls-pytest-")

# Fleet-safety belt (gm msg_bfb82953, 2026-09-16): no arturo test may open a socket to
# the LIVE proxy :5071 either -- shared autouse fixture from scripts/hermetic_5071.py.
import sys as _sys
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)
from hermetic_5071 import no_live_arturo_5071  # noqa: F401,E402

# --------------------------------------------------------------------------------------
# configured_install: an orchestra.toml the TEST owns.
#
# Two #86 tests asserted "the default is the CONFIGURED data dir" while reading whatever
# config the MACHINE happened to have. On a configured box they passed; on CI and on any
# fresh clone `config.load()` raises ConfigError (only orchestra.example.toml ships) and
# both went red -- in the very PR that exists to make a FRESH INSTALL correct.
#
# Machine-dependence was the deeper half: even green, they asserted against Shaw's own
# data_dir, so the assertion's meaning changed with the host. This fixture writes a real
# config pointing at tmp_path, so the property under test is pinned by the test itself.
#
# Built from orchestra.example.toml rather than a hand-written stub ON PURPOSE: the
# example is required to carry every required key, so a newly-added required key keeps
# this fixture working instead of failing as a missing-key ConfigError in a test that has
# nothing to do with that key.
# --------------------------------------------------------------------------------------
import pytest as _pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EXAMPLE_DATA_DIR_LINE = 'dir = "/var/lib/orchestraos"'


def _clear_config_cache():
    """services.config.load is @lru_cache(maxsize=1) -- a config read by an EARLIER test
    survives a monkeypatched ORCHESTRA_CONFIG. Clearing on BOTH setup and teardown keeps
    this fixture from leaking its tmp config into the next test, which is the mirror of
    the order-dependent green the cache already caused once here."""
    _sys.path.insert(0, os.path.join(_REPO, "services"))
    import config as _c
    _c.load.cache_clear()
    return _c


@_pytest.fixture
def configured_install(tmp_path, monkeypatch):
    """Yields the data_dir of a real, minimal, test-owned OrchestraOS config."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    example = os.path.join(_REPO, "orchestra.example.toml")
    text = open(example).read()
    # A silent no-op replace would leave the fixture pointing at /var/lib/orchestraos and
    # the test would assert against a path it does not own -- assert the substitution.
    assert text.count(_EXAMPLE_DATA_DIR_LINE) == 1, (
        "orchestra.example.toml's [data] dir line changed shape; this fixture must be updated")
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(text.replace(_EXAMPLE_DATA_DIR_LINE, f'dir = "{data_dir}"', 1))

    monkeypatch.setenv("ORCHESTRA_CONFIG", str(cfg))
    c = _clear_config_cache()
    assert c.load().data_dir == data_dir, "fixture did not take effect"
    yield data_dir
    c.load.cache_clear()
