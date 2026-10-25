"""Codex idle release: a codex pane whose idle prompt is visibly back skips only the
breathe-window quiet-wait, the way Claude's Stop-hook release does."""
import importlib.util
import inspect
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("message_router_codex_idle",
                                               os.path.join(_here, "message-router.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def test_release_only_for_a_visibly_idle_codex_pane():
    assert mr.codex_idle_release("codex", True) is True
    assert mr.codex_idle_release("codex", False) is False


def test_other_runtimes_never_release():
    for rt in ("claude", "gemini", "agy", "grok", "", None):
        assert mr.codex_idle_release(rt, True) is False


def test_release_skips_only_the_breathe_window():
    """STRUCTURAL: main() is one long loop. The release must be computed before the
    breathe-window hold, appear in its condition, and must NOT appear in the attached-client
    guard (a human typing still wins)."""
    src = inspect.getsource(mr.main)
    rel = src.index("_codex_idle_release = codex_idle_release(")
    attached = src.index("if _presence and msg.get(\"priority\")")
    breathe = src.index("if (idle_secs < BREATHE_WINDOW_S")
    assert rel < attached < breathe
    breathe_cond = src[breathe:src.index(":", src.index("_codex_idle_release", breathe))]
    assert "not _codex_idle_release" in breathe_cond
    attached_line = src[attached:src.index("\n", attached)]
    assert "_codex_idle_release" not in attached_line


def test_idle_gate_reuses_the_codex_probe_and_keeps_agent_is_idle_for_others():
    src = inspect.getsource(mr.main)
    assert ("_codex_idle_release if _runtime == \"codex\"\n"
            "                    else agent_is_idle(session, _runtime)") in src
    assert src.count("_runtime = routing_runtime(_target_agent, meta)") == 1
