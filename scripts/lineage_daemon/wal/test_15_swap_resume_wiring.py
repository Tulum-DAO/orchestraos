"""#15 END-TO-END WIRING (deterministic, no DB / no live green): make_swap_fn must
persist a successor with a per-runtime resume_command derived from the blue_record's
runtime. Captures the ``documents`` list handed to identity_writer.swap_generation and
asserts the promoted gemini seat carries `agy --conversation <cid>` + runtime + model —
the exact by-effect record a live promote writes to registry.json / agent-sessions.json.

This closes the chain read_canonical_blue(runtime) -> real_seams_for(blue_record) ->
make_swap_fn -> build_swap_documents -> swap_generation(documents) without depending on
a flaky antigravity green advancing a checklist.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import real_seams  # noqa: E402
from lineage_daemon.wal.real_seams import make_swap_fn  # noqa: E402


class _FakeConn:
    def close(self):
        pass


def _capture_swap(monkeypatch):
    captured = {}

    def fake_swap_generation(orchestra_dir, root, green, blue_generation_id=None,
                             now=None, documents=None, sync_effects_owner=False):
        captured["documents"] = documents
        captured["sync_effects_owner"] = sync_effects_owner
        return True  # committed

    # Avoid any DB: capture the docs, and no-op the post-commit sid verify (green has a sid).
    monkeypatch.setattr(real_seams.identity_writer, "swap_generation", fake_swap_generation)
    monkeypatch.setattr(real_seams.orchestra_db, "get_connection", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(real_seams.orchestra_db, "verify_session_id_attributed",
                        lambda *a, **k: True)
    return captured


def _succ(docs, root, file="registry.json"):
    return next(r for (f, k, key, r) in docs if f == file and key == root)


def test_gemini_promote_persists_agy_resume_and_identity(monkeypatch):
    captured = _capture_swap(monkeypatch)
    swap_fn = make_swap_fn(
        "/tmp/orch-x",
        blue_record={"generation": 1, "model": "gemini-3.7-flash",
                     "runtime": "gemini", "tier": "T2", "machine": "vps"})
    # green as bg_arm hands it: gen + captured sid, NO resume_command (the #15 gap).
    swap_fn("demo-gemini-pred2", {"generation": 2, "session_id": "cid9"},
            blue_generation_id=100)
    docs = captured["documents"]
    want = "agy --conversation cid9 --dangerously-skip-permissions"
    reg = _succ(docs, "demo-gemini-pred2")
    sess = _succ(docs, "demo-gemini-pred2", "agent-sessions.json")
    assert reg["resume_command"] == want
    assert reg["runtime"] == "gemini"
    assert sess["resume_command"] == want
    assert sess["runtime"] == "gemini"
    assert sess["model"] == "gemini-3.7-flash"


def test_claude_promote_still_derives_claude_resume(monkeypatch):
    # provider-agnostic: a claude lineage green with no resume_command derives claude.
    captured = _capture_swap(monkeypatch)
    swap_fn = make_swap_fn(
        "/tmp/orch-x",
        blue_record={"generation": 4, "model": "claude-opus-4-8[1m]",
                     "runtime": "claude", "tier": "T2"})
    swap_fn("some-claude-seat", {"generation": 5, "session_id": "sidC"},
            blue_generation_id=200)
    reg = _succ(captured["documents"], "some-claude-seat")
    assert reg["resume_command"] == "claude --resume sidC --dangerously-skip-permissions"
    assert reg["runtime"] == "claude"
