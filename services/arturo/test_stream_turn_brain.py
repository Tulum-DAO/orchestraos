"""RED-first: a streamed turn's prompt describes the brain the operator CHOSE.

/text sets the turn's brain before the context is built; /text/stream and /text/prewarm built it
with no turn brain set, so the identity line described the DEFAULT. On staging (2026-09-30) a
Codex turn was told "you are thinking with Claude (claude-opus-5-5)" — which is why it called
Claude's earlier answer "incorrect", and then guessed its own model.
"""
import types

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture


def _stub_streaming(P, seen):
    P.build_context = lambda **k: (seen.append(P._turn_brain()), "CTX")[1]
    P._text_stream = types.SimpleNamespace(
        stream_turn=lambda **kw: iter(()),
        whole_turn=lambda *a, **kw: iter(()),
        with_heartbeat=lambda turn, interval_s=10.0: iter(()),
        sse_frame=lambda ev: "",
        warm_argv=lambda *a, **k: ["cli"],
    )
    P._WARM_POOL = types.SimpleNamespace(prewarm=lambda *a, **k: True, turn=None,
                                         sync=lambda *a, **k: None, discard=lambda *a: None)


def test_a_streamed_turn_builds_its_context_as_the_chosen_brain(P):
    seen = []
    _stub_streaming(P, seen)
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "What about now?", "conversation_id": "c1",
                                         "brain": {"provider": "codex", "model": ""}})
        r.get_data()
    assert seen and seen[0].describe()["runtime"] == "codex"
    assert P._BRAIN_THIS_TURN.get() is None              # and it does not leak past the build


def test_a_prewarm_builds_its_context_as_the_chosen_brain(P):
    seen = []
    _stub_streaming(P, seen)
    with P.app.test_client() as c:
        r = c.post("/text/prewarm", json={"conversation_id": "c1",
                                          "brain": {"provider": "claude", "model": "claude-sonnet-5"}})
    assert r.status_code == 200
    assert seen and seen[0].describe()["model"] == "claude-sonnet-5"
