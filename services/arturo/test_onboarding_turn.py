"""Onboarding at the :5071 seam (item G, peer review DEC-1790048447550594): the marker is stripped
from what is stored, the directive rides in the system context for THAT turn only, ordinary text is
byte-identical, /health carries the operator, and an unwritable store never 500s a turn."""
import importlib.util
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wire(mod, tmp_path):
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.build_context = lambda **k: "BASECTX"
    seen = {}
    def brain_reply(messages, conversation_id):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[-1]["content"]
        return ("Nice to meet you.", ["set_operator_fact"])
    mod._brain_reply = brain_reply
    return seen


def test_name_marker_is_stripped_and_directive_applied_for_that_turn_only(tmp_path):
    mod = _load_proxy(); seen = _wire(mod, tmp_path)
    code, body = mod.text_turn("[Onboarding: step=name]\nhi my name is shaw", "c1")
    assert code == 200 and body["ok"]
    assert seen["user"] == "hi my name is shaw"                       # marker never reaches the brain as text
    assert seen["system"].startswith("BASECTX") and "set_operator_fact" in seen["system"]
    assert "[Onboarding" not in mod._THREADS.get_thread("c1")["turns"][0]["content"]   # nor the archive
    assert "operator" in body                                             # additive field on every reply
    code, _ = mod.text_turn("and what can you do?", "c1")
    assert code == 200 and "set_operator_fact" not in seen["system"]     # next turn: no directive


def test_marker_survives_an_attachment_preamble_only_when_first_line(tmp_path):
    mod = _load_proxy(); seen = _wire(mod, tmp_path)
    mod.text_turn("[Onboarding: step=name]\n[attached: /tmp/x.png (12 KB)]\n\nshaw", "c2")
    assert "set_operator_fact" in seen["system"] and seen["user"].startswith("[attached:")


def test_ordinary_text_is_byte_identical(tmp_path):
    mod = _load_proxy(); seen = _wire(mod, tmp_path)
    text = "  keep  my   spacing [not a marker] please "
    mod.text_turn(text, "c3")
    assert seen["user"] == text.strip() and seen["system"] == "BASECTX"


def test_health_carries_operator_none_then_name(tmp_path):
    mod = _load_proxy(); _wire(mod, tmp_path)
    c = mod.app.test_client()
    assert c.get("/health").get_json()["operator"]["name"] is None
    from services.arturo import operator_store as ops
    ops.set_fact(mod.ARTURO_STATE, "name", "Shaw", source="brain")
    assert c.get("/health").get_json()["operator"]["name"] == "Shaw"


def test_set_operator_fact_tool_reports_an_unwritable_store_instead_of_raising(tmp_path):
    mod = _load_proxy(); _wire(mod, tmp_path)
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)
    mod.ARTURO_STATE = ro / "arturo"
    try:
        out = mod.execute_tool("set_operator_fact", {"field": "name", "value": "Shaw"})
    finally:
        ro.chmod(0o700)
    assert out.startswith("Not recorded")


def test_tools_list_uses_the_stores_schema_once():
    mod = _load_proxy()
    from services.arturo import operator_store as ops
    entries = [t for t in mod.TOOLS if t["function"]["name"] == "set_operator_fact"]
    assert len(entries) == 1 and entries[0] is ops.TOOL
