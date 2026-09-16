"""RED tests — collect.py ctx range / unknown-model guard (gm mechanical spec
/tmp/gm-amp-ctxpct.md, incident inspiration.bg.json last_valid_ctx_pct=1.43).

Root cause by effect: jsonl_fallback_pct guessed the 160k "standard" ceiling
for model='unknown' while the live session ran a 1M window, yielding 143 (%),
and resolve_ctx_pct passed it through unbounded. Contract: never guess a
ceiling for an unknown model (None + loud skip line), and every source's value
is range-guarded to 0..100 (out-of-range -> loud line + next source).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from scripts.lineage_daemon import collect


def test_jsonl_fallback_unknown_model_returns_none():
    assert collect.jsonl_fallback_pct(229000, "unknown") is None
    assert collect.jsonl_fallback_pct(229000, "UNKNOWN") is None
    assert collect.jsonl_fallback_pct(229000, "") is None
    assert collect.jsonl_fallback_pct(229000, None) is None


def test_jsonl_fallback_1m_model_uses_800k():
    # 229000 / 800000 (effective 1M ceiling) -> 28.6 -> 29; known model unchanged.
    assert collect.jsonl_fallback_pct(229000, "claude-opus-4-8[1m]") == 29


def test_resolve_ctx_pct_rejects_over_100_from_jsonl():
    # The incident shape: no context_pct, no pane line, jsonl tokens with an
    # UNKNOWN model. Pre-fix this returned 143; the guard yields None until
    # the generation row's model is reconciled.
    status = {"session": "inspiration", "context_pct": "",
              "pane_status_line": "", "jsonl_tokens": 229000,
              "resolved_model": "unknown"}
    assert collect.resolve_ctx_pct(status) is None


def test_resolve_ctx_pct_rejects_pane_over_100():
    # A pane line carrying a bogus >100 integer-percent must not pass through;
    # the guarded resolver falls to the next source (here: a sane jsonl).
    status = {"session": "seat-x", "context_pct": "",
              "pane_status_line": "██████░░░░ 143%",
              "jsonl_tokens": 229000,
              "resolved_model": "claude-opus-4-8[1m]"}
    assert collect.resolve_ctx_pct(status) == 29


def test_resolve_ctx_pct_keeps_valid_context_pct():
    # Unchanged path: a valid shared-detector context_pct wins untouched.
    status = {"session": "seat-y", "context_pct": "36%",
              "pane_status_line": "██████░░░░ 99%",
              "jsonl_tokens": 229000,
              "resolved_model": "claude-opus-4-8[1m]"}
    assert collect.resolve_ctx_pct(status) == 36
