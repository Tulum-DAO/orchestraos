"""Per-adapter NORMALIZATION test (gm-required v2 invariant, DEC ccbd3793).

The contract: read_ctx returns the fraction of USABLE context consumed (0..1) with the
SAME meaning for every provider. Each adapter owns its conversion; here we pin one known
raw input per provider (gm's requirement — codex 118899/258400) and assert the normalized
fraction, plus the fail-closed misses (registry miss, seat-not-found, malformed input).
"""
import json
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import ctx_adapters as ca  # noqa: E402


# ---- normalization helpers (the semantic each adapter converts to) ----

def test_normalize_from_tokens_codex_pin():
    """gm PIN: codex 118899/258400 -> the normalized fraction the core must see."""
    assert ca.normalize_from_tokens(118899, 258400) == 118899 / 258400
    assert abs(ca.normalize_from_tokens(118899, 258400) - 0.4601) < 0.001


def test_normalize_from_pct_claude_semantic():
    """claude detector reports used_pct 0..100 -> same 0..1 fraction meaning."""
    assert ca.normalize_from_pct(82) == 0.82
    assert ca.normalize_from_pct(0) == 0.0
    assert ca.normalize_from_pct(100) == 1.0


def test_normalize_clamps_and_rejects_garbage():
    assert ca.normalize_from_pct(150) == 1.0       # clamp >100
    assert ca.normalize_from_pct(-5) == 0.0        # clamp <0
    assert ca.normalize_from_pct("x") is None      # non-numeric
    assert ca.normalize_from_tokens(5, 0) is None  # /0 guard
    assert ca.normalize_from_tokens(None, 100) is None


def test_gemini_normalizes_over_1m_window():
    """gemini estimated_tokens / 1M usable window -> same fraction meaning."""
    assert ca.normalize_from_tokens(730000, ca.GEMINI_USABLE_WINDOW) == 0.73


# ---- claude detector adapter: real file round-trip, normalized + fresh ----

def test_claude_adapter_reads_and_normalizes(tmp_path):
    sid = "sid-abc"
    (tmp_path / f"claude-ctx-{sid}.json").write_text(
        json.dumps({"used_pct": 82, "timestamp": 1_000_000.0}))
    pct, fresh = ca.read_ctx("claude", "seat", detector_dir=str(tmp_path), sid=sid,
                             now=1_000_000.0, ttl_s=120.0)
    assert pct == 0.82 and fresh is True


def test_claude_adapter_stale_fails_closed(tmp_path):
    sid = "sid-stale"
    (tmp_path / f"claude-ctx-{sid}.json").write_text(
        json.dumps({"used_pct": 82, "timestamp": 1_000_000.0}))
    pct, fresh = ca.read_ctx("claude", "seat", detector_dir=str(tmp_path), sid=sid,
                             now=1_000_000.0 + 999, ttl_s=120.0)  # aged past ttl
    assert pct is None and fresh is False


def test_claude_adapter_absent_fails_closed(tmp_path):
    pct, fresh = ca.read_ctx("claude", "seat", detector_dir=str(tmp_path), sid="nope",
                             now=1.0, ttl_s=120.0)
    assert pct is None and fresh is False


# ---- registry: unknown runtime + adapter miss are fail-closed, never a raise ----

def test_unknown_runtime_fails_closed():
    assert ca.read_ctx("some-future-provider", "seat") == (None, False)
    assert ca.read_ctx("", "seat") == (None, False)
    assert ca.read_ctx(None, "seat") == (None, False)


def test_registry_has_exactly_the_wrapped_readers():
    assert set(ca.CTX_ADAPTER_REGISTRY) == {"claude", "codex", "gemini"}


# ---- gemini identity resolution (fire finding): resolve by "You are <seat>" ----

def test_resolve_gemini_cid_by_declaration(tmp_path, monkeypatch):
    """A gemini seat self-identifies via its first 'You are <seat>' prompt -> cid, robust
    for ANY seat name (the name-matcher gap that left demo seats 'unknown')."""
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".gemini/antigravity-cli/brain/cid-xyz/.system_generated/logs"
    d.mkdir(parents=True)
    (d / "transcript.jsonl").write_text(
        json.dumps({"type": "USER_INPUT", "content": "You are demo-gemini-pred. Read /tmp/x"}) + "\n")
    assert ca.resolve_gemini_cid("demo-gemini-pred") == "cid-xyz"
    assert ca.resolve_gemini_cid("no-such-seat") is None
