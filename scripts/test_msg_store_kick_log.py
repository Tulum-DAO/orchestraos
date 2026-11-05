"""A router pass kicked by a send writes beside the router beat's own log (<data dir>/logs/router.log, as `orchestra
up` names it), never into the checkout: what that pass says (a hold, a page, a test seat it kept quiet about) must be
where the router is read. Nothing is spawned (the install fixture records Popen calls)."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_msg_store_kick_on_send import install, ms  # noqa: E402,F401


def test_the_kicked_pass_writes_to_the_beats_log(install):
    data, calls = install
    (data / "logs").mkdir()
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    cmd, kw = calls[0]
    assert kw["stdout"].name == str(data / "logs" / "router.log")
    assert kw["stderr"] is kw["stdout"]


def test_no_logs_dir_means_no_output_file_anywhere(install):
    data, calls = install
    ms.kick_router_after_send(str(data / "state" / "tasks.db"))
    assert calls[0][1]["stdout"].name == os.devnull


def test_the_gateways_deliver_now_pass_writes_to_the_beats_log(monkeypatch, tmp_path):
    import importlib.util
    import subprocess
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("watch_gateway_kicklog", HERE / "watch_gateway.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    (tmp_path / "logs").mkdir()
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.append(kw))
    assert G._kick_router() is True
    assert seen[0]["stdout"].name == str(tmp_path / "logs" / "router.log")
