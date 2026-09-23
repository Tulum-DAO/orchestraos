"""#86: the bridge must journal voice calls where the GATEWAY reads them.

ORCHESTRA_DIR defaulted to the repo root, so the bridge wrote <repo>/state/voice-calls while the
gateway serves transcript cards from <data dir>/state/voice-calls. On a fresh install those are
different directories, every card 404s, and check-voice-path.sh reports it as fix item 5. The
documented workaround is a symlink, which is a per-machine patch for a path the config already
knows.
"""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def _voice_calls_dir(env):
    """Import the bridge's dir resolution under a given environment."""
    src = (HERE / "gemini_live_bridge.py").read_text()
    ns = {"__file__": str(HERE / "gemini_live_bridge.py"), "__name__": "_probe"}
    head = src.split("execute_tool_fn = None")[0]
    old = dict(os.environ)
    os.environ.update(env)
    try:
        exec(compile(head, "bridge-head", "exec"), ns)
        return Path(ns["ORCHESTRA_DIR"]) / "state" / "voice-calls"
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_env_still_wins():
    assert _voice_calls_dir({"ORCHESTRA_DIR": "/srv/explicit"}) == Path("/srv/explicit/state/voice-calls")


def test_default_is_the_CONFIGURED_data_dir_not_the_repo_root():
    """RED before the fix: this resolves to <repo>/state/voice-calls, which the gateway never reads."""
    env = {k: v for k, v in os.environ.items() if k != "ORCHESTRA_DIR"}
    os.environ.pop("ORCHESTRA_DIR", None)
    got = _voice_calls_dir({})
    assert got != REPO / "state" / "voice-calls", (
        f"bridge journals to the repo root ({got}); the gateway reads the configured data dir")
