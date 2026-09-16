"""Stage-4 hydrate sanitization proof (gm gate). The hydrate seam renders a
digest of the target seat's LIVE WAL tape and injects it into Green — so the
digest MUST be court-clean (the WAL is a contagion vector).

BYTES-ONLY discipline: these tests operate on the digest OUTPUT + structural
summaries; they NEVER read the poison bodies of the tape into reasoning.

Two proofs on a synthetic tape (deterministic, hermetic) that mirror what the
DP-1 measurement on the REAL isb tape confirmed (0.0000% world-output FP-rate,
zero signature in the rendered digest):
  - class defense: model-voice (response/thinking) bodies never appear verbatim
    in the digest — signature-independent (holds even with no known instance);
  - world-output FP: a clean tool_result summary is NOT flagged by court_scrub.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.digest import render_digest  # noqa: E402
from lineage_daemon.wal.court_scrub import court_scrub  # noqa: E402


def _tape(tmp_path):
    s = WalStore(str(tmp_path / "isb.db"))
    # a model-voice response whose body is poisonous if replayed verbatim
    s.append(ts=0.0, lineage_root="identity-store-builder", generation=2,
             sid="s", runtime="claude", kind="response", summary="thinking",
             body_ref="mv:0", source_path="src", source_off=0, integrity="h")
    # clean world-output rows (the FP-rate surface), distinct body_refs
    for i in range(20):
        s.append(ts=0.0, lineage_root="identity-store-builder", generation=2,
                 sid="s", runtime="claude", kind="tool_result",
                 summary=f"result ({i} lines) build OK", body_ref=f"wo:{i}",
                 source_path="src", source_off=i, integrity="h")
    return s


def test_hydrate_model_voice_never_verbatim(tmp_path):
    s = _tape(tmp_path)
    poison = "MODEL_VOICE_TO_NOT_REPLAY"
    # class defense: only the response (model-voice) ref resolves to poison; the
    # digest must never surface it verbatim (response bodies are NEVER drilled).
    # World-output (tool_result) resolves to clean content.
    def resolve(ref):
        return poison if ref == "mv:0" else "clean build output"
    d = render_digest(s, "identity-store-builder",
                      resolve_body=resolve, scrub=court_scrub)
    assert poison not in d["text"]          # model-voice never verbatim
    assert court_scrub(d["text"])[1] is False


def test_world_output_clean_summaries_not_flagged(tmp_path):
    s = _tape(tmp_path)
    world = [r for r in s.events("identity-store-builder")
             if r["kind"] == "tool_result"]
    flagged = sum(1 for r in world if court_scrub(r["summary"] or "")[1])
    assert flagged == 0                     # 0% FP on clean world-output


def test_digest_is_substantive_not_vacuously_clean(tmp_path):
    # poison-absence must not be because the digest is empty
    s = _tape(tmp_path)
    d = render_digest(s, "identity-store-builder", scrub=court_scrub)
    assert "identity-store-builder" in d["text"]
    assert len(d["text"]) > 50
