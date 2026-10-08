"""The gateway's loopback upstreams follow the configured ports, not literals.

pm doc test, 2026-10-08: with [api] port changed, the gateway still sent /upload and red-alert
forwarding to 127.0.0.1:8888, i.e. to whatever other app owned that port. The same literal
pattern held for the dashboard origin (CRM passthrough, 8891) and every Arturo upstream (5071).
The import runs in a subprocess so its module-level constants see exactly this environment.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

_PRINT = ("import json, watch_gateway as w; print(json.dumps({k: getattr(w, k) for k in "
          "['API_URL', 'API_ORIGIN', 'ARTURO_PTT_URL', 'ARTURO_TRANSCRIBE_URL', "
          "'ARTURO_PTT_STREAM_BASE', 'ARTURO_PTT_VENDOR_URL', 'ARTURO_TEXT_BASE', "
          "'ARTURO_PTT_VOICE_BASE', 'ARTURO_FINALIZE_URL']}))")


def _consts(tmp_path, **env):
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("ORCH", "ARTURO_", "WATCH_GATEWAY"))}
    base.update({"ORCHESTRA_DIR": str(tmp_path), "ORCH_DIR": str(tmp_path), "HOME": str(tmp_path)})
    base.update(env)
    out = subprocess.run([sys.executable, "-c", _PRINT], cwd=SCRIPTS, env=base,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_defaults_are_unchanged(tmp_path):
    c = _consts(tmp_path)
    assert c["API_URL"] == "http://127.0.0.1:8888"
    assert c["API_ORIGIN"] == c["API_URL"]       # People passthrough calls the API directly
    assert c["ARTURO_PTT_URL"] == "http://127.0.0.1:5071/ptt"
    assert c["ARTURO_FINALIZE_URL"] == "http://127.0.0.1:5071/finalize-call"


def test_configured_ports_reach_every_loopback_upstream(tmp_path):
    c = _consts(tmp_path, ORCHESTRA_API_PORT="18888", ORCHESTRA_DASHBOARD_PORT="18891",
                ORCHESTRA_ARTURO_PORT="15071")
    assert c["API_URL"] == "http://127.0.0.1:18888"
    assert c["API_ORIGIN"] == "http://127.0.0.1:18888"   # the API, not the dashboard hop
    for k, v in c.items():
        if k.startswith("ARTURO_"):
            assert v.startswith("http://127.0.0.1:15071"), (k, v)


def test_an_empty_port_falls_back_to_the_default(tmp_path):
    """scripts/orchestra-env.sh can export a key the toml leaves unset as an empty string."""
    c = _consts(tmp_path, ORCHESTRA_API_PORT="", ORCHESTRA_ARTURO_PORT="")
    assert c["API_URL"] == "http://127.0.0.1:8888"
    assert c["ARTURO_TEXT_BASE"] == "http://127.0.0.1:5071"


def test_explicit_url_overrides_still_win(tmp_path):
    c = _consts(tmp_path, ORCHESTRA_API_PORT="18888", ORCH_API_URL="http://127.0.0.1:9999",
                ARTURO_PTT_URL="http://127.0.0.1:7777/ptt")
    assert c["API_URL"] == "http://127.0.0.1:9999"
    assert c["ARTURO_PTT_URL"] == "http://127.0.0.1:7777/ptt"


ENV_SH = SCRIPTS / "orchestra-env.sh"


def _sourced(tmp_path, toml):
    cfg = tmp_path / "orchestra.toml"
    cfg.write_text(toml)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORCH")}
    env["ORCHESTRA_CONFIG"] = str(cfg)
    out = subprocess.run(["bash", "-c", f"source '{ENV_SH}'; echo \"$ORCH_API_URL\""],
                         env=env, capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def test_orchestra_env_sh_exports_the_same_api_url_as_child_env(tmp_path):
    """The shell mirror of child_env (sourced by spawn-agent.sh and cron beats)."""
    assert _sourced(tmp_path, "[api]\nport = 18888\n") == "http://127.0.0.1:18888"
    assert _sourced(tmp_path, "[api]\nhost = \"0.0.0.0\"\nport = 18888\n") == "http://127.0.0.1:18888"
    assert _sourced(tmp_path, "[data]\ndir = \"/tmp\"\n") == "http://127.0.0.1:8888"
    assert _sourced(tmp_path, "[api]\nhost = \"::1\"\nport = 18888\n") == "http://[::1]:18888"
