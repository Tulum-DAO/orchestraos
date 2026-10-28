"""approval.py pending is the feed the dashboard's notifications bell reads every 10 s. It must not import
watch_gateway: that loads the whole gateway, the voice bridge and Arturo on every poll, and Arturo's
start-up lines then landed in the API's log thousands of times a day."""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

PROBE = (
    "import sys, io, json; sys.path.insert(0, %r);"
    "import approval; out = io.StringIO(); real = sys.stdout; sys.stdout = out;"
    "rc = approval.main(['pending', '--json']); sys.stdout = real;"
    "print(json.dumps({'rc': rc, 'gateway': 'watch_gateway' in sys.modules, 'out': out.getvalue()}))"
) % str(SCRIPTS)


def _run(tmp_path):
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    env = dict(os.environ, ORCHESTRA_DIR=str(data), ORCH_DIR=str(data))
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60,
                       env=env, cwd=str(SCRIPTS))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), r.stderr


def test_pending_never_imports_the_gateway(tmp_path):
    res, _ = _run(tmp_path)
    assert res["rc"] == 0
    assert res["gateway"] is False


def test_pending_prints_json_only_and_nothing_to_stderr(tmp_path):
    res, err = _run(tmp_path)
    assert isinstance(json.loads(res["out"]), list)
    assert "JARVIS" not in err and "intent_router" not in err


def test_the_gateway_parses_menus_with_the_same_function():
    sys.path.insert(0, str(SCRIPTS))
    import menu_parse
    import watch_gateway
    assert watch_gateway._menu is menu_parse.parse_menu
    assert menu_parse.parse_menu({"menu": json.dumps({"options": ["Yes", {"label": "No"}]})})["options"] == [
        {"n": "1", "label": "Yes", "input_kind": "direct"}, {"n": "2", "label": "No", "input_kind": "direct"}]
    assert menu_parse.parse_menu({"menu": None}) is None
