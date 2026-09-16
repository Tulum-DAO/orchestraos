"""DRIVER GATE (gm-required): fire_gemini_demo.py must drive arm/wake/obs through the
PRODUCTION runtime-DISPATCH seams, never a provider-hardwired path — the same divergence
class as the blue-pid override. It hardwired GeminiWalAdapter + resolve_gemini_cid +
read_ctx('gemini') instead of the multiplexer factory + resolve_cid_any + read_ctx(runtime).
This gate fails LOUD if any provider-specific dispatch token reappears in the driver, and
asserts the production-dispatch symbols are present. Plus hermetic source_path_for coverage
(the one runtime-specific input, a DATA lookup).
"""
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters as ca  # noqa: E402

_DRIVER = os.path.join(os.path.dirname(__file__), "..", "..", "fire_gemini_demo.py")


def _driver_src():
    with open(os.path.abspath(_DRIVER)) as fh:
        # strip trailing comments so a comment mentioning a provider never trips the gate
        return "\n".join(line.split("#", 1)[0] for line in fh)


# Provider-hardwired dispatch tokens that must NOT appear in the driver's executable code
# (they are the divergence-from-production defect gm flagged).
FORBIDDEN = (
    "resolve_gemini_cid", "resolve_codex_cid",
    "GeminiWalAdapter", "CodexWalAdapter",
    "adapter_gemini", "adapter_codex",
    'read_ctx("gemini"', "read_ctx('gemini'",
    'read_ctx("codex"', "read_ctx('codex'",
    '"runtime": "gemini"', "'runtime': 'gemini'",
    '"runtime": "codex"', "'runtime': 'codex'",
)
# Production runtime-dispatch symbols the converged driver MUST use.
REQUIRED = ("resolve_cid_any", "make_adapter", "source_path_for", "read_ctx(runtime")


def test_driver_uses_no_provider_hardwired_dispatch():
    src = _driver_src()
    offenders = [tok for tok in FORBIDDEN if tok in src]
    assert not offenders, f"driver hardwires provider dispatch (use production seams): {offenders}"


def test_driver_uses_production_dispatch_symbols():
    src = _driver_src()
    missing = [tok for tok in REQUIRED if tok not in src]
    assert not missing, f"driver missing production-dispatch seam(s): {missing}"


def test_source_path_for_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sid = "01a0a300-261d-7ff2-a2e1-f697da67ec8d"
    d = os.path.join(str(tmp_path), ".codex", "sessions", "2026", "09", "14")
    os.makedirs(d)
    p = os.path.join(d, f"rollout-2026-09-14T22-58-11-{sid}.jsonl")
    open(p, "w").close()
    assert ca.source_path_for("codex", sid) == p


def test_source_path_for_gemini_and_claude_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ca.source_path_for("gemini", "cidX").endswith(
        "/.gemini/antigravity-cli/conversations/cidX.db")
    claude = ca.source_path_for("claude", "sidY", cwd="/home/testuser/agent-orchestra")
    assert claude.endswith("/sidY.jsonl") and "-home-testuser-agent-orchestra" in claude


def test_source_path_for_unknown_or_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ca.source_path_for("nope", "cid") is None
    assert ca.source_path_for("gemini", None) is None
    assert ca.source_path_for("codex", "no-such-sid") is None  # no rollout glob hit
