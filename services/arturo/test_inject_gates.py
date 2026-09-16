# Gate tests for the end-of-call gm injection (the operator order 2026-08-10): NO injection unless the
# kill switch is on AND the call is genuine funnel-origin. Verified on a scratch dir — never
# touches state/voice-calls or the live gm.
import json
import importlib.util
import pathlib

import services.arturo.endcall as ec


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_journal(mod, dir_, origin):
    j = mod._CallJournal(dir=dir_, page="voice")
    j.add_turn("user", "hi")
    j.add_turn("arturo", "hello")
    d = json.loads(j.path.read_text())
    d["origin"] = origin
    from services.arturo.call_journal import _atomic_write
    _atomic_write(j.path, d)
    return j.path


def test_kill_switch_blocks_all_injection(tmp_path, monkeypatch):
    mod = _load_proxy()
    mod._GM_INJECT_ENABLED = False
    hits = {"n": 0}
    monkeypatch.setattr(ec, "_default_post", lambda s, t: (hits.__setitem__("n", hits["n"] + 1) or 200))
    mod._finalize_journal_file(_make_journal(mod, tmp_path, "funnel"))
    import time; time.sleep(0.2)
    assert hits["n"] == 0            # even a funnel call is silent while the kill switch is on


def test_origin_gate_only_funnel_injects(tmp_path, monkeypatch):
    mod = _load_proxy()
    mod._GM_INJECT_ENABLED = True    # kill switch lifted → gate 2 is the guard
    results = {}
    for origin in ("local", "test", "funnel"):
        hits = {"n": 0}
        monkeypatch.setattr(ec, "_default_post", lambda s, t: (hits.__setitem__("n", hits["n"] + 1) or 200))
        mod._finalize_journal_file(_make_journal(mod, tmp_path, origin))
        import time; time.sleep(0.2)
        results[origin] = hits["n"]
    assert results["local"] == 0
    assert results["test"] == 0
    assert results["funnel"] == 1
