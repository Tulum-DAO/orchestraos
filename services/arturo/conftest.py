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
