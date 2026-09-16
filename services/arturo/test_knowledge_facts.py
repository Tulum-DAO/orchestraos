# gm msg_c0a87329 (the operator 06:30 call, field-proven): _knowledge_lookup HIT the
# project entry but rendered only summary/current_state — entry['facts'], where
# ALL behavioral facts live, was invisible. The KB fact telling Arturo not to
# claim capabilities existed and could not reach it (the bug hid its own fix).
import importlib.util
import json
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(tmp_path, facts):
    (tmp_path / "projects.json").write_text(json.dumps({
        "testproj": {"name": "TestProj", "aliases": ["tee pee"], "status": "active",
                     "priority": 1, "summary": "A test project.",
                     "current_state": "testing.", "facts": facts}}))
    return tmp_path


def test_project_result_includes_facts(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR",
                        _seed(tmp_path, {"confirm_before_inject": "Always confirm with the operator first.",
                                         "voice_ui_desync": "Journal and div can diverge."}))
    out = mod._knowledge_lookup("testproj", category="projects")
    assert "confirm_before_inject: Always confirm with the operator first." in out
    assert "voice_ui_desync: Journal and div can diverge." in out
    # existing fields still render
    assert "A test project." in out and "testing." in out


def test_long_fact_values_capped(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR",
                        _seed(tmp_path, {"big": "x" * 2000}))
    out = mod._knowledge_lookup("testproj", category="projects")
    assert "big: " in out
    assert "x" * 501 not in out          # per-fact cap
    assert "…" in out                     # truncation is visible, not silent


def test_no_facts_key_unchanged(tmp_path, monkeypatch):
    mod = _load_proxy()
    (tmp_path / "projects.json").write_text(json.dumps({
        "p2": {"name": "Plainproj", "status": "active", "priority": 2,
               "summary": "s.", "current_state": "c."}}))
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR", tmp_path)
    out = mod._knowledge_lookup("plainproj", category="projects")
    assert "[PROJECT] Plainproj" in out and "facts" not in out.lower()


def test_broken_facts_never_breaks_lookup(tmp_path, monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR",
                        _seed(tmp_path, "not-a-dict"))
    out = mod._knowledge_lookup("testproj", category="projects")
    assert "[PROJECT] TestProj" in out    # entry still renders
