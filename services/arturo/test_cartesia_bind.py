"""The Cartesia and Gemini Live voice services are non-fleet paths too (orchestraos-builder, #278): their
models are offered only what Arturo's non-fleet allowlist runs (Cartesia also keeps end_call, the call's
own control)."""
import importlib.util
import pathlib
import sys
import types


def _load(monkeypatch):
    # the Cartesia `line` SDK is optional and absent in CI: stand it in with names only
    def end_call():
        pass
    mods = {
        "line": types.ModuleType("line"),
        "line.voice_agent_app": types.SimpleNamespace(VoiceAgentApp=lambda **k: object()),
        "line.llm_agent": types.SimpleNamespace(LlmAgent=object, LlmConfig=object, end_call=end_call),
        "line.llm_agent.tools": types.SimpleNamespace(ToolEnv=object),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("GEMINI_API_KEY", "")            # the module writes both; restore them after
    monkeypatch.setenv("CARTESIA_API_KEY", "")
    monkeypatch.delitem(sys.modules, "arturo_proxy_module", raising=False)
    spec = importlib.util.spec_from_file_location(
        "cartesia_bind_test", pathlib.Path("services/arturo/cartesia_arturo_service.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_cartesia_call_is_offered_only_the_non_fleet_allowlist(monkeypatch):
    mod = _load(monkeypatch)
    offered = {fn.__name__ for fn in mod.ALL_TOOLS}
    assert offered == {"end_call", "knowledge"}
    for name in ("run_shell", "gm_command", "answer_menu", "read_screen_context"):
        assert name not in offered


def test_a_gemini_live_call_is_offered_only_the_non_fleet_allowlist():
    # the browser's Live voice mode (gateway /live): no caller on the call, so non-fleet
    from services.arturo import gemini_live_bridge as glb
    assert glb.arturo_mod is not None
    assert {d["name"] for d in glb.offered_declarations()} == {"knowledge", "list_agents"}
    assert all(d["name"] in glb.arturo_mod._NON_FLEET_ALLOWED for d in glb.offered_declarations())


def test_a_fleet_gemini_live_session_is_offered_every_tool():
    """G1': /live admits only the fleet bearer, so the gateway opens the session as "fleet"."""
    from services.arturo import gemini_live_bridge as glb
    assert glb.offered_declarations("fleet") == glb.TOOL_DECLARATIONS
    assert {d["name"] for d in glb.offered_declarations(None)} == {"knowledge", "list_agents"}


def test_a_fleet_gemini_live_tool_call_runs_under_a_fleet_turn(monkeypatch):
    from services.arturo import gemini_live_bridge as glb
    seen = []
    monkeypatch.setattr(glb, "execute_tool_fn",
                        lambda name, args: seen.append(glb.arturo_mod._is_fleet(glb.arturo_mod._TEAM_TURN.get())) or "ok")
    glb.run_tool("fleet", "vc_live_x", "gm_command", {})
    glb.run_tool(None, "vc_live_x", "gm_command", {})
    assert seen == [True, False]
    assert glb.arturo_mod._TEAM_TURN.get() is None, "the turn is reset after the call"
