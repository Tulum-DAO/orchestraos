"""A text turn is stored without any tool-call envelope: history is replayed into later turns and
shown in the thread, so raw {"tool_calls": ...} JSON there would reach the operator a second time."""
import importlib.util
import pathlib

from services.arturo.conftest import as_fleet


def _load_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("ORCHESTRA_ARTURO_BRAIN", "api")
    spec = importlib.util.spec_from_file_location("arturo_proxy_record", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return as_fleet(mod)


def test_the_stored_reply_has_no_envelope(monkeypatch, tmp_path):
    P = _load_proxy(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(P._TEXT_HISTORY, "append", lambda cid, role, text, brain=None: seen.append((role, text)))
    monkeypatch.setattr(P._THREADS, "record_turn", lambda cid, text, reply, brain=None, effective=None, **kw: seen.append(("thread", reply)))
    P._record_text_turn("c1", "who is up?", 'e\n{"tool_calls": [{"name": "list_agents", "arguments": {}}]}')
    assert seen and all("tool_calls" not in t for _, t in seen)
    assert ("assistant", "e") in seen and ("thread", "e") in seen


def test_an_ordinary_reply_is_stored_as_is(monkeypatch, tmp_path):
    P = _load_proxy(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(P._TEXT_HISTORY, "append", lambda cid, role, text, brain=None: seen.append((role, text)))
    monkeypatch.setattr(P._THREADS, "record_turn", lambda cid, text, reply, brain=None, effective=None, **kw: seen.append(("thread", reply)))
    P._record_text_turn("c1", "hi", "Use a set {1, 2}.")
    assert ("assistant", "Use a set {1, 2}.") in seen
